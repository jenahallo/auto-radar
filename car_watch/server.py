"""Local/Docker web dashboard with manual refresh and Prague 08:00 scheduler."""
import base64
from datetime import datetime, time as daytime, timedelta
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

from collector import DEFAULT_DATA, PRAGUE, collect, load

ROOT = Path(__file__).parent
DATA = Path(os.getenv("CAR_DATA", str(DEFAULT_DATA)))
LOCK = threading.Lock()
STATE = dict(running=False, started_at=None, error=None)
STATE_LOCK = threading.Lock()


def start_scan():
    if not LOCK.acquire(blocking=False):
        return False
    with STATE_LOCK:
        STATE.update(running=True, started_at=datetime.now(PRAGUE).isoformat(), error=None)

    def run():
        try:
            collect(DATA, int(os.getenv("CAR_MAX_PAGES", "60")), int(os.getenv("CAR_MAX_DETAILS", "1500")))
        except Exception as exc:
            logging.exception("Scan failed")
            with STATE_LOCK:
                STATE["error"] = str(exc)
        finally:
            with STATE_LOCK:
                STATE["running"] = False
            LOCK.release()

    threading.Thread(target=run, daemon=True).start()
    return True


def next_run(current=None):
    current = current or datetime.now(PRAGUE)
    target = datetime.combine(current.date(), daytime(8), PRAGUE)
    if target <= current:
        target = datetime.combine(current.date() + timedelta(days=1), daytime(8), PRAGUE)
    return target


def scheduler():
    target = next_run()
    while True:
        remaining = (target - datetime.now(PRAGUE)).total_seconds()
        if remaining <= 0:
            if start_scan():
                target = next_run()
            else:
                threading.Event().wait(15)
        else:
            threading.Event().wait(min(30, remaining))


class Handler(BaseHTTPRequestHandler):
    def authorized(self):
        password = os.getenv("CAR_PASSWORD")
        if not password:
            return True
        expected = "Basic " + base64.b64encode(("carwatch:" + password).encode()).decode()
        if hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Car Watch", charset="UTF-8"')
        self.end_headers()
        return False

    def send(self, data, content_type="application/json; charset=utf-8", status=200):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            return self.send({"status": "ok"})
        if not self.authorized():
            return
        if path == "/":
            self.send((ROOT / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/data":
            self.send(load(DATA))
        elif path == "/api/status":
            with STATE_LOCK:
                status = dict(STATE)
            status["next_run"] = next_run().isoformat() if os.getenv("CAR_SCHEDULE", "true").lower() == "true" else None
            self.send(status)
        else:
            self.send({"error": "Nenalezeno"}, status=404)

    def do_POST(self):
        if not self.authorized():
            return
        if self.path != "/api/refresh":
            return self.send({"error": "Nenalezeno"}, status=404)
        # This header cannot be sent by a cross-origin HTML form. No CORS is enabled.
        if self.headers.get("X-Car-Watch") != "refresh":
            return self.send({"error": "Chybí hlavička požadavku"}, status=403)
        if self.headers.get("Origin"):
            origin = urlsplit(self.headers["Origin"]).netloc
            if origin != self.headers.get("Host"):
                return self.send({"error": "Nepovolený původ požadavku"}, status=403)
        if not start_scan():
            return self.send({"error": "Kontrola již běží"}, status=409)
        self.send({"started": True}, status=202)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if os.getenv("CAR_SCHEDULE", "true").lower() == "true":
        threading.Thread(target=scheduler, daemon=True).start()
    if not DATA.exists() or os.getenv("CAR_SCAN_ON_START", "false").lower() == "true":
        start_scan()
    host, port = os.getenv("CAR_HOST", "127.0.0.1"), int(os.getenv("PORT", "8080"))
    logging.info("Dashboard: http://%s:%s", host, port)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
