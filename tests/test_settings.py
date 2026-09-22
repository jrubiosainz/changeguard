import hashlib
from dataclasses import replace

import pytest

from changeguard.settings import ROOT, Settings
from changeguard.web import create_app
from tests.conftest import post


def test_default_preview_ignores_live_credentials_and_uses_application_relative_storage(
    monkeypatch, tmp_path
):
    for name in ("CHANGEGUARD_MODE", "PUBLIC_ORIGIN", "CHANGEGUARD_DATA_DIR", "SOURCE_COMMIT"):
        monkeypatch.delenv(name, raising=False)
    for name in ("OPERATOR_TOKEN_SHA256", "SESSION_SIGNING_KEY", "SPEECH_RESOURCE_ID", "SPEECH_HOST"):
        monkeypatch.setenv(name, "synthetic-unusable-live-setting")
    monkeypatch.chdir(tmp_path)
    settings = Settings.from_env()
    assert settings.mode == "local-fixture"
    assert settings.origin == "http://127.0.0.1:8033"
    assert settings.operator_hash == settings.speech_resource_id == settings.speech_host == ""
    assert len(settings.signing_key) >= 32
    assert settings.data_dir == str(ROOT / ".runtime")
    assert settings.source_commit == "working-tree"


def test_credential_free_preview_has_no_operator_or_billable_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CHANGEGUARD_MODE", "local-fixture")
    monkeypatch.setenv("PUBLIC_ORIGIN", "http://127.0.0.1:8033")
    monkeypatch.setenv("CHANGEGUARD_DATA_DIR", str(tmp_path))
    app = create_app()
    client = app.test_client()
    try:
        assert client.get("/", base_url="http://127.0.0.1:8033").status_code == 200
        assert client.get("/api/comparison", base_url="http://127.0.0.1:8033").status_code == 200
        assert (
            client.get("/api/evidence", base_url="http://127.0.0.1:8033").json["kind"] == "recorded-example"
        )
        assert post(client, "/api/operator/login", {"token": "synthetic-not-a-real-token"}).status_code == 409
        assert post(client, "/api/avatar/prepare", {}).status_code == 401
        assert app.extensions["changeguard"].active is None
    finally:
        app.extensions["changeguard"].close()


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.invalid",
        "https://example.invalid",
        "ftp://127.0.0.1",
        "http://127.0.0.1/path",
        "http://127.0.0.1?query=1",
        "http://127.0.0.1#fragment",
        "http://user@127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
    ],
)
def test_preview_origin_cannot_be_remote_or_ambiguous(settings, origin):
    with pytest.raises(ValueError):
        replace(settings, origin=origin).validate()


@pytest.mark.parametrize("seconds", [0, -1, 91])
def test_session_deadline_cannot_exceed_the_application_limit(settings, seconds):
    with pytest.raises(ValueError, match="at most 90 seconds"):
        replace(settings, session_seconds=seconds).validate()


@pytest.mark.parametrize("starts", [0, -1, 9])
def test_start_budget_is_bounded(settings, starts):
    with pytest.raises(ValueError, match="1 to 8 starts"):
        replace(settings, starts_per_hour=starts).validate()


def live_settings(settings):
    return replace(
        settings,
        mode="live-azure",
        origin="https://app.example.invalid",
        secure_cookie=True,
        start_interval=60,
        speech_host="example-speech.cognitiveservices.azure.com",
        speech_resource_id=(
            "/subscriptions/example-subscription/resourceGroups/rg-changeguard/"
            "providers/Microsoft.CognitiveServices/accounts/example-speech"
        ),
    )


def test_live_mode_requires_explicit_operator_hash_and_independent_signing_key(settings):
    live = live_settings(settings)
    live.validate()
    with pytest.raises(ValueError, match="operator token"):
        replace(live, operator_hash="").validate()
    with pytest.raises(ValueError, match="signing key"):
        replace(live, signing_key="").validate()


@pytest.mark.parametrize("values", [{"secure_cookie": False}, {"start_interval": 0}])
def test_live_mode_cannot_disable_cookie_or_rate_guards(settings, values):
    with pytest.raises(ValueError, match="secure cookies"):
        replace(live_settings(settings), **values).validate()


def test_live_environment_requires_bundled_runtime_identity(monkeypatch):
    monkeypatch.setenv("CHANGEGUARD_MODE", "live-azure")
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://app.example.invalid")
    monkeypatch.setenv("OPERATOR_TOKEN_SHA256", hashlib.sha256(b"synthetic-operator").hexdigest())
    monkeypatch.setenv("SESSION_SIGNING_KEY", "synthetic-signing-key-for-offline-test-only")
    with pytest.raises(ValueError, match="bundled build identity"):
        Settings.from_env()
