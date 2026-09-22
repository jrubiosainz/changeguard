import hashlib
import time
from dataclasses import replace

import pytest

from changeguard.errors import PublicError
from changeguard.policy import scan_boundary
from changeguard.settings import ROOT
from changeguard.web import create_app
from tests.conftest import OFFER, login, post, prepare


@pytest.mark.parametrize(
    "path", ["/api/getSpeechToken", "/api/speech/token", "/api/token", "/api/getIceToken"]
)
@pytest.mark.parametrize("method", ["GET", "POST", "HEAD", "OPTIONS", "PUT", "DELETE"])
def test_obsolete_token_endpoints_are_inaccessible(client, path, method):
    login(client)
    response = client.open(path, method=method, base_url="http://127.0.0.1:8033")
    assert response.status_code == 404
    assert not scan_boundary(response.get_data(as_text=True))


@pytest.mark.parametrize(
    "path",
    ["/api/avatar/prepare", "/api/avatar/connect", "/api/avatar/speak", "/api/avatar/stop"],
)
def test_billable_routes_fail_closed_without_operator(client, provider, path):
    assert post(client, path, {}).status_code == 401
    assert provider.calls == 0


def test_cross_origin_and_missing_marker_are_denied(client, provider):
    login(client)
    assert post(client, "/api/avatar/prepare", {}, origin="https://untrusted.invalid").status_code == 403
    response = client.post(
        "/api/avatar/prepare",
        json={},
        base_url="http://127.0.0.1:8033",
        headers={"Origin": "http://127.0.0.1:8033"},
    )
    assert response.status_code == 403
    assert provider.calls == 0


def test_cookie_is_httponly_samesite_and_not_azure_authorization(client):
    response = login(client)
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert not scan_boundary(cookie)
    assert client.get("/api/operator", base_url="http://127.0.0.1:8033").json["authenticated"]


def test_secure_host_cookie_configuration(settings, provider):
    secure = replace(settings, origin="https://demo.example", secure_cookie=True)
    # The fixture cannot be accidentally made public, even with a secure cookie.
    with pytest.raises(ValueError, match="Fixture execution must remain local"):
        create_app(secure, provider)


def test_errors_do_not_reflect_private_values(client, provider, caplog):
    login(client)
    provider.failure = PublicError("speech-relay-unavailable", 502)
    response = post(client, "/api/avatar/prepare", {})
    assert response.json == {"error": "speech-relay-unavailable"}
    assert "speech-relay-unavailable" in caplog.text
    assert not scan_boundary(response.get_data(as_text=True))


def test_successful_fake_signaling_is_never_claimed_as_live_media(client, provider):
    session = prepare(client)
    connected = post(client, "/api/avatar/connect", {"sessionId": session, "offer": OFFER})
    assert connected.status_code == 200
    assert set(connected.json) == {"type", "sdp"}
    assert not scan_boundary(connected.get_data(as_text=True))
    assert post(client, "/api/avatar/speak", {"sessionId": session, "text": "Synthetic test"}).json == {
        "synthesized": True
    }
    assert provider.current.spoken == ["Synthetic test"]
    assert post(client, "/api/avatar/stop", {"sessionId": session}).json["closed"]
    assert provider.current.closed
    health = client.get("/api/health", base_url="http://127.0.0.1:8033").json
    assert health["executionMode"] == "local-fixture"
    assert health["avatarReadiness"] == "not-proven-by-health-check"


def test_server_refuses_a_leaking_provider_answer(client, provider):
    session = prepare(client)
    provider.current.answer["sdp"] += (
        "aad#/subscriptions/SYNTHETIC/resourceGroups/SYNTHETIC/providers/SYNTHETIC#INVALID"
    )
    response = post(client, "/api/avatar/connect", {"sessionId": session, "offer": OFFER})
    assert response.status_code == 502
    assert response.json == {"error": "response-boundary-rejected"}
    assert provider.current.closed
    assert not scan_boundary(response.get_data(as_text=True))


