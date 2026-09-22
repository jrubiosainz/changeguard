import json
import logging
import secrets
import sqlite3
import threading
import time
from _thread import LockType
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from pathlib import Path

from changeguard.errors import PublicError
from changeguard.policy import scan_boundary
from changeguard.provider import Avatar, Provider
from changeguard.settings import Settings

LOG = logging.getLogger("changeguard.sessions")


@dataclass
class ActiveSession:
    id: str
    owner: str
    started: float
    state: str = "preparing"
    avatar: Avatar | None = None
    timer: threading.Timer | None = None
    speech_count: int = 0
    cleanup_attempts: int = 0
    close_lock: LockType = field(default_factory=threading.Lock)


class SessionManager:
    def __init__(self, settings: Settings, provider: Provider):
        self.settings = settings
        self.provider = provider
        self.lock = threading.RLock()
        self.active: ActiveSession | None = None
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="avatar")
        directory = Path(settings.data_dir)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.budget_path = directory / "usage.sqlite3"
        with sqlite3.connect(self.budget_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS starts (at REAL NOT NULL)")
        self.budget_path.chmod(0o600)

    def _consume_budget(self) -> None:
        now = time.time()
        with sqlite3.connect(self.budget_path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM starts WHERE at < ?", (now - 3600,))
            count, latest = db.execute("SELECT count(*), max(at) FROM starts").fetchone()
            if count >= self.settings.starts_per_hour:
                raise PublicError("hourly-demo-budget-reached", 429)
            if latest is not None and now - latest < self.settings.start_interval:
                raise PublicError("wait-before-next-session", 429)
            db.execute("INSERT INTO starts VALUES (?)", (now,))

    def prepare(self, owner: str) -> dict:
        with self.lock:
            if self.active is not None:
                raise PublicError("demo-session-already-active", 409)
            self._consume_budget()
            active = ActiveSession(secrets.token_urlsafe(24), owner, time.monotonic())
            self.active = active
            active.timer = threading.Timer(self.settings.session_seconds, self._expire, args=(active.id,))
            active.timer.daemon = True
            active.timer.start()
        succeeded = False
        try:
            ice = self.provider.relay()
            if scan_boundary(json.dumps(ice), self.settings.forbidden_values):
                raise PublicError("response-boundary-rejected", 502)
            avatar = self.provider.avatar(ice)
            with self.lock:
                if self.active is not active:
                    avatar.close()
                    raise PublicError("session-expired", 410)
                active.avatar = avatar
                active.state = "prepared"
            succeeded = True
            return {
                "sessionId": active.id,
                "iceServers": ice,
                "deadlineSeconds": self.settings.session_seconds,
                "dataClass": "ephemeral-webrtc-transport-not-azure-service-authorization",
            }
        finally:
            if not succeeded:
                self.stop(owner, active.id)

    def _owned(self, owner: str, session_id: str) -> ActiveSession:
        active = self.active
        if active is None or active.owner != owner or active.id != session_id:
            raise PublicError("session-not-found", 404)
        return active

    def connect(self, owner: str, session_id: str, offer: dict) -> dict:
        with self.lock:
            active = self._owned(owner, session_id)
            if active.state != "prepared" or active.avatar is None:
                raise PublicError("invalid-session-state", 409)
            active.state = "connecting"
            avatar = active.avatar
        succeeded = False
        try:
            answer = self.executor.submit(avatar.connect, offer).result(timeout=self.settings.connect_timeout)
            if scan_boundary(answer["sdp"], self.settings.forbidden_values):
                raise PublicError("response-boundary-rejected", 502)
            with self.lock:
                self._owned(owner, session_id)
                if active.state != "connecting":
                    raise PublicError("session-expired", 410)
                active.state = "connected"
            succeeded = True
            return answer
        except TimeoutError:
            raise PublicError("speech-connect-deadline-exceeded", 504) from None
        finally:
            if not succeeded:
                self.stop(owner, session_id)

    def speak(self, owner: str, session_id: str, text: str) -> None:
        with self.lock:
            active = self._owned(owner, session_id)
            if active.state != "connected" or active.avatar is None:
                raise PublicError("invalid-session-state", 409)
            if active.speech_count >= 3:
                raise PublicError("session-speech-budget-reached", 429)
            active.speech_count += 1
            active.state = "speaking"
            avatar = active.avatar
        succeeded = False
        try:
            self.executor.submit(avatar.speak, text).result(timeout=25)
            with self.lock:
                self._owned(owner, session_id)
                if active.state != "speaking":
                    raise PublicError("session-expired", 410)
                active.state = "connected"
            succeeded = True
        except TimeoutError:
            raise PublicError("speech-synthesis-deadline-exceeded", 504) from None
        finally:
            if not succeeded:
                self.stop(owner, session_id)

    def stop(self, owner: str, session_id: str) -> None:
        with self.lock:
            active = self.active
            if active is None:
                return
            self._owned(owner, session_id)
            active.state = "closing"
        with active.close_lock:
            with self.lock:
                if self.active is not active:
                    return
                active.cleanup_attempts += 1
            try:
                if active.avatar is not None:
                    active.avatar.close()
            except PublicError:
                with self.lock:
                    active.state = "cleanup-failed"
                    if active.timer is not None:
                        active.timer.cancel()
                    if active.cleanup_attempts < 3:
                        active.timer = threading.Timer(1, self._expire, args=(active.id,))
                        active.timer.daemon = True
                        active.timer.start()
                    else:
                        LOG.error("avatar-cleanup-quarantined operator-retry-required")
                raise
            with self.lock:
                self.active = None
                if active.timer is not None:
                    active.timer.cancel()
        LOG.info("avatar-session-closed")

    def _expire(self, session_id: str) -> None:
        with self.lock:
            active = self.active
            if active is None or active.id != session_id:
                return
            owner = active.owner
        try:
            self.stop(owner, session_id)
        except PublicError as exc:
            LOG.error("avatar-deadline-close-error code=%s", exc.code)

    def status(self, owner: str) -> dict:
        with self.lock:
            active = self.active
            if active is None or active.owner != owner:
                return {"active": False}
            return {
                "active": True,
                "state": active.state,
                "remainingSeconds": max(
                    0, round(self.settings.session_seconds - (time.monotonic() - active.started))
                ),
            }

    def close(self) -> None:
        with self.lock:
            active = self.active
        if active is not None:
            self.stop(active.owner, active.id)
        self.executor.shutdown(wait=False, cancel_futures=True)
