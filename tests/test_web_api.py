"""The public token endpoint must only ever hand out a fresh room with the DJ."""

import importlib
import sys
from pathlib import Path

import jwt
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))

SECRET = "test-secret-that-is-long-enough-for-hs256"


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "APItest")
    monkeypatch.setenv("LIVEKIT_API_SECRET", SECRET)
    monkeypatch.delenv("DJ_DEMO_NUMBER", raising=False)
    import api.index as mod

    mod = importlib.reload(mod)
    mod._recent.clear()
    return mod


def _claims(resp):
    return jwt.decode(resp.json()["participant_token"], SECRET, algorithms=["HS256"])


def test_token_ignores_client_room_identity_and_dispatch(api):
    client = TestClient(api.app)
    resp = client.post(
        "/api/token",
        json={
            "room_name": "dj-_+14155551234_abc",
            "participant_identity": "sip_+14155551234",
            "participant_metadata": "x",
            "room_config": {
                "agents": [
                    {"agent_name": "dial-in-dj", "metadata": '{"phone_number": "+1"}'}
                ]
            },
        },
    )
    assert resp.status_code == 201
    claims = _claims(resp)
    assert claims["video"]["room"].startswith("dj-web-")
    assert claims["sub"].startswith("web-")
    agents = claims["roomConfig"]["agents"]
    assert agents == [{"agentName": "dial-in-dj"}]  # no client metadata
    assert claims["exp"] - claims["nbf"] <= 600


def test_rate_limit_per_ip(api, monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT", 2)
    client = TestClient(api.app)
    codes = [client.post("/api/token", json={}).status_code for _ in range(3)]
    assert codes == [201, 201, 429]


def test_removed_endpoints_are_gone(api):
    client = TestClient(api.app)
    assert (
        client.post("/api/call", json={"phone_number": "+14155551234"}).status_code
        == 404
    )
    assert client.get("/api/live").status_code == 404
    assert client.post("/api/watch", json={"room": "dj-_x"}).status_code == 404
    assert client.post("/api/hangup", json={"room": "dj-out-x"}).status_code == 404


def test_config_hides_number_when_unset(api):
    assert TestClient(api.app).get("/api/config").json() == {"phone_number": None}


def test_errors_do_not_leak_details(api, monkeypatch):
    monkeypatch.delenv("LIVEKIT_URL")
    resp = TestClient(api.app, raise_server_exceptions=False).post(
        "/api/token", json={}
    )
    assert resp.status_code == 500
    assert "LIVEKIT" not in resp.text
