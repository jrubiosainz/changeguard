import atexit
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from collections import deque
from functools import wraps
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, render_template, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.exceptions import HTTPException

from changeguard.errors import PublicError
from changeguard.policy import architecture_gate, scan_boundary, synthetic_comparison
from changeguard.provider import AzureProvider, FixtureProvider, Provider
from changeguard.sessions import SessionManager
from changeguard.settings import ROOT, Settings

COOKIE = "__Host-changeguard"  # Loopback fixtures use a separate non-__Host cookie.
OBSOLETE = {"/api/getSpeechToken", "/api/speech/token", "/api/token", "/api/getIceToken"}
LOG = logging.getLogger("changeguard.web")


def create_app(settings: Settings | None = None, provider: Provider | None = None) -> Flask:
    settings = settings or Settings.from_env()
    settings.validate()
    app = Flask(__name__, static_folder=str(ROOT / "static"), template_folder=str(ROOT / "templates"))
    app.config.update(
        MAX_CONTENT_LENGTH=65536,
        TRUSTED_HOSTS=[urlsplit(settings.origin).hostname],
    )
    logging.getLogger("azure").setLevel(logging.ERROR)
    manager = SessionManager(
        settings,
        provider
        if provider is not None
        else (AzureProvider(settings) if settings.mode == "live-azure" else FixtureProvider()),
    )
    app.extensions["changeguard"] = manager
    atexit.register(manager.close)
    signer = URLSafeTimedSerializer(settings.signing_key, salt="changeguard-operator-v1")
    cookie = COOKIE if settings.secure_cookie else "changeguard-local"
    login_attempts: deque[float] = deque()
    login_lock = threading.Lock()

    def owner() -> str | None:
        value = request.cookies.get(cookie)
        if not value:
            return None
        try:
            result = signer.loads(value, max_age=1800)
            return result if isinstance(result, str) else None
        except (BadSignature, SignatureExpired):
            return None

    def authenticated(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            g.operator = owner()
            if g.operator is None:
                raise PublicError("operator-authentication-required", 401)
            return function(*args, **kwargs)

        return wrapper

    def payload(*keys: str) -> dict:
        if not request.is_json:
            raise PublicError("json-body-required", 415)
        body = request.get_json()
        if not isinstance(body, dict) or set(body) != set(keys):
            raise PublicError("invalid-request-shape")
        return body

    def session_id(body: dict) -> str:
        value = body["sessionId"]
        if not isinstance(value, str) or not 16 <= len(value) <= 64:
            raise PublicError("invalid-session-id")
        return value

    @app.before_request
    def boundary():
        if request.path.rstrip("/") in OBSOLETE:
            raise PublicError("not-found", 404)
        if request.path.startswith("/api/"):
            if request.query_string:
                raise PublicError("query-parameters-not-accepted")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                if request.headers.get("Origin") != settings.origin:
                    raise PublicError("same-origin-required", 403)
                if request.headers.get("X-ChangeGuard-Request") != "1":
                    raise PublicError("request-marker-required", 403)

    @app.after_request
    def response_boundary(response):
        if request.path.startswith("/api/") and response.is_json:
            findings = scan_boundary(response.get_data(as_text=True), settings.forbidden_values)
            if findings:
                LOG.error("response-boundary-rejected categories=%s", ",".join(findings))
                response = jsonify(error="response-boundary-rejected")
                response.status_code = 502
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                    "form-action 'self'"
                ),
            }
        )
        if settings.secure_cookie:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(PublicError)
    def known_error(exc):
        LOG.warning("request-denied code=%s status=%s", exc.code, exc.status)
        return jsonify(error=exc.code), exc.status

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error=f"http-{exc.code}"), exc.code

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        # Tracebacks and exception text may contain SDK credentials or SDP.
        LOG.error("unexpected-server-error type=%s", type(exc).__name__)
        return jsonify(error="internal-error"), 500

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify(
            status="ok",
            applicationId="changeguard",
            executionMode=settings.mode,
            sourceCommit=settings.source_commit,
            sourceDigest=settings.source_digest,
            avatarReadiness="not-proven-by-health-check",
        )

    @app.get("/api/manifest")
    def manifest():
        return jsonify(
            schemaVersion=1,
            applicationId="changeguard",
            name="ChangeGuard",
            executionMode=settings.mode,
            sourceCommit=settings.source_commit,
            sourceDigest=settings.source_digest,
            requirementId="CG-001",
            authentication="operator-required-for-all-avatar-operations",
            limits={"sessionSeconds": settings.session_seconds, "concurrentSessions": 1},
            boundary={
                "azureServiceCredentials": "server-only",
                "armIdentifiers": "server-only",
                "webRtcTransportCredentials": "browser-visible-when-authenticated",
                "media": "browser-to-azure-webrtc",
            },
            approval={"state": "pending", "actorType": "none", "reason": "Human review required"},
        )

    @app.get("/api/evidence")
    def evidence():
        path = ROOT / "static" / "examples" / "recorded-session.json"
        return jsonify(json.loads(path.read_text()))

    @app.get("/api/workflow")
    def workflow():
        requirement = json.loads((ROOT / "workflow" / "requirement.json").read_text())
        plan = json.loads((ROOT / "workflow" / "architecture.json").read_text())
        return jsonify(
            requirement=requirement,
            gate={
                "automatedArchitecturePassed": not architecture_gate(plan),
                "humanApproval": "pending",
                "productionRelease": "not-authorized",
            },
        )

    @app.get("/api/comparison")
    def comparison():
        return jsonify(synthetic_comparison())

    @app.get("/api/operator")
    def operator_status():
        return jsonify(authenticated=owner() is not None, executionMode=settings.mode)

    @app.post("/api/operator/login")
    def login():
        if not settings.operator_hash:
            raise PublicError("operator-access-unavailable-in-preview", 409)
        body = payload("token")
        with login_lock:
            now = time.monotonic()
            while login_attempts and now - login_attempts[0] >= 60:
                login_attempts.popleft()
            if len(login_attempts) >= 10:
                raise PublicError("operator-login-rate-limited", 429)
            login_attempts.append(now)
        token = body["token"]
        if (
            not isinstance(token, str)
            or not 32 <= len(token) <= 256
            or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), settings.operator_hash)
        ):
            raise PublicError("operator-authentication-failed", 401)
        response = jsonify(authenticated=True)
        response.set_cookie(
            cookie,
            signer.dumps(secrets.token_urlsafe(24)),
            max_age=1800,
            secure=settings.secure_cookie,
            httponly=True,
            samesite="Strict",
            path="/",
        )
        return response

    @app.post("/api/operator/logout")
    @authenticated
    def logout():
        with manager.lock:
            active = manager.active
            active_id = active.id if active and active.owner == g.operator else None
        if active_id:
            manager.stop(g.operator, active_id)
        response = jsonify(authenticated=False)
        response.delete_cookie(
            cookie, secure=settings.secure_cookie, httponly=True, samesite="Strict", path="/"
        )
        return response

    @app.get("/api/avatar/status")
    @authenticated
    def avatar_status():
        return jsonify(manager.status(g.operator))

    @app.post("/api/avatar/prepare")
    @authenticated
    def prepare():
        payload()
        return jsonify(manager.prepare(g.operator))

    @app.post("/api/avatar/connect")
    @authenticated
    def connect():
        body = payload("sessionId", "offer")
        offer = body["offer"]
        if (
            not isinstance(offer, dict)
            or set(offer) != {"type", "sdp"}
            or offer["type"] != "offer"
            or not isinstance(offer["sdp"], str)
            or not offer["sdp"].startswith("v=0")
            or not 20 <= len(offer["sdp"]) <= 48000
            or "m=video" not in offer["sdp"]
            or "m=audio" not in offer["sdp"]
        ):
            raise PublicError("invalid-sdp-offer")
        return jsonify(manager.connect(g.operator, session_id(body), offer))

    @app.post("/api/avatar/speak")
    @authenticated
    def speak():
        body = payload("sessionId", "text")
        text = body["text"]
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 280:
            raise PublicError("speech-text-must-be-1-to-280-characters")
        manager.speak(g.operator, session_id(body), text)
        return jsonify(synthesized=True)

    @app.post("/api/avatar/stop")
    @authenticated
    def stop():
        body = payload("sessionId")
        manager.stop(g.operator, session_id(body))
        return jsonify(closed=True)

    return app