def test_ownership_and_one_session_limit(app, client, provider):
    session = prepare(client)
    stranger = app.test_client()
    login(stranger)
    assert post(stranger, "/api/avatar/connect", {"sessionId": session, "offer": OFFER}).status_code == 404
    assert post(stranger, "/api/avatar/stop", {"sessionId": session}).status_code == 404
    assert post(stranger, "/api/avatar/prepare", {}).status_code == 409
    assert provider.calls == 1


@pytest.mark.parametrize("offer", [{}, {"type": "answer", "sdp": "v=0"}, {"type": "offer", "sdp": 42}])
def test_invalid_offers_never_reach_provider(client, provider, offer):
    session = prepare(client)
    response = post(client, "/api/avatar/connect", {"sessionId": session, "offer": offer})
    assert response.status_code == 400
    assert provider.current.spoken == []


def test_deadline_closes_provider_without_browser_action(settings, provider):
    app = create_app(replace(settings, session_seconds=0.12), provider)
    client = app.test_client()
    prepare(client)
    time.sleep(0.2)
    assert provider.current.closed
    assert app.extensions["changeguard"].active is None
    app.extensions["changeguard"].close()


def test_connect_timeout_closes_provider(settings, provider):
    app = create_app(replace(settings, connect_timeout=0.01), provider)
    provider.current.delay = 0.04
    client = app.test_client()
    session = prepare(client)
    response = post(client, "/api/avatar/connect", {"sessionId": session, "offer": OFFER})
    assert response.status_code == 504
    assert provider.current.closed
    app.extensions["changeguard"].close()


def test_hourly_budget_is_persisted_across_manager_restarts(settings, provider):
    settings = replace(settings, starts_per_hour=1)
    first = create_app(settings, provider)
    client = first.test_client()
    session = prepare(client)
    post(client, "/api/avatar/stop", {"sessionId": session})
    first.extensions["changeguard"].close()
    second = create_app(settings, provider)
    client = second.test_client()
    login(client)
    assert post(client, "/api/avatar/prepare", {}).status_code == 429
    second.extensions["changeguard"].close()


def test_csp_and_no_store_are_present(client):
    response = client.get("/", base_url="http://127.0.0.1:8033")
    assert response.status_code == 200
    assert "connect-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_query_credentials_are_not_accepted_or_reflected(client):
    response = client.get("/api/health?authorization=SYNTHETIC-NEVER-REAL", base_url="http://127.0.0.1:8033")
    assert response.status_code == 400
    assert "SYNTHETIC" not in response.get_data(as_text=True)


def test_no_automatic_human_approval_route(client):
    login(client)
    response = post(client, "/api/approval", {"state": "approved"})
    assert response.status_code == 404
    assert client.get("/api/manifest", base_url="http://127.0.0.1:8033").json["approval"] == {
        "state": "pending",
        "actorType": "none",
        "reason": "Human review required",
    }


def test_local_fixture_cannot_spend_on_azure(settings):
    app = create_app(settings)
    client = app.test_client()
    login(client)
    response = post(client, "/api/avatar/prepare", {})
    assert response.status_code == 409
    assert response.json["error"] == "live-avatar-unavailable-in-fixture-mode"
    assert app.extensions["changeguard"].active is None
    app.extensions["changeguard"].close()


def test_recorded_example_is_a_distinct_read_only_artifact(client, provider):
    response = client.get("/static/examples/recorded-session.json", base_url="http://127.0.0.1:8033")
    example = (ROOT / "static/examples/recorded-session.json").read_bytes()
    assert response.status_code == 200
    assert response.data == example
    report = client.get("/api/evidence", base_url="http://127.0.0.1:8033").json
    assert report["kind"] == report["executionMode"] == "recorded-example"
    assert "sourceCommit" not in report
    assert hashlib.sha256(example).hexdigest() != report["provenance"]["originalReportSha256"]
    assert report["provenance"]["transformation"] == "public metadata adaptation only"
    assert provider.calls == 0
