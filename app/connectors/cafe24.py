"""Server-side Cafe24 OAuth and order-report integration."""

import base64
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from app.store import get_json, set_json
except ModuleNotFoundError:
    from store import get_json, set_json


def load_local_environment():
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value)


class Cafe24Client:
    def __init__(self):
        load_local_environment()
        self.mall_id = os.environ.get("CAFE24_MALL_ID")
        self.client_id = os.environ.get("CAFE24_CLIENT_ID")
        self.client_secret = os.environ.get("CAFE24_CLIENT_SECRET")
        self.redirect_uri = os.environ.get("CAFE24_REDIRECT_URI")

    def is_configured(self):
        return bool(self.mall_id and self.client_id and self.client_secret and self.redirect_uri)

    def authorization_url(self, state):
        if not self.is_configured():
            raise RuntimeError("카페24 앱 환경설정이 완료되지 않았습니다.")
        return "https://{}.cafe24api.com/api/v2/oauth/authorize?{}".format(
            self.mall_id,
            urlencode({"response_type": "code", "client_id": self.client_id, "state": state,
                       "redirect_uri": self.redirect_uri, "scope": "mall.read_order"}),
        )

    def _basic_authorization(self):
        encoded = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        return f"Basic {encoded}"

    def _token_request(self, payload):
        request = Request(
            f"https://{self.mall_id}.cafe24api.com/api/v2/oauth/token",
            data=urlencode(payload).encode(),
            headers={"Authorization": self._basic_authorization(), "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            token = json.load(response)
        set_json("cafe24_token", token)
        return token

    def exchange_code(self, code):
        return self._token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri})

    def _access_token(self):
        token = get_json("cafe24_token")
        if not token:
            raise RuntimeError("카페24 인증을 먼저 완료해 주세요.")
        try:
            expires_at = datetime.fromisoformat(token.get("expires_at", "").replace("Z", "+00:00"))
            expires_at = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        except ValueError:
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        if expires_at <= datetime.now(timezone.utc) + timedelta(minutes=5):
            refresh_token = token.get("refresh_token")
            if not refresh_token:
                raise RuntimeError("카페24 인증이 만료되었습니다. 다시 연결해 주세요.")
            token = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
        return token["access_token"]

    def fetch_orders(self, start_date, end_date):
        """Fetch orders without requesting buyer or recipient information."""
        query = urlencode({
            "shop_no": 1, "start_date": f"{start_date} 00:00:00", "end_date": f"{end_date} 23:59:59",
            "date_type": "order_date", "limit": 100,
            "fields": "order_id,order_date,payment_amount,currency,order_status",
        })
        request = Request(
            f"https://{self.mall_id}.cafe24api.com/api/v2/admin/orders?{query}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        try:
            with urlopen(request, timeout=25) as response:
                payload = json.load(response)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"카페24 주문 조회에 실패했습니다 ({error.code}). {detail}") from error
        return payload.get("orders", [])


def report_from_orders(orders):
    grouped = {}
    for order in orders:
        day_key = str(order.get("order_date", ""))[:10]
        if not day_key:
            continue
        try:
            amount = int(float(order.get("payment_amount", 0)))
        except (TypeError, ValueError):
            amount = 0
        day = grouped.setdefault(day_key, {"date": day_key, "orders": 0, "revenue": 0})
        day["orders"] += 1
        day["revenue"] += amount
    daily = [grouped[key] for key in sorted(grouped)]
    return {
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "summary": {"spend": 0, "orders": sum(row["orders"] for row in daily), "revenue": sum(row["revenue"] for row in daily), "roas": 0},
        "channels": [], "daily": daily, "mode": "cafe24",
        "note": "광고 매체를 연결하면 광고비와 ROAS가 함께 표시됩니다.",
    }


def sync_last_30_days():
    end_date = date.today()
    report = report_from_orders(Cafe24Client().fetch_orders(end_date - timedelta(days=29), end_date))
    set_json("report", report)
    return report
