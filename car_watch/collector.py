"""Sauto + Sbazar collector. Python 3.12+, standard library only."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

LOG = logging.getLogger(__name__)
PRAGUE = ZoneInfo("Europe/Prague")
DEFAULT_DATA = Path(__file__).parent / "data" / "latest.json"
FILTERS = dict(max_price=220000, max_km=130000, min_year=2015,
               fuel="Benzín", gearbox="Automatická", country="ČR")
QUERIES = ("automat", "dsg", "dct", "cvt", "edc", "eat6", "eat8",
           "tronic", "powershift")


def now():
    return datetime.now(timezone.utc)


def iso(value):
    """Seznam's naive timestamps represent Czech local time, not UTC."""
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=PRAGUE)).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def plain(value):
    return "".join(c for c in unicodedata.normalize("NFKD", str(value or "").lower())
                   if not unicodedata.combining(c)).replace("\xa0", " ")


def number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (float, int)):
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\d[\d\s.]*", value.strip()):
        return int(re.sub(r"\D", "", value))
    return None


def get(url):
    # Sbazar offers this public flag in its own autologin return URL. Reading
    # anonymous listings must not enter the optional Seznam autologin roundtrip.
    if url.startswith("https://www.sbazar.cz/"):
        url += ("&" if "?" in url else "?") + "noredirect=1"
    LOG.info("Read %s", url)
    request = Request(url, headers={"User-Agent": "CarWatch/1.0 (personal car search)",
                                    "Accept-Language": "cs"})
    # Retry transient server/network errors, never access denials/rate limits.
    for attempt in range(2):
        try:
            with urlopen(request, timeout=25) as response:
                return response.read(15_000_000).decode("utf-8")
        except Exception as exc:
            code = getattr(exc, "code", None)
            if (code is not None and code < 500) or attempt:
                raise
            time.sleep(1)


def astro_decode(value):
    """Decode Astro's serialized props (0=value, 1=array)."""
    if isinstance(value, dict):
        return {k: astro_decode(v) for k, v in value.items()}
    if isinstance(value, list):
        if value and value[0] == 0:
            return astro_decode(value[1]) if len(value) > 1 else None
        if value and value[0] == 1:
            return [astro_decode(v) for v in value[1]]
        raise ValueError("Unsupported Astro serialization tag")
    return value


