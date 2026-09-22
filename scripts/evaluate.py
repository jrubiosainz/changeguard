import argparse
import hashlib
import json
import os
import platform
import sys
import tempfile
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

from common import ROOT, azure, command, secret, source_commit, write_json
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(ROOT))
from browser_checks import (  # noqa: E402
    LEGACY_CLONE_SCRIPT,
    clone_matches_response,
    inspect_clone,
    inspect_response,
    safe_diagnostic,
    wait_for_environment,
    wait_for_video,
)

from changeguard.evidence import audio_increased, check, metric, playback_metrics  # noqa: E402
from changeguard.policy import scan_boundary, synthetic_comparison  # noqa: E402
from changeguard.provenance import source_digest  # noqa: E402

HEADERS = {"X-ChangeGuard-Request": "1"}


def provisioned_identifiers(subscription: str, group: str) -> tuple[str, ...]:
    if not subscription or not group:
        raise RuntimeError("Live boundary verification requires the explicit scoped subscription")
    deployment = azure(["deployment", "group", "show", "-g", group, "-n", "changeguard-v1"], subscription)
    outputs = deployment["properties"]["outputs"]
    name = outputs["speechName"]["value"]
    account = azure(
        [
            "cognitiveservices",
            "account",
            "show",
            "-g",
            group,
            "-n",
            name,
            "--query",
            "{id:id,host:properties.customSubDomainName}",
        ],
        subscription,
    )
    identity = azure(["account", "show", "--query", "{tenant:tenantId}"], subscription)
    return (
        subscription,
        group,
        name,
        account["id"],
        f"{account['host']}.cognitiveservices.azure.com",
        identity["tenant"],
    )


