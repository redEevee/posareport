"""Commerce reporting MVP server."""

import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import re
from urllib.parse import parse_qs, urlparse

try:
    from app.connectors.cafe24 import Cafe24Client, load_local_environment, sync_last_30_days
    from app.store import get_json, set_json
except ModuleNotFoundError:
    from connectors.cafe24 import Cafe24Client, load_local_environment, sync_last_30_days
    from store import get_json, set_json


ROOT = Path(__file__).resolve().parent.parent
EVENTS_FILE = ROOT / "data" / "events.jsonl"
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


def valid_mall_id(value):
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,49}", (value or "").lower()))


def configured_mall_id():
    return os.environ.get("CAFE24_MALL_ID", "").lower()


def connected_malls():
    malls = get_json("cafe24_malls", [])
    default = configured_mall_id()
    if default and default not in malls:
        malls.append(default)
    return sorted(set(malls))


def add_connected_mall(mall_id):
    malls = connected_malls()
    if mall_id not in malls:
        malls.append(mall_id)
        set_json("cafe24_malls", sorted(malls))


def create_oauth_state(mall_id):
    """Create a signed, short-lived OAuth state that survives a Render restart."""
    issued_at = int(datetime.now(timezone.utc).timestamp())
    payload = f"{mall_id}:{issued_at}".encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signing_key = (os.environ.get("OAUTH_STATE_SECRET") or os.environ.get("CAFE24_CLIENT_SECRET") or "").encode("utf-8")
    if not signing_key:
        raise RuntimeError("카페24 앱 환경설정이 완료되지 않았습니다.")
    signature = hmac.new(signing_key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def consume_oauth_state(state):
    if not state or "." not in state:
        return None
    try:
        encoded, signature = state.rsplit(".", 1)
        signing_key = (os.environ.get("OAUTH_STATE_SECRET") or os.environ.get("CAFE24_CLIENT_SECRET") or "").encode("utf-8")
        expected = hmac.new(signing_key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not signing_key or not hmac.compare_digest(signature, expected):
            return None
        padded = encoded + "=" * (-len(encoded) % 4)
        mall_id, issued_at = base64.urlsafe_b64decode(padded).decode("utf-8").rsplit(":", 1)
        created_at = datetime.fromtimestamp(int(issued_at), timezone.utc)
    except (ValueError, UnicodeDecodeError):
        return None
    return mall_id if valid_mall_id(mall_id) and created_at > datetime.now(timezone.utc) - timedelta(minutes=15) else None


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
            mall_id = parse_qs(parsed.query).get("mall_id", [configured_mall_id()])[0].lower()
            if not valid_mall_id(mall_id):
                self.send_json({"error": "카페24 쇼핑몰 ID를 영문 소문자·숫자로 입력해 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            state = create_oauth_state(mall_id)
            try:
                self.redirect(Cafe24Client(mall_id).authorization_url(state))
            except RuntimeError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/auth/cafe24/callback":
            query = parse_qs(parsed.query)
            state, code = query.get("state", [None])[0], query.get("code", [None])[0]
            mall_id = consume_oauth_state(state)
            if not mall_id or not code:
                self.send_json({"error": "인증 요청이 만료되었거나 직접 열린 주소입니다. 리포트 첫 화면에서 쇼핑몰 ID를 입력한 뒤 ‘이 쇼핑몰 연결’을 다시 눌러 주세요."}, HTTPStatus.BAD_REQUEST)
                return
            OAUTH_STATES.remove(state)
            try:
                Cafe24Client(mall_id).exchange_code(code)
                add_connected_mall(mall_id)
                sync_last_30_days(mall_id)
                self.redirect(f"/?connected=cafe24&mall_id={mall_id}")
            except Exception as error:
                self.send_json({"error": "카페24 연결에 실패했습니다.", "detail": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        if parsed.path == "/api/report":
            mall_id = parse_qs(parsed.query).get("mall_id", [configured_mall_id()])[0].lower()
            report = get_json(f"report:{mall_id}")
            if not report and mall_id == configured_mall_id():
                report = get_json("report")
            self.send_json(report or sample_report())
            return
        if parsed.path == "/api/status":
            mall_id = parse_qs(parsed.query).get("mall_id", [configured_mall_id()])[0].lower()
            token = get_json(f"cafe24_token:{mall_id}")
            report = get_json(f"report:{mall_id}")
            if mall_id == configured_mall_id():
                token = token or get_json("cafe24_token")
                report = report or get_json("report")
            self.send_json({"mall_id": mall_id, "cafe24_connected": bool(token), "has_real_report": bool(report), "malls": connected_malls()})
            return
        if parsed.path == "/api/malls":
            self.send_json({"malls": connected_malls(), "default_mall_id": configured_mall_id()})
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
                mall_id = parse_qs(parsed.query).get("mall_id", [configured_mall_id()])[0].lower()
                if not valid_mall_id(mall_id):
                    raise ValueError("올바른 쇼핑몰 ID가 아닙니다.")
                self.send_json(sync_last_30_days(mall_id))
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
