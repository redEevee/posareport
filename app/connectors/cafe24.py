"""Cafe24 integration boundary.

Implement OAuth and order retrieval here. Never expose the access token to web/index.html.
"""

import base64
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
TOKEN_FILE = ROOT / "data" / "cafe24-token.json"


def load_local_environment():
    """Load local development values without replacing deployment environment values."""
    env_file = ROOT / ".env"
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
        self.access_token = os.environ.get("CAFE24_ACCESS_TOKEN")
        self.redirect_uri = os.environ.get("CAFE24_REDIRECT_URI")

    def is_configured(self):
        return bool(self.mall_id and self.client_id and self.client_secret and self.redirect_uri)

    def authorization_url(self, state):
        if not self.is_configured():
            raise RuntimeError("Cafe24 app settings are not configured")
        return "https://{}.cafe24api.com/api/v2/oauth/authorize?{}".format(
            self.mall_id,
            urlencode({
                "response_type": "code",
                "client_id": self.client_id,
                "state": state,
                "redirect_uri": self.redirect_uri,
                "scope": "mall.read_order",
            }),
        )

    def exchange_code(self, code):
        """Exchange a one-minute authorization code and persist tokens outside source control."""
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("ascii")
        body = urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        }).encode("utf-8")
        request = Request(
            f"https://{self.mall_id}.cafe24api.com/api/v2/oauth/token",
            data=body,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            token = json.load(response)
        TOKEN_FILE.parent.mkdir(exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(token), encoding="utf-8")
        return token

    def fetch_orders(self, start_date, end_date):
        """Return normalized order records once the Cafe24 Orders API is connected.

        Keep this code on the server because it holds the OAuth access token.
        """
        if not self.access_token and not TOKEN_FILE.exists():
            raise RuntimeError("Cafe24 authorization has not been completed")
        raise NotImplementedError("Connect the Cafe24 Orders API in this method")
