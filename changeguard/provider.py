import base64
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Protocol

import requests
from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

from changeguard.errors import PublicError
from changeguard.settings import Settings

LOG = logging.getLogger("changeguard.provider")
SCOPE = "https://cognitiveservices.azure.com/.default"
CLEANUP = ThreadPoolExecutor(max_workers=2, thread_name_prefix="avatar-cleanup")


class Avatar(Protocol):
    def connect(self, offer: dict) -> dict: ...

    def speak(self, text: str) -> None: ...

    def close(self) -> None: ...


class Provider(Protocol):
    def relay(self) -> list[dict]: ...

    def avatar(self, ice: list[dict]) -> Avatar: ...


def checked_result(result, sdk) -> None:
    if result.reason != sdk.ResultReason.Canceled:
        return
    details = result.cancellation_details
    code = str(details.error_code)
    # Inspect upstream diagnostics in memory; never log or return the diagnostic string.
    message = str(details.error_details).lower()
    if "429" in message or "capacity" in message or "TooManyRequests" in code:
        raise PublicError("speech-capacity-or-throttle", 503)
    if "Authentication" in code or "401" in message or "403" in message:
        raise PublicError("speech-authorization-failed", 502)
    if "Connection" in code:
        raise PublicError("speech-connection-failed", 502)
    raise PublicError("speech-synthesis-canceled", 502)


class AzureProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        options = {"connection_timeout": 5, "read_timeout": 10, "retry_total": 0}
        self.credential = (
            ManagedIdentityCredential(**options)
            if os.environ.get("WEBSITE_INSTANCE_ID")
            else DefaultAzureCredential(
                exclude_interactive_browser_credential=True, process_timeout=10, **options
            )
        )

    def token(self) -> str:
        try:
            return self.credential.get_token(SCOPE).token
        except AzureError as exc:
            raise PublicError("managed-identity-unavailable", 503) from exc

    def relay(self) -> list[dict]:
        try:
            response = requests.get(
                f"https://{self.settings.speech_host}/tts/cognitiveservices/avatar/relay/token/v1",
                headers={"Authorization": f"Bearer {self.token()}"},
                timeout=(5, 12),
            )
            if response.status_code in {401, 403}:
                raise PublicError("speech-authorization-failed", 502)
            if response.status_code == 429:
                raise PublicError("speech-capacity-or-throttle", 503)
            response.raise_for_status()
            payload = response.json()
            urls = payload["Urls"]
            if (
                not isinstance(urls, list)
                or not urls
                or any(
                    not isinstance(url, str)
                    or not re.fullmatch(
                        r"turns?:[a-zA-Z0-9.-]+\.communication\.microsoft\.com:"
                        r"\d+(?:\?transport=(?:udp|tcp))?",
                        url,
                    )
                    for url in urls
                )
                or not isinstance(payload["Username"], str)
                or not isinstance(payload["Password"], str)
            ):
                raise PublicError("unexpected-relay-contract", 502)
            return [{"urls": urls, "username": payload["Username"], "credential": payload["Password"]}]
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise PublicError("speech-relay-unavailable", 502) from exc

    def avatar(self, ice: list[dict]) -> Avatar:
        return AzureAvatar(self, ice)


class AzureAvatar:
    def __init__(self, provider: AzureProvider, ice: list[dict]):
        self.provider = provider
        self.ice = ice
        self._lock = threading.Lock()
        self._closed = False
        self._closing = False
        self.connection = None
        self.synthesizer = None

    def connect(self, offer: dict) -> dict:
        import azure.cognitiveservices.speech as sdk

        settings = self.provider.settings
        token = self.provider.token()
        speech = sdk.SpeechConfig(
            endpoint=(
                f"wss://{settings.speech_host}/tts/cognitiveservices/websocket/v1?enableTalkingAvatar=true"
            )
        )
        speech.authorization_token = f"aad#{settings.speech_resource_id}#{token}"
        speech.speech_synthesis_voice_name = "en-US-AvaMultilingualNeural"
        synthesizer = sdk.SpeechSynthesizer(speech_config=speech, audio_config=None)
        connection = sdk.Connection.from_speech_synthesizer(synthesizer)
        config = {
            "synthesis": {
                "video": {
                    "protocol": {
                        "name": "WebRTC",
                        "webrtcConfig": {
                            "clientDescription": base64.b64encode(json.dumps(offer).encode()).decode(),
                            "iceServers": self.ice,
                        },
                    },
                    "format": {
                        "resolution": {"width": 1920, "height": 1080},
                        "bitrate": 1000000,
                    },
                    "talkingAvatar": {
                        "customized": False,
                        "character": "lisa",
                        "style": "casual-sitting",
                        "background": {"color": "#172235FF"},
                    },
                }
            }
        }
        with self._lock:
            if self._closed or self._closing:
                connection.close()
                raise PublicError("session-expired", 410)
            self.connection = connection
            self.synthesizer = synthesizer
            try:
                connection.set_message_property("speech.config", "context", json.dumps(config))
                operation = synthesizer.speak_text_async("")
            except RuntimeError as exc:
                raise PublicError("speech-negotiation-failed", 502) from exc
        try:
            checked_result(operation.get(), sdk)
            turn = synthesizer.properties.get_property_by_name("SpeechSDKInternal-ExtraTurnStartMessage")
            encoded = json.loads(turn)["webrtc"]["connectionString"]
            answer = json.loads(base64.b64decode(encoded, validate=True))
            if answer.get("type") != "answer" or not isinstance(answer.get("sdp"), str):
                raise PublicError("unexpected-sdp-contract", 502)
            with self._lock:
                if self._closed or self._closing:
                    raise PublicError("session-expired", 410)
            return {"type": "answer", "sdp": answer["sdp"]}
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            raise PublicError("speech-negotiation-failed", 502) from exc

    def speak(self, text: str) -> None:
        import azure.cognitiveservices.speech as sdk

        with self._lock:
            if self._closed or self._closing or self.synthesizer is None:
                raise PublicError("session-expired", 410)
            try:
                operation = self.synthesizer.speak_text_async(text)
            except RuntimeError as exc:
                raise PublicError("speech-synthesis-failed", 502) from exc
        try:
            checked_result(operation.get(), sdk)
            with self._lock:
                if self._closed or self._closing:
                    raise PublicError("session-expired", 410)
        except RuntimeError as exc:
            raise PublicError("speech-synthesis-failed", 502) from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closing = True
            if self.connection is None:
                self._closed = True
                return
            failed = False
            try:
                if self.synthesizer is not None:
                    operation = self.synthesizer.stop_speaking_async()
                    CLEANUP.submit(operation.get).result(timeout=3)
            except (RuntimeError, TimeoutError):
                failed = True
                LOG.error("speech-cancel-failed")
            try:
                self.connection.close()
            except RuntimeError:
                failed = True
                LOG.error("speech-connection-close-failed")
            if failed:
                raise PublicError("speech-close-failed", 502) from None
            self._closed = True


class FixtureProvider:
    def relay(self) -> list[dict]:
        raise PublicError("live-avatar-unavailable-in-fixture-mode", 409)

    def avatar(self, ice: list[dict]) -> Avatar:
        raise PublicError("live-avatar-unavailable-in-fixture-mode", 409)
