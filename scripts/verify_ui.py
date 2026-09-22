import secrets
import sys
import tempfile
import threading
from datetime import UTC, datetime
from urllib.request import urlopen

from common import ROOT, source_commit, write_json
from playwright.sync_api import sync_playwright
from werkzeug.serving import WSGIRequestHandler, make_server

sys.path.insert(0, str(ROOT))
from browser_checks import wait_for_environment  # noqa: E402

from changeguard.settings import Settings  # noqa: E402
from changeguard.web import create_app  # noqa: E402


class QuietHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        pass


def main():
    with tempfile.TemporaryDirectory(prefix="changeguard-ui-") as data:
        settings = Settings(
            mode="local-fixture",
            origin="http://127.0.0.1:8033",
            operator_hash="",
            signing_key=secrets.token_urlsafe(48),
            source_commit=source_commit(),
            data_dir=data,
            secure_cookie=False,
        )
        app = create_app(settings)
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        with urlopen(origin + "/api/health", timeout=5) as response:
            assert response.status == 200
        checks = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.webkit.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1040})
                errors = []
                page.on("pageerror", lambda error: errors.append(type(error).__name__))
                page.goto(origin, wait_until="domcontentloaded")
                wait_for_environment(page, "LOCAL")
                assert page.get_by_test_id("start-avatar").is_disabled()
                assert "LOCAL / OFFLINE PREVIEW" in page.get_by_test_id("environment").inner_text()
                assert page.get_by_test_id("operator-button").is_hidden()
                assert page.locator("#recorded-label").inner_text() == "RECORDED EXAMPLE / NOT LIVE"
                assert page.get_by_test_id("frames").inner_text() == "--"
                assert page.evaluate("window.changeguard.readStats()")["closed"]
                page.get_by_test_id("run-comparison").click()
                page.get_by_text("wrong-fix-base64-mask", exact=True).wait_for()
                assert page.get_by_test_id("comparison-results").inner_text().count("REJECTED") == 2
                assert "PENDING" in page.get_by_test_id("review-gate").inner_text()
                for width in (1440, 390):
                    page.set_viewport_size({"width": width, "height": 1040})
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
                        f"Horizontal overflow at {width}px"
                    )
                    checks.append(
                        {
                            "id": f"layout-{width}",
                            "status": "pass",
                            "details": f"WebKit offline UI fits {width}px without horizontal overflow.",
                        }
                    )
                page.set_viewport_size({"width": 1440, "height": 1040})
                page.goto(origin, wait_until="domcontentloaded")
                wait_for_environment(page, "LOCAL")
                capture = ROOT / ".runtime/screenshots/offline-preview.png"
                capture.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(capture), full_page=False)
                page.locator("#live-demo").scroll_into_view_if_needed()
                media_capture = capture.with_name("avatar-workspace.png")
                page.screenshot(path=str(media_capture), full_page=False)
                assert not errors, "Browser page errors occurred"
                browser.close()
        finally:
            server.shutdown()
            thread.join(5)
            app.extensions["changeguard"].close()
        report = {
            "schemaVersion": 1,
            "applicationId": "changeguard",
            "runId": "ui-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            "generatedAt": datetime.now(UTC).isoformat(),
            "sourceCommit": settings.source_commit,
            "executionMode": "local-fixture",
            "metrics": [
                {
                    "name": "viewports_checked",
                    "value": 2,
                    "unit": "viewports",
                    "kind": "measured",
                    "method": "Actual headless WebKit over a loopback-only offline server",
                }
            ],
            "checks": checks
            + [
                {
                    "id": "read-only-fixture-behavior",
                    "status": "pass",
                    "details": "Live start disabled, synthetic wrong fix rejected, human gate pending, "
                    "no page errors. No Azure operation was performed.",
                }
            ],
            "approval": {"state": "pending", "actorType": "none", "reason": "Human review required"},
            "artifacts": [
                {
                    "label": "Local fixture UI screenshot, NOT Azure media",
                    "path": ".runtime/screenshots/offline-preview.png",
                },
                {
                    "label": "Offline workspace showing a labeled recorded still, not new Azure media",
                    "path": ".runtime/screenshots/avatar-workspace.png",
                },
            ],
        }
        write_json(ROOT / ".runtime/ui-checks.json", report)
        print("Read-only WebKit UI passed at 1440px and 390px; no Azure calls made.")


if __name__ == "__main__":
    main()
