"""A fake HTTPS handler so the client and api layers run through a real urllib opener (cookies included) without the network."""
from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
import urllib.response
from email.message import Message

import pytest

from hidma import client as client_mod
from hidma import session as session_mod


def jwt(exp: float, sub="user-1", tid="tenant-1") -> str:
    import base64
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub, "tid": tid, "exp": exp, "jti": "x"}).encode()).rstrip(b"=")
    return "hdr." + payload.decode() + ".sig"


def session_data(exp_in: float = 3600) -> dict:
    return {
        "cookies": [
            {"name": "a_t_p", "value": jwt(time.time() + exp_in), "domain": ".app.hidma.com", "path": "/", "expires": -1,
             "httpOnly": False, "secure": True},
            {"name": "hidma_session", "value": "secret", "domain": "api.app.hidma.com", "path": "/", "expires": -1,
             "httpOnly": True, "secure": True},
            {"name": "s_tid", "value": "tenant-1", "domain": ".app.hidma.com", "path": "/", "expires": -1,
             "httpOnly": False, "secure": True},
        ],
        "logged_in_at": "2026-10-05T09:00:00", "last_refresh_at": None,
    }


class FakeHTTPS(urllib.request.HTTPSHandler):
    """Route (METHOD, path) to a handler returning (status, body) or (status, body, headers)."""

    def __init__(self):
        super().__init__()
        self.routes = {}
        self.calls = []

    def route(self, method, path, handler):
        self.routes[(method, path)] = handler

    def https_open(self, req):
        path = req.full_url[len(client_mod.API):]
        body = json.loads(req.data) if req.data else None
        self.calls.append({"method": req.get_method(), "path": path, "body": body,
                           "headers": {k.lower(): v for k, v in req.header_items()}})
        handler = self.routes.get((req.get_method(), path.split("?")[0]))
        if handler is None:
            raise AssertionError(f"unexpected {req.get_method()} {path}")
        result = handler(self.calls[-1])
        status, payload = result[0], result[1]
        msg = Message()
        for k, v in (result[2] if len(result) > 2 else {}).items():
            msg[k] = v
        raw = json.dumps(payload).encode()
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", msg, io.BytesIO(raw))
        resp = urllib.response.addinfourl(io.BytesIO(raw), msg, req.full_url, status)
        resp.msg = "OK"
        return resp


@pytest.fixture
def opener():
    return FakeHTTPS()


@pytest.fixture
def client(opener):
    return client_mod.Client(session_data(), handler=opener, save=False)


@pytest.fixture(autouse=True)
def no_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(session_mod, "SESSION_FILE", str(tmp_path / "session.json"))
    monkeypatch.setattr(session_mod, "DATA_DIR", str(tmp_path))
