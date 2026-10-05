"""The saved hidma session: cookies taken from a real browser login, plus who they belong to.

hidma authenticates with cookies on api.app.hidma.com, and the login form sits behind
reCAPTCHA, so the CLI never posts credentials. `hidma login` opens a Playwright Chromium
window on the app's login page, the user signs in, and once the app's `a_t_p` cookie
appears the whole cookie jar for hidma.com is copied to SESSION_FILE (mode 600). Every
other command replays those cookies over plain HTTP and refreshes them through the app's
own refresh endpoint.
"""
from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime

DATA_DIR = os.path.expanduser("~/.local/share/hidma-cli")
PROFILE_DIR = os.path.join(DATA_DIR, "chrome-profile")
SESSION_FILE = os.path.join(DATA_DIR, "session.json")
APP = "https://app.hidma.com"
LOGIN_URL = f"{APP}/login"
TOKEN_COOKIE = "a_t_p"
TENANT_COOKIE = "s_tid"
CHROME_MAJOR = 148
# Cloudflare in front of the API rejects non-browser user agents, and cf_clearance is
# tied to the one that logged in, so the CLI sends the same string as the login browser.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              f"(KHTML, like Gecko) Chrome/{CHROME_MAJOR}.0.0.0 Safari/537.36")
LOGIN_WAIT_SECONDS = 300


class NotLoggedIn(RuntimeError):
    pass


def decode_jwt_payload(token: str) -> dict:
    """The middle segment of a JWT as a dict; {} when it is not one. No signature check: we only read exp/sub/tid."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


def load() -> dict:
    """The saved session; NotLoggedIn when there is none."""
    try:
        with open(SESSION_FILE) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise NotLoggedIn("no saved session, run `hidma login`")
    except ValueError:
        raise NotLoggedIn(f"{SESSION_FILE} is not valid JSON, run `hidma login`")
    if not data.get("cookies"):
        raise NotLoggedIn("saved session has no cookies, run `hidma login`")
    return data


def save(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SESSION_FILE)


def token_payload(data: dict) -> dict:
    for c in data.get("cookies", []):
        if c["name"] == TOKEN_COOKIE:
            return decode_jwt_payload(c["value"])
    return {}


def identity(data: dict) -> tuple[str, str]:
    """(user id, tenant id) from the saved cookies; NotLoggedIn when the token cookie is missing."""
    payload = token_payload(data)
    uid = payload.get("sub")
    if not uid:
        raise NotLoggedIn("session has no token cookie, run `hidma login`")
    tid = next((c["value"] for c in data["cookies"] if c["name"] == TENANT_COOKIE), None) or payload.get("tid") or ""
    return str(uid), str(tid)


def describe(data: dict) -> dict:
    """Non-secret facts about the session for `whoami`: ages and expiry, never values."""
    payload = token_payload(data)
    now = time.time()
    info = {
        "logged_in_at": data.get("logged_in_at"),
        "last_refresh_at": data.get("last_refresh_at"),
        "token_expires_in_s": int(payload["exp"] - now) if payload.get("exp") else None,
        "cookies": len(data.get("cookies", [])),
    }
    if data.get("logged_in_at"):
        age = datetime.now() - datetime.fromisoformat(data["logged_in_at"])
        info["age_hours"] = round(age.total_seconds() / 3600, 1)
    return info


def _hidma_cookies(cookies: list[dict]) -> list[dict]:
    keep = []
    for c in cookies:
        if "hidma.com" not in c.get("domain", ""):
            continue
        keep.append({
            "name": c["name"], "value": c["value"], "domain": c["domain"], "path": c.get("path", "/"),
            "expires": c.get("expires", -1), "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure")),
        })
    return keep


def login(wait_seconds: int = LOGIN_WAIT_SECONDS) -> dict:
    """Open a headful browser for the user to sign in; save the cookies once the app has its token cookie."""
    from playwright.sync_api import sync_playwright

    os.makedirs(PROFILE_DIR, exist_ok=True)
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        PROFILE_DIR, headless=False,
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 860},
    )
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("A browser window opened on app.hidma.com. Sign in there; this waits up to "
              f"{wait_seconds // 60} minutes and saves the session when the app is logged in.")
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            cookies = ctx.cookies()
            has_token = any(c["name"] == TOKEN_COOKIE and c["value"] for c in cookies)
            on_login = "/login" in (page.url or "")
            if has_token and not on_login:
                page.wait_for_timeout(1500)
                cookies = _hidma_cookies(ctx.cookies())
                now = datetime.now().isoformat(timespec="seconds")
                data = {"cookies": cookies, "logged_in_at": now, "last_refresh_at": None}
                uid, tid = identity(data)
                data.update(user_id=uid, tenant_id=tid)
                save(data)
                return data
            time.sleep(1)
        raise NotLoggedIn(f"timed out waiting for login ({wait_seconds // 60} min)")
    finally:
        ctx.close()
        pw.stop()
