import base64
import json
import sys
import threading
from types import SimpleNamespace

import pytest

from changeguard.errors import PublicError
from changeguard.provider import AzureAvatar
from tests.conftest import ANSWER, OFFER, login, post, prepare


class Completed:
    def get(self):
        return SimpleNamespace(reason="complete")


class NativeDouble:
    def __init__(self):
        self.events = []
        self.fail_close = False
        self.launched = threading.Event()
        self.release = threading.Event()
        self.close_entered = threading.Event()
        self.properties = SimpleNamespace(
            get_property_by_name=lambda name: json.dumps(
                {"webrtc": {"connectionString": base64.b64encode(json.dumps(ANSWER).encode()).decode()}}
            )
        )

    def set_message_property(self, *args):
        pass

    def speak_text_async(self, text):
        self.events.append("launch")
        self.launched.set()
        assert self.release.wait(2)
        return Completed()

    def stop_speaking_async(self):
        self.events.append("cancel")
        return Completed()

    def close(self):
        self.close_entered.set()
        self.events.append("close")
        if self.fail_close:
            self.fail_close = False
            raise RuntimeError("synthetic native close failure")


def stub_sdk(monkeypatch, native):
    import azure.cognitiveservices

    sdk = SimpleNamespace(
        SpeechConfig=lambda **kwargs: SimpleNamespace(),
        SpeechSynthesizer=lambda **kwargs: native,
        Connection=SimpleNamespace(from_speech_synthesizer=lambda synthesizer: native),
        ResultReason=SimpleNamespace(Canceled="canceled"),
    )
    monkeypatch.setitem(sys.modules, "azure.cognitiveservices.speech", sdk)
    monkeypatch.setattr(azure.cognitiveservices, "speech", sdk, raising=False)


def wrapper(settings):
    provider = SimpleNamespace(settings=settings, token=lambda: "SYNTHETIC-NOT-AZURE")
    return AzureAvatar(provider, [])


def test_stop_cannot_interleave_before_async_launch(monkeypatch, settings):
    native = NativeDouble()
    stub_sdk(monkeypatch, native)
    avatar = wrapper(settings)
    errors = []

    def connect():
        try:
            avatar.connect(OFFER)
        except PublicError as exc:
            errors.append(exc.code)

    worker = threading.Thread(target=connect)
    worker.start()
    assert native.launched.wait(1)
    stopper = threading.Thread(target=avatar.close)
    stopper.start()
    assert not native.close_entered.wait(0.05)
    native.release.set()
    stopper.join(2)
    worker.join(2)
    assert not stopper.is_alive() and not worker.is_alive()
    assert native.events == ["launch", "cancel", "close"]
    assert errors in ([], ["session-expired"])
    with pytest.raises(PublicError, match="session-expired"):
        avatar.speak("Must never restart a closed SDK connection")
    assert native.events == ["launch", "cancel", "close"]


def test_failed_native_close_is_retryable_and_blocks_new_synthesis(monkeypatch, settings):
    native = NativeDouble()
    native.fail_close = True
    stub_sdk(monkeypatch, native)
    avatar = wrapper(settings)
    avatar.connection = native
    avatar.synthesizer = native
    with pytest.raises(PublicError, match="speech-close-failed"):
        avatar.close()
    with pytest.raises(PublicError, match="session-expired"):
        avatar.speak("Do not restart while cleanup is pending")
    avatar.close()
    assert native.events == ["cancel", "close", "cancel", "close"]
    assert avatar._closed


def test_manager_retains_failed_cleanup_until_confirmed(app, client, provider, monkeypatch):
    session = prepare(client)
    attempts = []

    def close():
        attempts.append(1)
        if len(attempts) == 1:
            raise PublicError("speech-close-failed", 502)
        provider.current.closed = True

    monkeypatch.setattr(provider.current, "close", close)
    assert post(client, "/api/avatar/stop", {"sessionId": session}).status_code == 502
    status = client.get("/api/avatar/status", base_url="http://127.0.0.1:8033").json
    assert status["active"] and status["state"] == "cleanup-failed"
    assert post(client, "/api/avatar/prepare", {}).status_code == 409
    assert post(client, "/api/avatar/stop", {"sessionId": session}).status_code == 200
    assert provider.current.closed
    assert app.extensions["changeguard"].active is None
    assert len(attempts) == 2


def test_unexpected_provider_failure_also_releases_unused_reservation(app, client, provider, monkeypatch):
    login(client)

    def relay():
        raise ValueError("Synthetic failure, not a real credential")

    monkeypatch.setattr(provider, "relay", relay)
    assert post(client, "/api/avatar/prepare", {}).status_code == 500
    assert app.extensions["changeguard"].active is None
