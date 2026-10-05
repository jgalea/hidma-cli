import hashlib
import json

import pytest

from hidma import client as client_mod
from hidma.client import HidmaError, SessionExpired, encode_params, idempotency_key, unwrap
from tests.conftest import session_data


def test_idempotency_key_matches_app_recipe():
    body = {"b": 2, "a": {"z": 1, "createdAt": "x", "n": None}, "updatedAt": "y"}
    url = "https://api.app.hidma.com/timesheets/1/rows/"
    canon = json.dumps({"a": {"z": 1}, "b": 2}, separators=(",", ":"))
    expected = hashlib.sha256("\n".join(["u", "t", "POST", url, canon]).encode()).hexdigest()
    assert idempotency_key("u", "t", "post", url, body) == expected


def test_encode_params_json_lists_and_skips_empty():
    assert encode_params({"page": 1, "clients": ["a", "b"], "term": None, "x": ""}) == "?page=1&clients=%5B%22a%22%2C%22b%22%5D"
    assert encode_params(None) == ""


def test_unwrap_prefers_error_then_result_then_data():
    with pytest.raises(HidmaError, match="boom"):
        unwrap({"error": "boom"})
    assert unwrap({"result": 1, "data": {"x": 1}}) == 1
    assert unwrap({"data": [1]}) == [1]
    assert unwrap([1, 2]) == [1, 2]


def test_headers_cookies_and_idempotency_on_write(client, opener):
    opener.route("POST", "timesheets/1/rows/", lambda call: (200, {"result": 1}))
    assert client.post("timesheets/1/rows/", {"id": "r"}) == 1
    h = opener.calls[0]["headers"]
    assert h["x-requested-from"] == "hidma-frontend"
    assert h["accept"] == "application/json"
    assert "idempotency-key" in h and len(h["idempotency-key"]) == 64
    assert "hidma_session=secret" in h["cookie"] and "a_t_p=" in h["cookie"]


def test_get_has_no_idempotency_key(client, opener):
    opener.route("GET", "users/user-1", lambda call: (200, {"data": {"id": "user-1"}}))
    assert client.get("users/user-1") == {"id": "user-1"}
    assert "idempotency-key" not in opener.calls[0]["headers"]


def test_refresh_before_expiry_then_request(opener):
    client = client_mod.Client(session_data(exp_in=10), handler=opener, save=False)
    new_token = session_data(exp_in=7200)["cookies"][0]["value"]
    opener.route("POST", "auth/tokens/refresh/",
                 lambda call: (200, {"result": 1}, {"Set-Cookie": f"a_t_p={new_token}; Domain=.app.hidma.com; Path=/; Secure"}))
    opener.route("GET", "users/user-1", lambda call: (200, {"data": {"id": "user-1"}}))
    assert client.get("users/user-1") == {"id": "user-1"}
    assert [c["path"] for c in opener.calls] == ["auth/tokens/refresh/", "users/user-1"]
    assert opener.calls[0]["body"] == {"uid": "user-1"}
    assert client.data["last_refresh_at"] is not None
    assert new_token in opener.calls[1]["headers"]["cookie"]


def test_401_triggers_one_refresh_and_retry(client, opener):
    state = {"n": 0}

    def users(call):
        state["n"] += 1
        return (401, {"error": "Unauthenticated"}) if state["n"] == 1 else (200, {"data": {"id": "user-1"}})

    token = session_data()["cookies"][0]["value"]
    opener.route("GET", "users/user-1", users)
    opener.route("POST", "auth/tokens/refresh/", lambda call: (200, {"result": 1}, {"Set-Cookie": f"a_t_p={token}; Domain=.app.hidma.com; Path=/"}))
    assert client.get("users/user-1") == {"id": "user-1"}
    assert [c["path"] for c in opener.calls] == ["users/user-1", "auth/tokens/refresh/", "users/user-1"]


def test_dead_session_is_exit_4_material(client, opener):
    opener.route("GET", "users/user-1", lambda call: (401, {"error": "Unauthenticated"}))
    opener.route("POST", "auth/tokens/refresh/", lambda call: (401, {"error": "invalid_token"}))
    with pytest.raises(SessionExpired):
        client.get("users/user-1")


def test_419_is_session_expired(client, opener):
    opener.route("GET", "users/user-1", lambda call: (419, {"error": "User Tokens Expired"}))
    with pytest.raises(SessionExpired):
        client.get("users/user-1")


def test_other_http_errors_carry_status(client, opener):
    opener.route("GET", "users/user-1", lambda call: (422, {"error": {"minutes": "required"}}))
    with pytest.raises(HidmaError) as exc:
        client.get("users/user-1")
    assert exc.value.status == 422 and "minutes: required" in str(exc.value)


def test_missing_session_file_is_not_logged_in():
    from hidma import session
    with pytest.raises(session.NotLoggedIn):
        session.load()
