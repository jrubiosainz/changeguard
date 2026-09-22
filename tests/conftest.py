import hashlib
import time

import pytest

from changeguard.errors import PublicError
from changeguard.settings import Settings
from changeguard.web import create_app

OPERATOR = "synthetic-test-operator-never-an-azure-credential-12345"
OFFER = {"type": "offer", "sdp": "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\nm=audio 9 RTP 0\r\n"}
ANSWER = {
    "type": "answer",
    "sdp": "v=0\r\nm=video 9 RTP 96\r\na=ice-ufrag:fixture\r\na=ice-pwd:synthetic-transient-transport\r\n",
}


class FakeAvatar:
    def __init__(self):
        self.closed = False
        self.answer = ANSWER.copy()
        self.spoken = []
        self.delay = 0
        self.failure = None

    def connect(self, offer):
        time.sleep(self.delay)
        if self.failure:
            raise self.failure
        if self.closed:
            raise PublicError("session-expired", 410)
        return self.answer

    def speak(self, text):
        self.spoken.append(text)

    def close(self):
        self.closed = True


class FakeProvider:
    def __init__(self):
        self.calls = 0
        self.current = FakeAvatar()
        self.failure = None

    def relay(self):
        self.calls += 1
        if self.failure:
            raise self.failure
        return [
            {
                "urls": ["turn:relay.communication.microsoft.com:3478"],
                "username": "synthetic-transport-user",
                "credential": "synthetic-transport-secret",
            }
        ]

    def avatar(self, ice):
        return self.current


@pytest.fixture
def settings(tmp_path):
    return Settings(
        mode="local-fixture",
        origin="http://127.0.0.1:8033",
        operator_hash=hashlib.sha256(OPERATOR.encode()).hexdigest(),
        signing_key="synthetic-signing-key-only-for-offline-tests-12345",
        data_dir=str(tmp_path),
        secure_cookie=False,
        start_interval=0,
    )


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def app(settings, provider):
    instance = create_app(settings, provider)
    instance.config.update(TESTING=True)
    yield instance
    instance.extensions["changeguard"].close()


@pytest.fixture
def client(app):
    return app.test_client()


def post(client, path, body, *, origin="http://127.0.0.1:8033"):
    return client.post(
        path,
        base_url="http://127.0.0.1:8033",
        json=body,
        headers={"Origin": origin, "X-ChangeGuard-Request": "1"},
    )


def login(client):
    response = post(client, "/api/operator/login", {"token": OPERATOR})
    assert response.status_code == 200
    return response


def prepare(client):
    login(client)
    response = post(client, "/api/avatar/prepare", {})
    assert response.status_code == 200
    return response.json["sessionId"]
