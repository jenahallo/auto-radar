import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collector as c
import server as s


class Filters(unittest.TestCase):
    def setUp(self):
        self.item = dict(price=220000, km=130000, year=2015, fuel="Benzín",
                         gearbox="Automatická", published_at=c.now().isoformat())

    def test_inclusive_boundaries(self):
        self.assertTrue(c.match(self.item))
        for key, value in (("price", 220001), ("km", 130001), ("year", 2014),
                           ("fuel", "Nafta"), ("gearbox", "Manuální"), ("price", 0),
                           ("km", None), ("year", None), ("published_at", None)):
            with self.subTest(key=key, value=value):
                self.assertFalse(c.match(dict(self.item, **{key: value})))

    def test_czech_time_and_dst(self):
        self.assertEqual(c.iso("2026-10-09T08:00:00").hour, 6)
        self.assertEqual(c.iso("2026-12-09T08:00:00").hour, 7)
        self.assertEqual(c.iso("2026-10-09T06:00:00Z").hour, 6)
        self.assertIsNone(c.iso("bad date"))

    def test_scheduler_crosses_dst(self):
        current = datetime(2026, 10, 24, 9, tzinfo=c.PRAGUE)
        target = s.next_run(current)
        self.assertEqual(target.hour, 8)
        self.assertEqual(target.day, 25)
        self.assertEqual(target.astimezone(timezone.utc).hour, 7)

    def test_dates_not_replaced_by_bumping(self):
        item = c.sauto_item(dict(id=1, name="Auto", create_date="2025-01-01T08:00:00",
                                edit_date="2026-10-09T08:00:00", price=100000,
                                manufacturing_date="2017-01-01", tachometer=100000,
                                fuel_cb={"name": "Benzín"}, gearbox_cb={"name": "Automatická"},
                                manufacturer_cb={"seo_name": "opel"}, model_cb={"seo_name": "corsa"}))
        self.assertTrue(item['published_at'].startswith("2025-01-01"))
        self.assertNotEqual(item['published_at'], item['updated_at'])


class SbazarParsing(unittest.TestCase):
    def test_astro_ssr(self):
        page = '<astro-island props="{&quot;offer&quot;:[0,{&quot;name&quot;:[0,&quot;Auto&quot;],&quot;images&quot;:[1,[[0,{&quot;url&quot;:[0,&quot;//example.test/a.jpg&quot;]}]]]}]}"></astro-island>'
        self.assertEqual(c.sb_props(page, "offer")["name"], "Auto")
        self.assertEqual(c.sb_props(page, "offer")["images"][0]["url"], "//example.test/a.jpg")
        with self.assertRaises(ValueError): c.sb_props("<html>Changed</html>", "offer")

    def test_labelled_specs(self):
        description = "Stav tachometru: 120 000 km\nRok a měsíc výroby: 01.01.2017\nPalivo: Benzín\nPřevodovka: Automatická\nSTK do: 2028"
        self.assertEqual(c.sb_specs("Opel Corsa", description), dict(year=2017, km=120000, fuel="Benzín", gearbox="Automatická"))

    def test_climate_is_not_gearbox(self):
        parsed = c.sb_specs("Auto 2017", "Najeto 90000 km. Benzín. Automatická klimatizace a automatické svícení.")
        self.assertIsNone(parsed["gearbox"])

    def test_stk_and_warranty_are_not_specs(self):
        parsed = c.sb_specs("Auto automat", "Benzín. STK 2027. Záruka do 130000 km.")
        self.assertIsNone(parsed["year"])
        self.assertIsNone(parsed["km"])

    def test_registration_does_not_confirm_production_year(self):
        parsed = c.sb_specs("Auto automat", "První registrace: 01.01.2015. Najeto 120000 km. Benzín.")
        self.assertIsNone(parsed["year"])

    def test_unsuitable_ads_not_in_unknown_candidates(self):
        for item in [dict(fuel="Nafta"), dict(gearbox="Manuální"), dict(km=150000), dict(year=2014), dict(price=250000)]:
            self.assertFalse(c.potentially_matches(item))

    def test_other_fuels_are_excluded(self):
        for fuel in ["Nafta", "Elektro", "Benzín + LPG", "Benzín hybrid", "Benzín CNG"]:
            with self.subTest(fuel=fuel):
                self.assertNotEqual(c.sb_specs("Auto automat", "Palivo: " + fuel)["fuel"], "Benzín")

    def test_thousands_and_manual(self):
        parsed = c.sb_specs("Auto 2015 DSG 120 tis km benzín", "Převodovka: Manuální")
        self.assertEqual(parsed["km"], 120000)
        self.assertEqual(parsed["gearbox"], "Manuální")


class FailureHandling(unittest.TestCase):
    def test_source_failure_retains_stale_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "latest.json"
            previous = dict(id="sauto:1", source="Sauto", title="Auto", published_at=c.now().isoformat())
            c.save(dict(items=[previous], unverified=[]), path)
            with patch.object(c, "sauto", side_effect=ValueError("Source unavailable")), patch.object(c, "sbazar", return_value=([], [], dict(state="ok", scanned=0))):
                with self.assertLogs(c.LOG, level="ERROR"):
                    result = c.collect(path)
            self.assertEqual(result["sources"]["Sauto"]["state"], "error")
            self.assertTrue(result["items"][0]["stale"])
            self.assertEqual(c.load(path)["sources"]["Sbazar"]["state"], "ok")

    def test_sauto_pagination_and_local_filter(self):
        bad = dict(id=1, create_date=c.now().isoformat(), price=999999)
        good = dict(id=2, create_date=c.now().isoformat(), price=220000, tachometer=130000,
                    manufacturing_date="2015-01-01", fuel_cb={"name":"Benzín"}, gearbox_cb={"name":"Automatická"})
        page = json.dumps(dict(results=[bad,good],pagination=dict(total=2)))
        with patch.object(c, "get", return_value=page):
            items, state = c.sauto(c.now() - timedelta(days=30))
        self.assertEqual(len(items),1)
        self.assertEqual(state["state"], "ok")


class WebAPI(unittest.TestCase):
    def setUp(self):
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), s.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()

    def test_refresh_requires_same_origin_header(self):
        with patch.dict('os.environ', {"CAR_PASSWORD": ""}):
            with self.assertRaises(HTTPError) as ctx:
                urlopen(Request(self.base + "/api/refresh", method="POST"))
            self.assertEqual(ctx.exception.code,403)
            with patch.object(s, "start_scan", return_value=True):
                r=urlopen(Request(self.base + "/api/refresh", method="POST", headers={"X-Car-Watch":"refresh"}))
                self.assertEqual(r.status,202)
                self.assertTrue(json.load(r)["started"])

    def test_busy_refresh_and_foreign_origin(self):
        with patch.dict('os.environ', {"CAR_PASSWORD": ""}), patch.object(s, "start_scan", return_value=False):
            for headers, code in [({"X-Car-Watch":"refresh"},409), ({"X-Car-Watch":"refresh","Origin":"https://foreign.test"},403)]:
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(Request(self.base + "/api/refresh", method="POST", headers=headers))
                self.assertEqual(ctx.exception.code,code)

    def test_optional_authentication(self):
        with patch.dict('os.environ', {"CAR_PASSWORD":"test-only-password"}):
            with self.assertRaises(HTTPError) as ctx: urlopen(self.base + "/api/data")
            self.assertEqual(ctx.exception.code,401)
            self.assertEqual(urlopen(self.base + "/health").status,200)


if __name__ == "__main__":
    unittest.main()
