"""Commerce reporting MVP server."""

from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlparse

from app.connectors.cafe24 import Cafe24Client, load_local_environment, sync_last_30_days
from app.store import get_json


ROOT = Path(__file__).resolve().parent.parent
EVENTS_FILE = ROOT / "data" / "events.jsonl"
OAUTH_STATES = set()
load_local_environment()

SAMPLE_ROWS = [
    {"date": "2026-08-25", "channel": "Google Ads", "spend": 185000, "orders": 18, "revenue": 1260000},
    {"date": "2026-08-25", "channel": "Meta Ads", "spend": 122000, "orders": 11, "revenue": 715000},
    {"date": "2026-08-26", "channel": "Google Ads", "spend": 212000, "orders": 20, "revenue": 1490000},
    {"date": "2026-08-26", "channel": "Meta Ads", "spend": 138000, "orders": 12, "revenue": 756000},
    {"date": "2026-08-27", "channel": "Google Ads", "spend": 164000, "orders": 16, "revenue": 1040000},
    {"date": "2026-08-27", "channel": "Meta Ads", "spend": 109000, "orders": 10, "revenue": 690000},
]


def allowed_origin(origin):
    permitted = os.environ.get("EVENT_ALLOWED_ORIGINS", "").split(",")
    return origin if origin and origin in {item.strip() for item in permitted} else None


def sample_report():
    grouped = {}
    for row in SAMPLE_ROWS:
        grouped.setdefault(row["channel"], []).append(row)
    spend, revenue = sum(row["spend"] for row in SAMPLE_ROWS), sum(row["revenue"] for row in SAMPLE_ROWS)
    return {
        "updated_at": date.today().isoformat(),
        "summary": {"spend": spend, "orders": sum(row["orders"] for row in SAMPLE_ROWS), "revenue": revenue, "roas": round(revenue / spend, 2)},
        "channels": [{"name": name, "spend": sum(row["spend"] for row in rows), "orders": sum(row["orders"] for row in rows), "revenue": sum(row["revenue"] for row in rows), "roas": round(sum(row["revenue"] for row in rows) / sum(row["spend"] for row in rows), 2)} for name, rows in grouped.items()],
        "daily": SAMPLE_ROWS, "mode": "sample", "note": "카페24를 연결하면 실제 주문·매출 데이터로 전환됩니다.",
    }


class Handler(BaseHTTPRequestHandler):
    def send_json(self, value, status=HTTPStatus.OK):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        origin = allowed_origin(self.headers.get("Origin"))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, location):
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    def do_OPTIONS(self):
        origin = allowed_origin(self.headers.get("Origin"))
        if not origin:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Report-Admin-Key")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/auth/cafe24/connect":
            state = secrets.token_urlsafe(32)
            OAUTH_STATES.add(state)
            try:
                self.redirect(Cafe24Client().authorization_url(state))
            except RuntimeError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/auth/cafe24/callback":
            query = parse_qs(parsed.query)
            state, code = query.get("state", [None])[0], query.get("code", [None])[0]
            if state not in OAUTH_STATES or not code:
                self.send_json({"error": "인증 요청을 확인할 수 없습니다."}, HTTPStatus.BAD_REQUEST)
                return
            OAUTH_STATES.remove(state)
            try:
                Cafe24Client().exchange_code(code)
                sync_last_30_days()
                self.redirect("/?connected=cafe24")
            except Exception as error:
                self.send_json({"error": "카페24 연결에 실패했습니다.", "detail": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        if parsed.path == "/api/report":
            self.send_json(get_json("report", sample_report()))
            return
        if parsed.path == "/api/status":
            self.send_json({"cafe24_connected": bool(get_json("cafe24_token")), "has_real_report": bool(get_json("report"))})
            return
        if parsed.path == "/health":
            self.send_json({"ok": True})
            return
        if parsed.path in ("/", "/index.html", "/tracker.js"):
            filename = "tracker.js" if parsed.path == "/tracker.js" else "index.html"
            content = (ROOT / "web" / filename).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/javascript; charset=utf-8" if filename.endswith(".js") else "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/sync/cafe24":
            expected_key = os.environ.get("REPORT_ADMIN_KEY")
            if not expected_key or not secrets.compare_digest(self.headers.get("X-Report-Admin-Key", ""), expected_key):
                self.send_json({"error": "관리자 키가 필요합니다."}, HTTPStatus.UNAUTHORIZED)
                return
            try:
                self.send_json(sync_last_30_days())
            except Exception as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        if parsed.path != "/api/events":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            event = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if event.get("type") not in {"page_view", "view_item", "add_to_cart", "purchase"}:
                raise ValueError("unsupported event type")
            event["received_at"] = date.today().isoformat()
            EVENTS_FILE.parent.mkdir(exist_ok=True)
            with EVENTS_FILE.open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.send_json({"accepted": True}, HTTPStatus.ACCEPTED)
        except (json.JSONDecodeError, ValueError):
            self.send_json({"error": "올바른 이벤트 형식이 아닙니다."}, HTTPStatus.BAD_REQUEST)

    def log_message(self, *_):
        return


if __name__ == "__main__":
    port, host = int(os.environ.get("PORT", "8787")), os.environ.get("HOST", "127.0.0.1")
    print(f"Commerce report dashboard: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