class Islands(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = []

    def handle_starttag(self, tag, attrs):
        if tag == "astro-island":
            props = dict(attrs).get("props")
            if props:
                self.values.append(props)


def sb_props(text, key):
    parser = Islands()
    parser.feed(text)
    for props in parser.values:
        # Select only the relevant island, before decoding date/Map tags elsewhere.
        raw = json.loads(props)
        if key in raw:
            return astro_decode(raw)[key]
    raise ValueError(f"Sbazar changed its page format: missing {key}")


def match(item):
    return (item.get("price") is not None and 0 < item["price"] <= FILTERS["max_price"]
            and item.get("km") is not None and 0 <= item["km"] <= FILTERS["max_km"]
            and item.get("year") is not None and FILTERS["min_year"] <= item["year"] <= now().year + 1
            and plain(item.get("fuel")) == "benzin"
            and plain(item.get("gearbox")) == "automaticka"
            and bool(iso(item.get("published_at"))))


def potentially_matches(item):
    """Only incomplete candidates; definitely unsuitable ads stay excluded."""
    return ((item.get("price") is None or 0 < item["price"] <= 220000)
            and (item.get("km") is None or 0 <= item["km"] <= 130000)
            and (item.get("year") is None or 2015 <= item["year"] <= now().year + 1)
            and item.get("fuel") in (None, "Benzín")
            and item.get("gearbox") in (None, "Automatická"))


def common(source, row):
    camel = source == "Sbazar"
    created = row.get("createDate" if camel else "create_date")
    edited = row.get("editDate" if camel else "edit_date")
    loc = row.get("locality") or {}
    imgs = row.get("images") or []
    image = imgs[0].get("url", "") if imgs else ""
    if image.startswith("//"):
        image = "https:" + image
    if not image.startswith("https://"):
        image = ""
    return dict(id=f"{source.lower()}:{row['id']}", source=source,
                title=row.get("name", ""), price=number(row.get("price")),
                image=image, location=loc.get("municipality") or loc.get("district") or loc.get("region") or "",
                published_at=iso(created).isoformat() if iso(created) else None,
                updated_at=iso(edited).isoformat() if iso(edited) else None,
                first_seen_at=now().isoformat())


def sauto_item(row):
    item = common("Sauto", row)
    d = row.get("manufacturing_date") or ""
    item.update(year=int(d[:4]) if re.match(r"^\d{4}-", d) else None,
                km=number(row.get("tachometer")),
                fuel=(row.get("fuel_cb") or {}).get("name"),
                gearbox=(row.get("gearbox_cb") or {}).get("name"))
    make = row.get("manufacturer_cb", {}).get("seo_name", "")
    model = row.get("model_cb", {}).get("seo_name", "")
    item["url"] = f"https://www.sauto.cz/osobni/detail/{quote(make)}/{quote(model)}/{row['id']}"
    return item


def sauto(cutoff, limit_pages=60):
    items, scanned, complete = [], 0, False
    for page in range(limit_pages):
        params = dict(category_id=838, price_to=220000, fuel_seo="benzin",
                      gearbox_seo="automaticka", vehicle_age_from=2015,
                      tachometer_to=130000, condition_seo="ojete,nove,predvadeci",
                      limit=100, offset=page * 100)
        data = json.loads(get("https://www.sauto.cz/api/v1/items/search?" + urlencode(params)))
        if "results" not in data or "pagination" not in data:
            raise ValueError("Sauto changed its response format")
        for row in data["results"]:
            scanned += 1
            item = sauto_item(row)
            if match(item) and iso(item["published_at"]) >= cutoff:
                items.append(item)
        if not data["results"] or (page + 1) * 100 >= data["pagination"]["total"]:
            complete = True
            break
        time.sleep(0.3)
    return items, dict(state="ok" if complete else "partial", scanned=scanned,
                       message="" if complete else "Dosažen limit stránek; výsledky nejsou úplné.")


def sb_specs(title, description):
    text = plain(description)
    heading = plain(title)
    year = None
    # Prefer explicit production year; an STK date is not a production year.
    production = re.search(r"(?:rok(?: a mesic)? vyroby|rok vyroby|r\.?\s*v\.?|rocnik)\s*[:=-]?\s*(?:\d{1,2}[./]\d{1,2}[./])?\s*((?:19|20)\d{2})\b", text)
    title_years = re.findall(r"\b((?:19|20)\d{2})\b", heading)
    if production:
        year = int(production[1])
    elif len(set(title_years)) == 1:
        year = int(title_years[0])
    km = None
    mileage = re.search(r"(?:stav tachometru|najeto|najezd|naj\.)\s*[:=-]?\s*(\d[\d .]*?)\s*(?:tis\.?\s*)?km\b", text)
    if mileage:
        km = number(mileage[1])
        if re.search(r"\btis", mileage[0]):
            km *= 1000
    else:
        # Read the title only; warranty or service intervals in body must not qualify.
        m = re.search(r"\b(\d[\d .]*?)\s*(tis\.?\s*)?km\b", heading)
        if m:
            km = number(m[1])
            if m[2]:
                km *= 1000
    fuel_line = re.search(r"palivo\s*:\s*([^\n]+)", text)
    fuel_text = fuel_line[1] if fuel_line else heading + " " + text
    fuel = None
    if re.search(r"\b(?:nafta|diesel|tdi|hdi|dci|crdi)\b", fuel_text):
        fuel = "Nafta"
    elif re.search(r"\b(?:elektro|elektricky)\b", fuel_text):
        fuel = "Elektro"
    elif re.search(r"\b(?:hybrid|lpg|cng)\b", fuel_text):
        fuel = "Jiné palivo"
    if re.search(r"\b(?:benzin(?:ovy)?|benz[ií]n|petrol|gasoline)\b", fuel_text):
        if not re.search(r"\b(?:nafta|diesel|hybrid|lpg|cng|elektro)\b", fuel_text):
            fuel = "Benzín"
    gearbox_line = re.search(r"prevodovka\s*:\s*([^\n]+)", text)
    gearbox_text = gearbox_line[1] if gearbox_line else heading + " " + text
    # Automatic air conditioning, lights or windows are not transmission evidence.
    automatic = bool(re.search(r"\b(?:automat|automaticka prevodovka|automatickou prevodovkou|dsg|dct|cvt|edc|eat[68]|tiptronic|s[ -]tronic|powershift|easytronic)\b", gearbox_text))
    if gearbox_line and "automaticka" in gearbox_text:
        automatic = True
    if re.search(r"\bmanual(?:ni)?\b", gearbox_text):
        automatic = False
    manual = bool(re.search(r"\bmanual(?:ni)?\b", gearbox_text))
    return dict(year=year, km=km, fuel=fuel, gearbox="Automatická" if automatic else "Manuální" if manual else None)


def sb_detail(row, previous):
    url = "https://www.sbazar.cz/inzerat/" + quote(row["seoName"])
    item = common("Sbazar", row)
    item["url"] = url
    cached = previous.get(item["id"])
    if cached and cached.get("updated_at") == item["updated_at"] and cached.get("parser_version") == 2:
        return cached
    offer = sb_props(get(url), "offer")
    item = common("Sbazar", offer)
    item["url"] = url
    item.update(sb_specs(item["title"], offer.get("description") or ""))
    item["parser_version"] = 2
    return item


def sbazar(cutoff, previous, limit_pages=60, max_details=1500):
    candidates = {}
    partial = False
    pages = 0
    for query in QUERIES:
        exhausted = False
        for page in range(1, limit_pages + 1):
            base = f"https://www.sbazar.cz/hledej/{quote(query)}/170-osobni-auta"
            url = base if page == 1 else f"{base}/cela-cr/cena-neomezena/nejnovejsi/{page}"
            data = sb_props(get(url), "ssrOffers")
            pages += 1
            rows = data["results"]
            for row in rows:
                published = iso(row.get("createDate"))
                price = number(row.get("price"))
                if published and published >= cutoff and price and 0 < price <= 220000:
                    candidates[str(row["id"])] = row
            total = data["pagination"]["total"]
            # The page's newest order includes bumping. Stop only on sortingDate,
            # never merely because old createDate values occur on one page.
            sort_dates = [iso(r.get("sortingDate")) for r in rows if not r.get("topped")]
            old_tail = bool(sort_dates) and all(d and d < cutoff for d in sort_dates)
            if not rows or page * data["pagination"]["limit"] >= total or old_tail:
                exhausted = True
                break
            time.sleep(0.35)
        partial |= not exhausted
    selected = sorted(candidates.values(), key=lambda r: r.get("createDate", ""), reverse=True)
    partial |= len(selected) > max_details
    items, unverified, errors = [], [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(sb_detail, row, previous): row for row in selected[:max_details]}
        for job in as_completed(jobs):
            try:
                item = job.result()
                if match(item):
                    items.append(item)
                elif potentially_matches(item) and any(item.get(k) is None for k in ("km", "year", "fuel", "gearbox")):
                    unverified.append(item)
            except Exception as exc:
                errors.append(f"{jobs[job]['id']}: {exc}")
                LOG.warning("Sbazar detail failed: %s", errors[-1])
    partial |= bool(errors)
    return items, unverified, dict(state="partial" if partial else "ok", scanned=len(candidates),
                                   pages=pages, unverified=len(unverified), failed_details=len(errors),
                                   message="Část nabídek nebyla prověřena; výsledky nejsou úplné." if partial else "")


def load(path=DEFAULT_DATA):
    if not path.exists():
        return dict(items=[], unverified=[], sources={}, filters=FILTERS, generated_at=None)
    return json.loads(path.read_text(encoding="utf-8"))


def save(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def collect(path=DEFAULT_DATA, limit_pages=60, max_details=1500):
    started = now()
    cutoff = started - timedelta(days=30)
    old = load(path)
    previous = {i["id"]: i for i in old.get("items", []) + old.get("unverified", [])}
    items, unverified, sources = [], [], {}
    for source in ("Sauto", "Sbazar"):
        try:
            if source == "Sauto":
                found, status = sauto(cutoff, limit_pages)
                unknown = []
            else:
                found, unknown, status = sbazar(cutoff, previous, limit_pages, max_details)
            status["checked_at"] = now().isoformat()
            for item in found + unknown:
                item["first_seen_at"] = previous.get(item["id"], {}).get("first_seen_at", item["first_seen_at"])
                item["last_seen_at"] = status["checked_at"]
                item["stale"] = False
            # Keep previous records when the scan is partial, explicitly stale.
            if status["state"] == "partial":
                seen = {i["id"] for i in found + unknown}
                found += [dict(i, stale=True) for i in old.get("items", [])
                          if i["source"] == source and i["id"] not in seen and iso(i.get("published_at")) and iso(i["published_at"]) >= cutoff]
        except Exception as exc:
            LOG.exception("%s failed", source)
            status = dict(state="error", checked_at=now().isoformat(), message=str(exc), scanned=0)
            found = [dict(i, stale=True) for i in old.get("items", []) if i["source"] == source
                     and iso(i.get("published_at")) and iso(i["published_at"]) >= cutoff]
            unknown = [dict(i, stale=True) for i in old.get("unverified", []) if i["source"] == source
                       and iso(i.get("published_at")) and iso(i["published_at"]) >= cutoff]
        sources[source] = status
        items.extend(found)
        unverified.extend(unknown)
    data = dict(version=1, filters=FILTERS, generated_at=now().isoformat(), started_at=started.isoformat(),
                lookback_days=30, sources=sources,
                items=sorted(items, key=lambda i: i["published_at"], reverse=True),
                unverified=sorted(unverified, key=lambda i: i.get("published_at") or "", reverse=True))
    save(data, path)
    return data


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--max-details", type=int, default=1500)
    args = parser.parse_args()
    result = collect(args.data, args.max_pages, args.max_details)
    print(json.dumps(dict(count=len(result["items"]), sources=result["sources"]), ensure_ascii=False, indent=2))
    if any(s["state"] == "error" for s in result["sources"].values()):
        raise SystemExit(1)
    if any(s["state"] == "partial" for s in result["sources"].values()):
        raise SystemExit(2)
