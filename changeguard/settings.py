import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from changeguard.provenance import verified_build

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    mode: str
    origin: str
    operator_hash: str
    signing_key: str
    speech_resource_id: str = ""
    speech_host: str = ""
    region: str = "westeurope"
    source_commit: str = "working-tree"
    source_digest: str = ""
    data_dir: str = ".runtime"
    session_seconds: int = 90
    connect_timeout: int = 35
    start_interval: int = 60
    starts_per_hour: int = 8
    secure_cookie: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.environ.get("CHANGEGUARD_MODE", "local-fixture")
        origin = os.environ.get("PUBLIC_ORIGIN", "http://127.0.0.1:8033").rstrip("/")
        live = mode == "live-azure"
        build = verified_build(ROOT) if live else {}
        settings = cls(
            mode=mode,
            origin=origin,
            operator_hash=os.environ.get("OPERATOR_TOKEN_SHA256", "") if live else "",
            signing_key=os.environ.get("SESSION_SIGNING_KEY", "") if live else secrets.token_urlsafe(48),
            speech_resource_id=os.environ.get("SPEECH_RESOURCE_ID", "") if live else "",
            speech_host=os.environ.get("SPEECH_HOST", "") if live else "",
            region=os.environ.get("SPEECH_REGION", "westeurope"),
            source_commit=build.get("sourceCommit", os.environ.get("SOURCE_COMMIT", "working-tree")),
            source_digest=build.get("sourceDigest", ""),
            data_dir=os.environ.get("CHANGEGUARD_DATA_DIR", str(ROOT / ".runtime")),
            secure_cookie=origin.startswith("https://"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.mode not in {"live-azure", "local-fixture"}:
            raise ValueError("CHANGEGUARD_MODE must be live-azure or local-fixture")
        origin = urlsplit(self.origin)
        if (
            origin.scheme not in {"http", "https"}
            or origin.path
            or origin.query
            or origin.fragment
            or origin.username is not None
            or origin.password is not None
            or not origin.hostname
        ):
            raise ValueError("PUBLIC_ORIGIN must be an origin, not a URL with a path")
        if origin.port is not None and not 1 <= origin.port <= 65535:
            raise ValueError("PUBLIC_ORIGIN must use a valid port")
        local = origin.hostname in {"127.0.0.1", "localhost", "::1"}
        if origin.scheme != "https" and not (local and self.mode == "local-fixture"):
            raise ValueError("HTTPS is required outside the loopback fixture")
        if self.mode == "local-fixture" and not local:
            raise ValueError("Fixture execution must remain local")
        if (self.mode == "live-azure" or self.operator_hash) and not re.fullmatch(
            r"[a-f0-9]{64}", self.operator_hash
        ):
            raise ValueError("A high-entropy operator token SHA256 is required")
        if len(self.signing_key) < 32:
            raise ValueError("An independent session signing key is required")
        if not 0 < self.session_seconds <= 90:
            raise ValueError("The server session deadline must be positive and at most 90 seconds")
        if not 1 <= self.starts_per_hour <= 8 or self.start_interval < 0:
            raise ValueError("The start budget must allow 1 to 8 starts per hour with a nonnegative interval")
        if self.mode == "live-azure":
            if not self.secure_cookie or self.start_interval < 60:
                raise ValueError(
                    "Live execution requires secure cookies and at least 60 seconds between starts"
                )
            if not re.fullmatch(r"[a-z0-9-]+\.cognitiveservices\.azure\.com", self.speech_host):
                raise ValueError("SPEECH_HOST must be an Azure Cognitive Services custom hostname")
            if not re.fullmatch(
                r"/subscriptions/[^/]+/resourceGroups/[^/]+/"
                r"providers/Microsoft\.CognitiveServices/accounts/[^/]+",
                self.speech_resource_id,
            ):
                raise ValueError("A server-only Speech resource ID is required")

    @property
    def forbidden_values(self) -> tuple[str, ...]:
        values = [self.speech_host, self.speech_resource_id]
        if self.speech_resource_id:
            segments = self.speech_resource_id.split("/")
            values.extend((segments[2], segments[4], segments[-1]))
        return tuple(value for value in values if value)
