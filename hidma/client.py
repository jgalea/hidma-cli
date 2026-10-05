"""Plain-HTTP client for api.app.hidma.com that replays the saved browser cookies.

Mirrors the app's interceptors: Accept/X-Requested-From headers, cookies on every call,
an Idempotency-Key on writes (SHA-256 of user, tenant, method, URL and canonical body),
and one refresh-then-retry on 401. A dead session surfaces as SessionExpired.
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime

from . import session as session_mod

API = "https://api.app.hidma.com/"
REFRESH_PATH = "auth/tokens/refresh/"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
REFRESH_MARGIN_S = 60
TIMEOUT_S = 30


class HidmaError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


class SessionExpired(HidmaError):
    pass


def unwrap(payload):
    """The app's envelope: error, then result, then data (chunk-D335H3Z3 G)."""
    if not isinstance(payload, dict):
        return payload
    if payload.get("error"):
        raise HidmaError(str(payload["error"]), body=payload)
    if "result" in payload and payload["result"] is not None:
        return payload["result"]
    if "data" in payload:
        return payload["data"]
    return payload


def _canonical(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    if isinstance(value, dict):
        return {k: _canonical(value[k]) for k in sorted(value) if k not in ("createdAt", "updatedAt") and value[k] is not None}
    return value


def idempotency_key(user_id: str, tenant_id: str, method: str, url: str, body) -> str:
    """Same recipe as the app (chunk-RD3H54Y6 oc), so an identical retry is deduplicated server-side."""
    if body is None:
        canon = ""
    else:
        canon = json.dumps(_canonical(body), separators=(",", ":"), ensure_ascii=False)
    material = "\n".join([user_id, tenant_id, method.upper(), url, canon])
    return hashlib.sha256(material.encode()).hexdigest()


def encode_params(params: dict | None) -> str:
    """Query string the way the app builds it: lists and dicts as JSON, None/'' skipped."""
    if not params:
        return ""
    items = []
    for k, v in params.items():
        if v is None or v == "":
            continue
        if isinstance(v, (list, dict)):
            v = json.dumps(v, separators=(",", ":"))
        elif isinstance(v, bool):
            v = "true" if v else "false"
        items.append((k, str(v)))
    return "?" + urllib.parse.urlencode(items) if items else ""


class Client:
    def __init__(self, data: dict | None = None, *, handler=None, save=True):
        self.data = data if data is not None else session_mod.load()
        self.user_id, self.tenant_id = session_mod.identity(self.data)
        self.jar = http.cookiejar.CookieJar()
        for c in self.data["cookies"]:
            self.jar.set_cookie(_to_cookie(c))
        handlers = [urllib.request.HTTPCookieProcessor(self.jar)] + ([handler] if handler else [])
        self.opener = urllib.request.build_opener(*handlers)
        self._save = save
        self._refreshed = False

    # -- cookies ---------------------------------------------------------

    def _persist(self, refreshed: bool = False) -> None:
        self.data["cookies"] = [_from_cookie(c) for c in self.jar if "hidma.com" in c.domain]
        if refreshed:
            self.data["last_refresh_at"] = datetime.now().isoformat(timespec="seconds")
        if self._save:
            session_mod.save(self.data)

    def token_exp(self) -> float | None:
        payload = session_mod.token_payload(self.data)
        return payload.get("exp")

    def ensure_fresh(self) -> None:
        """Refresh ahead of expiry, like the app's 60 s timer."""
        exp = self.token_exp()
        if exp is not None and exp - time.time() < REFRESH_MARGIN_S:
            self.refresh()

    def refresh(self) -> None:
        if self._refreshed:
            raise SessionExpired("session expired, run `hidma login`")
        self._refreshed = True
        try:
            self._raw("POST", REFRESH_PATH, {"uid": self.user_id}, source="authService.refreshTokensAsync")
        except HidmaError as exc:
            if exc.status in (401, 419) or exc.status is None and "invalid_token" in str(exc):
                raise SessionExpired("session expired, run `hidma login`") from exc
            raise
        if not any(c.name == session_mod.TOKEN_COOKIE for c in self.jar):
            raise SessionExpired("session expired, run `hidma login`")
        self._persist(refreshed=True)

    # -- requests --------------------------------------------------------

    def request(self, method: str, path: str, body=None, params: dict | None = None, source: str = "hidma-cli"):
        """One API call, unwrapped. Refreshes once on 401 and retries."""
        self.ensure_fresh()
        try:
            return unwrap(self._raw(method, path, body, params, source))
        except HidmaError as exc:
            if exc.status == 401:
                self.refresh()
                return unwrap(self._raw(method, path, body, params, source))
            raise

    def get(self, path, params=None, source="hidma-cli"):
        return self.request("GET", path, None, params, source)

    def post(self, path, body, params=None, source="hidma-cli"):
        return self.request("POST", path, body, params, source)

    def put(self, path, body, params=None, source="hidma-cli"):
        return self.request("PUT", path, body, params, source)

    def delete(self, path, params=None, source="hidma-cli"):
        return self.request("DELETE", path, None, params, source)

    def _raw(self, method: str, path: str, body=None, params=None, source="hidma-cli"):
        url = API + path.lstrip("/") + encode_params(params)
        headers = {"Accept": "application/json", "X-Requested-From": "hidma-frontend", "X-Request-Source": source,
                   "Origin": session_mod.APP, "Referer": session_mod.APP + "/",
                   "User-Agent": session_mod.USER_AGENT}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        if method in WRITE_METHODS:
            headers["Idempotency-Key"] = idempotency_key(self.user_id, self.tenant_id, method, url, body)
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=TIMEOUT_S) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            parsed = _parse(raw)
            if exc.code == 419:
                raise SessionExpired("session expired, run `hidma login`")
            if exc.code == 401 and isinstance(parsed, dict) and parsed.get("error") == "invalid_token":
                raise SessionExpired("session expired, run `hidma login`")
            msg = _error_message(parsed) or exc.reason
            raise HidmaError(f"HTTP {exc.code} {method} {path}: {msg}", status=exc.code, body=parsed)
        except urllib.error.URLError as exc:
            raise HidmaError(f"cannot reach {API}: {exc.reason}")
        if method in WRITE_METHODS or path == REFRESH_PATH:
            self._persist()
        if status == 204 or not raw:
            return {}
        return _parse(raw)


def _parse(raw: bytes):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {"error": raw[:300].decode(errors="replace")}


def _error_message(parsed) -> str:
    if isinstance(parsed, dict):
        err = parsed.get("error") or parsed.get("message") or parsed.get("errors")
        if isinstance(err, dict):
            return "; ".join(f"{k}: {v}" for k, v in err.items())
        if err:
            return str(err)
    return ""


def _to_cookie(c: dict) -> http.cookiejar.Cookie:
    expires = c.get("expires")
    expires = int(expires) if expires and expires > 0 else None
    domain = c["domain"]
    return http.cookiejar.Cookie(
        version=0, name=c["name"], value=c["value"], port=None, port_specified=False,
        domain=domain, domain_specified=True, domain_initial_dot=domain.startswith("."),
        path=c.get("path", "/"), path_specified=True, secure=bool(c.get("secure")), expires=expires,
        discard=expires is None, comment=None, comment_url=None, rest={"HttpOnly": None} if c.get("httpOnly") else {},
        rfc2109=False,
    )


def _from_cookie(c: http.cookiejar.Cookie) -> dict:
    return {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
            "expires": c.expires if c.expires else -1, "httpOnly": c.has_nonstandard_attr("HttpOnly"),
            "secure": bool(c.secure)}


def new_id() -> str:
    return str(uuid.uuid4())
