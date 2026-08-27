"""Local MVP server. Keeps secrets server-side and exposes report-only APIs."""

from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlparse

try:
    from connectors.cafe24 import Cafe24Client, load_local_environment
except ModuleNotFoundError:
    from app.connectors.cafe24 import Cafe24Client, load_local_environment

ROOT = Path(__file__).resolve().parent.parent
EVENTS_FILE = ROOT / "data" / "events.jsonl"
OAUTH_STATES = set()
load_local_environment()


def allowed_origin(origin):
    permitted = os.environ.get("EVENT_ALLOWED_ORIGINS", "").split(",")
    return origin if origin and origin in {item.strip() for item in permitted} else None

SAMPLE_ROWS = [
    {"date": "2026-08-25", "channel": "Google Ads", "spend": 185000, "orders": 18, "revenue": 1260000},
    {"date": "2026-08-25", "channel": "Meta Ads", "spend": 122000, "orders": 11, "revenue": 715000},
    {"date": "2026-08-26", "channel": "Google Ads", "spend": 212000, "orders": 20, "revenue": 1490000},
    {"date": "2026-08-26", "channel": "Meta Ads", "spend": 138000, "orders": 12, "revenue": 756000},
    {"date": "2026-08-27", "channel": "Google Ads", "spend": 164000, "orders": 16, "revenue": 1040000},
    {"date": "2026-08-27", "channel": "Meta Ads", "spend": 109000, "orders": 10, "revenue": 690000},
]


def summary(rows):
    spend = sum(row["spend"] for row in rows)
    revenue = sum(row["revenue"] for row in rows)
    return {
        "spend": spend,
        "orders": sum(row["orders"] for row in rows),
        "revenue": revenue,
        "roas": round(revenue / spend, 2) if spend else 0,
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

    def do_OPTIONS(self):
        origin = allowed_origin(self.headers.get("Origin"))
        if not origin:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/auth/cafe24/connect":
            state = secrets.token_urlsafe(32)
            OAUTH_STATES.add(state)
            try:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", Cafe24Client().authorization_url(state))
                self.end_headers()
            except RuntimeError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/auth/cafe24/callback":
            query = parse_qs(parsed.query)
            state = query.get("state", [None])[0]
            code = query.get("code", [None])[0]
            if state not in OAUTH_STATES or not code:
                self.send_json({"error": "인증 요청을 확인할 수 없습니다."}, HTTPStatus.BAD_REQUEST)
                return
            OAUTH_STATES.remove(state)
            try:
                Cafe24Client().exchange_code(code)
                self.send_json({"connected": True, "message": "카페24 연결이 완료되었습니다."})
            except Exception:
                self.send_json({"error": "토큰 발급에 실패했습니다. 앱 설정을 확인해 주세요."}, HTTPStatus.BAD_GATEWAY)
            return
        if self.path == "/api/report":
            grouped = {}
            for row in SAMPLE_ROWS:
                grouped.setdefault(row["channel"], []).append(row)
            self.send_json({
                "updated_at": date.today().isoformat(),
                "summary": summary(SAMPLE_ROWS),
                "channels": [{"name": name, **summary(rows)} for name, rows in grouped.items()],
                "daily": SAMPLE_ROWS,
                "mode": "sample",
            })
            return
        if self.path == "/health":
            self.send_json({"ok": True})
            return
        if self.path in ("/", "/index.html"):
            file_path = ROOT / "web" / "index.html"
            content = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if self.path == "/tracker.js":
            file_path = ROOT / "web" / "tracker.js"
            content = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path != "/api/events":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            event = json.loads(self.rfile.read(length))
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
    port = int(os.environ.get("PORT", "8787"))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"Commerce report dashboard: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