def main():
    parser = argparse.ArgumentParser(
        description="One explicitly authorized, bounded Azure avatar run; no automatic retries."
    )
    parser.add_argument("--url")
    parser.add_argument("--subscription", default=os.environ.get("AZURE_SUBSCRIPTION_ID"))
    parser.add_argument("--group", default=os.environ.get("AZURE_RESOURCE_GROUP", "rg-changeguard"))
    parser.add_argument("--record", action="store_true")
    parser.add_argument(
        "--allow-billable", action="store_true", help="Explicitly authorize one paid avatar run"
    )
    parser.add_argument("--output", default=".runtime/evidence/live-run.json")
    parser.add_argument("--browser", choices=["chromium", "webkit"], default="webkit")
    parser.add_argument("--channel", default="chromium", help="Full Chromium/new headless by default")
    args = parser.parse_args()
    if not args.allow_billable:
        parser.error("Live evaluation requires --allow-billable; offline preview is scripts/run_local.py")
    deployment = json.loads((ROOT / ".runtime/deployment.json").read_text())
    origin = (args.url or deployment["url"]).rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or parsed.query or parsed.fragment or parsed.path:
        raise RuntimeError("Live evaluation needs a query-free HTTPS origin")
    # Never send the local operator credential to an arbitrary --url destination.
    if origin != deployment["url"]:
        raise RuntimeError("Origin must match the verified scoped deployment")
    forbidden = provisioned_identifiers(args.subscription, args.group)
    evaluator_commit = source_commit()
    commit = deployment["sourceCommit"]
    if command(["git", "status", "--porcelain", "--", str(ROOT / "scripts")]).strip():
        raise RuntimeError("Commit the evaluator before collecting persistent live evidence")
    now = datetime.now(UTC)
    run_id = f"live-{now.strftime('%Y%m%dT%H%M%SZ')}"
    target = (ROOT / args.output).resolve()
    if not target.is_relative_to((ROOT / ".runtime").resolve()):
        raise RuntimeError("Generated evidence must remain inside the untracked .runtime directory")
    captures = ROOT / ".runtime/evidence/captures" / run_id
    captures.mkdir(parents=True, exist_ok=True)
    observations = {
        "responses": 0,
        "headers": 0,
        "bodyless": 0,
        "bytes": 0,
        "urls": 0,
        "azureSockets": 0,
        "scanErrors": 0,
    }
    inspection_errors = []
    findings: set[str] = set()
    checks: list[dict] = []
    artifacts = [
        {"label": "Requirement fixture", "path": "workflow/requirement.json"},
    ]
    before: dict = {}
    after: dict = {}
    native: dict = {}
    screenshot_path = captures / "live-avatar.png"
    media_path = captures / "live-avatar-media.webm"
    ui_path = captures / "live-avatar-ui.webm"
    failure = None
    close_confirmed = False
    human_gate_pending = False
    elapsed = 0.0
    started = time.monotonic()
    phase = "browser-launch"
    browser_version = None
    capture_mime_type = None

    with tempfile.TemporaryDirectory(prefix="changeguard-browser-recording-") as temporary:
        with sync_playwright() as playwright:
            if args.browser == "chromium":
                browser = playwright.chromium.launch(
                    headless=True,
                    channel=args.channel,
                    chromium_sandbox=True,
                    timeout=20000,
                )
            else:
                browser = playwright.webkit.launch(headless=True, timeout=20000)
            browser_version = browser.version
            context = browser.new_context(
                viewport={"width": 1440, "height": 1040},
                record_video_dir=temporary if args.record else None,
                record_video_size={"width": 1440, "height": 1040} if args.record else None,
            )
            headers = {**HEADERS, "Origin": origin}
            anonymous = playwright.request.new_context(base_url=origin, extra_http_headers=headers)
            page = None
            media_started = False
            legacy_clone = None
            legacy_response = None
            try:
                phase = "deployed-identity-check"
                health = anonymous.get("/api/health").json()
                if health.get("executionMode") != "live-azure":
                    raise RuntimeError("Refusing to call a local fixture live evidence")
                if health.get("sourceCommit") != commit:
                    raise RuntimeError("Server source commit differs from the verified deployment")
                if health.get("sourceDigest") != source_digest(ROOT):
                    raise RuntimeError("Server runtime source digest differs from the local source")
                manifest = anonymous.get("/api/manifest").json()
                human_gate_pending = manifest.get("approval") == {
                    "state": "pending",
                    "actorType": "none",
                    "reason": "Human review required",
                }
                login = context.request.post(
                    f"{origin}/api/operator/login",
                    headers=headers,
                    data={"token": secret("operator")},
                )
                if login.status != 200:
                    raise RuntimeError("Operator authentication failed before recording")
                protected = [
                    anonymous.post(path, data={}).status
                    for path in (
                        "/api/avatar/prepare",
                        "/api/avatar/connect",
                        "/api/avatar/speak",
                        "/api/avatar/stop",
                    )
                ]
                cross_origin = context.request.post(
                    f"{origin}/api/avatar/prepare",
                    headers={**HEADERS, "Origin": "https://untrusted.invalid"},
                    data={},
                ).status
                checks.append(
                    check(
                        "operator-protection",
                        protected == [401] * 4 and cross_origin == 403,
                        "All four anonymous avatar operations returned 401; authenticated "
                        "cross-origin prepare returned 403. These requests never start a session.",
                    )
                )
                obsolete = []
                for path in (
                    "/api/getSpeechToken",
                    "/api/speech/token",
                    "/api/token",
                    "/api/getIceToken",
                ):
                    for method in ("GET", "POST"):
                        response = context.request.fetch(f"{origin}{path}", method=method, headers=headers)
                        obsolete.append(response.status)
                        findings.update(scan_boundary(response.text(), forbidden))
                checks.append(
                    check(
                        "obsolete-token-routes",
                        obsolete == [404] * 8,
                        "Four obsolete credential routes returned 404 for authenticated GET and POST.",
                    )
                )
                page = context.new_page()

                def on_legacy_clone(source, payload):
                    nonlocal legacy_clone
                    legacy_clone = inspect_clone(payload, forbidden)
                    findings.update(legacy_clone.findings)

                page.expose_binding("__changeguardLegacyObservation", on_legacy_clone)
                page.add_init_script(LEGACY_CLONE_SCRIPT)

                def on_response(response):
                    nonlocal legacy_response
                    if not response.url.startswith(origin + "/"):
                        return
                    if urlsplit(response.url).path == "/api/getSpeechToken":
                        observations["headers"] += 1
                        findings.update(
                            scan_boundary(response.url + "\n" + json.dumps(response.headers), forbidden)
                        )
                        legacy_response = (
                            urlsplit(response.url).path,
                            response.request.method,
                            response.status,
                        )
                        return
                    inspection = inspect_response(response, forbidden)
                    observations["headers"] += 1
                    findings.update(inspection.findings)
                    if inspection.error:
                        observations["scanErrors"] += 1
                        inspection_errors.append(
                            {
                                "path": inspection.path,
                                "method": inspection.method,
                                "status": inspection.status,
                                "error": inspection.error,
                            }
                        )
                        return
                    if inspection.coverage == "body-and-headers":
                        observations["responses"] += 1
                        observations["bytes"] += inspection.body_bytes
                    elif inspection.coverage == "protocol-no-body":
                        observations["bodyless"] += 1

                def on_request(request):
                    observations["urls"] += 1
                    findings.update(scan_boundary(request.url, forbidden))

                def on_console(message):
                    findings.update(scan_boundary(message.text, forbidden))

                def on_websocket(socket):
                    host = urlsplit(socket.url).hostname or ""
                    if host.endswith((".speech.microsoft.com", ".cognitiveservices.azure.com")):
                        observations["azureSockets"] += 1
                    findings.update(scan_boundary(socket.url, forbidden))

                page.on("response", on_response)
                page.on("request", on_request)
                page.on("console", on_console)
                page.on("websocket", on_websocket)
                phase = "public-page-readiness"
                page.goto(origin, wait_until="domcontentloaded")
                wait_for_environment(page, "AZURE")
                page.locator("html[data-changeguard-legacy-observed='true']").wait_for(timeout=15000)
                if not page.evaluate("window.changeguard.readStats()")["closed"]:
                    raise RuntimeError("Fresh browser unexpectedly reports an active avatar")
                phase = "synthetic-comparison"
                page.get_by_test_id("run-comparison").click()
                page.get_by_test_id("comparison-results").get_by_text(
                    "wrong-fix-base64-mask", exact=True
                ).wait_for()
                page.get_by_test_id("start-avatar").scroll_into_view_if_needed()
                phase = "bounded-avatar-start"
                page.get_by_test_id("start-avatar").click()
                before = wait_for_video(page)
                phase = "actual-media-capture"
                if args.record:
                    supported = page.evaluate("""() => [
                        'video/webm;codecs=vp8,opus', 'video/webm', 'video/mp4'
                    ].filter(type => MediaRecorder.isTypeSupported(type))""")
                    if not supported:
                        raise RuntimeError("This browser cannot record the actual remote media stream")
                    capture_mime_type = supported[0]
                    media_path = captures / (
                        "live-avatar-media.mp4"
                        if capture_mime_type.startswith("video/mp4")
                        else "live-avatar-media.webm"
                    )
                    page.evaluate(
                        """mimeType => {
                        const tracks = [
                          ...document.querySelector('#avatar-video').srcObject.getVideoTracks(),
                          ...document.querySelector('#avatar-audio').srcObject.getAudioTracks(),
                        ];
                        const stream = new MediaStream(tracks);
                        window.__cgRecordingChunks = [];
                        window.__cgRecorder = new MediaRecorder(stream, {mimeType});
                        window.__cgRecorder.ondataavailable = e => {
                          if (e.data.size) window.__cgRecordingChunks.push(e.data);
                        };
                        window.__cgRecorder.start(250);
                    }""",
                        capture_mime_type,
                    )
                    media_started = True
                with page.expect_response(lambda r: r.url.endswith("/api/avatar/speak")) as spoken:
                    page.get_by_test_id("speak-avatar").click()
                checks.append(
                    check(
                        "speech-synthesis",
                        spoken.value.status == 200,
                        "Authenticated server-side speech request completed on the real avatar session.",
                    )
                )
                page.wait_for_timeout(10500)
                after = page.evaluate("window.changeguard.readStats()")
                native = page.evaluate("""() => {
                    const video = document.querySelector('#avatar-video');
                    return {
                      width: video.videoWidth, height: video.videoHeight,
                      readyState: video.readyState,
                      frames: video.getVideoPlaybackQuality().totalVideoFrames,
                      currentTime: video.currentTime,
                    };
                }""")
                page.screenshot(path=str(screenshot_path), full_page=False)
                artifacts.append(
                    {
                        "label": "Actual live browser screenshot",
                        "path": str(screenshot_path.relative_to(ROOT)),
                    }
                )
                if media_started:
                    with page.expect_download() as download:
                        page.evaluate(
                            """async filename => {
                            const recorder = window.__cgRecorder;
                            await new Promise(resolve => {
                              recorder.onstop = resolve;
                              recorder.stop();
                            });
                            const blob = new Blob(window.__cgRecordingChunks, {type: recorder.mimeType});
                            const url = URL.createObjectURL(blob);
                            const link = document.createElement('a');
                            link.href = url;
                            link.download = filename;
                            link.click();
                            setTimeout(() => URL.revokeObjectURL(url), 10000);
                        }""",
                            media_path.name,
                        )
                    download.value.save_as(media_path)
                    artifacts.append(
                        {
                            "label": "Real Azure video/audio, MediaRecorder capture",
                            "path": str(media_path.relative_to(ROOT)),
                        }
                    )
                checks.extend(
                    [
                        check(
                            "actual-video-playback",
                            after["decodedFrames"] > before["decodedFrames"] + 10
                            and native["width"] >= 640
                            and native["height"] >= 360
                            and native["frames"] > 10
                            and native["readyState"] >= 2
                            and after["renderedFrames"] > before["renderedFrames"],
                            "Native browser video dimensions and playback-quality frames plus "
                            "increasing inbound-RTP frames and rendered-frame callbacks; not HTTP 200.",
                        ),
                        check(
                            "inbound-audio",
                            audio_increased(before, after),
                            "Inbound audio bytes increased while the real service synthesized the demo line.",
                        ),
                    ]
                )
            except (BrowserError, RuntimeError, ValueError, KeyError) as exc:
                failure = {
                    "type": type(exc).__name__,
                    "phase": phase,
                    "diagnostic": safe_diagnostic(exc, forbidden),
                }
                if page is not None and not page.is_closed():
                    try:
                        public_message = page.get_by_test_id("session-message").text_content(timeout=3000)
                    except BrowserError:
                        failure["publicMessage"] = "Browser message unavailable after failure."
                    else:
                        if public_message and not scan_boundary(public_message, forbidden):
                            failure["publicMessage"] = public_message[:350]
                checks.append(
                    check(
                        "live-evaluation-completed",
                        False,
                        f"Run failed with {type(exc).__name__}; raw SDK/HTTP data was not retained.",
                    )
                )
            finally:
                if page is not None:
                    try:
                        page.evaluate("window.changeguard.stop()")
                        page.wait_for_timeout(500)
                    except BrowserError:
                        checks.append(
                            check(
                                "browser-close-request",
                                False,
                                "Browser close failed; independent server deadline remains enforced.",
                            )
                        )
                status = context.request.get(f"{origin}/api/avatar/status", headers=headers)
                close_confirmed = status.status == 200 and status.json().get("active") is False
                elapsed = time.monotonic() - started
                if page is not None and args.record:
                    video = page.video
                    page.close()
                    video.save_as(ui_path)
                    artifacts.append(
                        {
                            "label": "Actual browser interaction recording (screen capture; no audio)",
                            "path": str(ui_path.relative_to(ROOT)),
                        }
                    )
                context.close()
                anonymous.dispose()
                browser.close()
            if clone_matches_response(legacy_clone, legacy_response):
                observations["responses"] += 1
                observations["bytes"] += legacy_clone.body_bytes
            else:
                observations["scanErrors"] += 1
                inspection_errors.append(
                    {
                        "path": "/api/getSpeechToken",
                        "method": "GET",
                        "status": legacy_response[2] if legacy_response else 0,
                        "error": "matching-same-response-clone-not-available",
                    }
                )

    checks.extend(
        [
            check(
                "browser-credential-boundary",
                not findings and observations["responses"] > 0 and observations["scanErrors"] == 0,
                f"Scanned {observations['responses']} response bodies and {observations['headers']} headers; "
                f"{observations['urls']} browser URLs and console events. Raw, URL/base64/JWT patterns "
                "and actual provisioned identifiers were checked in memory; none retained in artifacts."
                f" Inspection errors: {observations['scanErrors']}; "
                f"protocol-bodyless responses: {observations['bodyless']}."
                f" Findings: {', '.join(sorted(findings)) or 'none'}.",
            ),
            check(
                "no-browser-azure-auth-websocket",
                observations["azureSockets"] == 0,
                "Browser observed zero Speech-service authentication WebSockets; server owns signaling.",
            ),
            check(
                "server-session-closed",
                close_confirmed,
                "Authenticated status confirmed no remaining owned server session after explicit closure.",
            ),
            check(
                "synthetic-wrong-fix-rejected",
                not synthetic_comparison()["cases"][1]["passed"],
                "Separate SYNTHETIC negative control: masking the same invented credential still fails.",
            ),
            check(
                "human-review-remains-pending",
                human_gate_pending,
                "Automation does not approve or merge the PR; approval is still pending human review.",
            ),
        ]
    )
    for identifier in ("actual-video-playback", "inbound-audio", "speech-synthesis"):
        if not any(item["id"] == identifier for item in checks):
            checks.append(check(identifier, False, "Not reached; no successful live claim is made."))
    report = {
        "schemaVersion": 1,
        "applicationId": "changeguard",
        "runId": run_id,
        "generatedAt": datetime.now(UTC).isoformat(),
        "sourceCommit": commit,
        "evaluatorCommit": evaluator_commit,
        "evaluatorSourceSha256": hashlib.sha256((ROOT / "scripts/evaluate.py").read_bytes()).hexdigest(),
        "browser": {
            "engine": args.browser,
            "version": browser_version,
            "location": f"local-{platform.system().lower()}",
        },
        "captureMimeType": capture_mime_type,
        "runtimeSourceSha256": source_digest(ROOT),
        "executionMode": "live-azure",
        "execution": {"mode": "live-azure", "attempts": 1, "automaticRetries": 0},
        "metrics": [
            *playback_metrics(after, native),
            metric(
                "evaluation_duration",
                round(elapsed, 2),
                "seconds",
                "Monotonic wall time of one run including auth, checks and session closure",
            ),
            metric(
                "browser_response_count",
                observations["responses"],
                "responses",
                "Actual Playwright browser response observations",
            ),
            metric(
                "browser_boundary_findings",
                len(findings),
                "categories",
                "In-memory scanning; service credentials/identifiers distinguished from transport",
            ),
            metric(
                "response_inspection_errors",
                observations["scanErrors"],
                "responses",
                "Uninspectable expected bodies fail the boundary check; never treated as zero leakage",
            ),
            metric(
                "protocol_bodyless_responses",
                observations["bodyless"],
                "responses",
                "Only HEAD, 204/205/304 and redirects; headers and URLs still scanned",
            ),
            metric(
                "azure_auth_websockets_in_browser",
                observations["azureSockets"],
                "connections",
                "Actual Playwright websocket observations, not inferred from HTTP",
            ),
        ],
        "checks": checks,
        "approval": {"state": "pending", "actorType": "none", "reason": "Human review required"},
        "artifacts": artifacts,
        "limitations": [
            f"One bounded {args.browser}/network/region run, not an exhaustive audit or availability SLO.",
            "Boundary scanner covers responses, URLs and console, not all browser memory.",
            "SDP and ephemeral TURN/ICE transport credentials and media network addresses remain visible.",
            "A synthetic baseline is not a live before/after performance or security comparison.",
            "Video/audio stream capture is real service output; UI screen recording itself is silent.",
        ],
        "failure": failure,
        "responseInspectionErrors": inspection_errors,
        "legacyProbeBodySource": (
            "Actual browser fetch response.clone().text(), unchanged original response returned to app; "
            "paired with observed URL/method/status. No second request or fabricated fulfillment."
        ),
    }
    if scan_boundary(json.dumps(report), forbidden):
        raise RuntimeError("Refusing to persist an evidence report containing protected identifiers")
    write_json(ROOT / ".runtime/evidence/runs" / f"{run_id}.json", report)
    write_json(target, report)
    passed = all(item["status"] == "pass" for item in checks)
    print(f"{run_id}: {'PASS' if passed else 'FAIL'}; sanitized evidence: {args.output}")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
