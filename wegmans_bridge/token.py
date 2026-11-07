"""Gets a Wegmans access token by letting a real browser profile do the login dance.

Wegmans uses Azure AD B2C (myaccount.wegmans.com). Access tokens last one hour and
the SPA refresh token is short-lived, so instead of re-implementing MSAL we keep a
persistent Chromium profile that you sign into once. Each refresh opens
wegmans.com headlessly, lets the site renew its own token silently from the B2C
session cookie, and captures the Bearer header from its first API call.
"""
from __future__ import annotations

import base64
import json
import logging
import time

log = logging.getLogger(__name__)

API_HOST = "api.digitaldevelopment.wegmans.cloud"
START_URL = "https://www.wegmans.com/my-list"


class LoginRequired(RuntimeError):
    """The saved browser session no longer produces a token; run `login` again."""


def jwt_exp(token: str) -> int:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return int(json.loads(base64.urlsafe_b64decode(payload))["exp"])


class BrowserTokenProvider:
    def __init__(self, profile_dir: str, timeout_s: int = 45):
        self.profile_dir = profile_dir
        self.timeout_s = timeout_s
        self._token: str | None = None
        self._exp = 0

    def get(self) -> str:
        # Renew five minutes before expiry.
        if self._token and time.time() < self._exp - 300:
            return self._token
        self._token = self._capture()
        self._exp = jwt_exp(self._token)
        log.info("Got Wegmans token, valid until %s", time.strftime("%H:%M", time.localtime(self._exp)))
        return self._token

    def invalidate(self) -> None:
        self._token, self._exp = None, 0

    def _capture(self) -> str:
        from playwright.sync_api import sync_playwright

        captured: dict[str, str] = {}

        def on_request(req):
            if API_HOST in req.url and "authorization" in req.headers:
                auth = req.headers["authorization"]
                if auth.lower().startswith("bearer ") and len(auth) > 100:
                    captured.setdefault("token", auth[7:])

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(self.profile_dir, headless=True)
            try:
                page = ctx.new_page()
                page.on("request", on_request)
                page.goto(START_URL, wait_until="domcontentloaded")
                deadline = time.time() + self.timeout_s
                while "token" not in captured and time.time() < deadline:
                    page.wait_for_timeout(500)
            finally:
                ctx.close()

        if "token" not in captured:
            raise LoginRequired(
                "No Wegmans token captured - the saved sign-in has probably expired. Run `login` again."
            )
        return captured["token"]


def interactive_login(profile_dir: str) -> None:
    """Open a visible browser on the profile so you can sign in once."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(profile_dir, headless=False)
        page = ctx.new_page()
        page.goto("https://www.wegmans.com/")
        print("\nSign in to Wegmans in the browser window (tick 'keep me signed in' if offered).")
        input("When your name shows at the top of the page, press Enter here... ")
        ctx.close()
    # Prove it worked.
    tok = BrowserTokenProvider(profile_dir).get()
    print(f"Login saved. Token valid until {time.strftime('%H:%M', time.localtime(jwt_exp(tok)))}.")
