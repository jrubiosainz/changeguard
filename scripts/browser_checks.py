import json
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from playwright.sync_api import Error as BrowserError

from changeguard.policy import scan_boundary

LEGACY_CLONE_SCRIPT = """
(() => {
  const original = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await original(...args);
    const input = args[0] instanceof Request ? args[0].url : String(args[0]);
    const requested = new URL(input, location.href);
    if (requested.origin === location.origin && requested.pathname === '/api/getSpeechToken') {
      const method = args[1]?.method || (args[0] instanceof Request ? args[0].method : 'GET');
      const observation = {
        url: response.url, method, status: response.status,
        headers: Object.fromEntries(response.headers.entries())
      };
      try {
        observation.body = await response.clone().text();
      } catch (error) {
        observation.error = 'same-response-clone-unavailable';
      }
      await window.__changeguardLegacyObservation(observation);
      document.documentElement.dataset.changeguardLegacyObserved = 'true';
    }
    return response;
  };
})();
"""


@dataclass(frozen=True)
class ResponseInspection:
    path: str
    method: str
    status: int
    findings: list[str]
    coverage: str
    body_bytes: int = 0
    error: str | None = None


def inspect_response(response, forbidden: tuple[str, ...]) -> ResponseInspection:
    path = urlsplit(response.url).path
    method, status = response.request.method, response.status
    headers = response.headers
    findings = scan_boundary(response.url + "\n" + json.dumps(headers), forbidden)
    if method == "HEAD" or status in {204, 205, 304, 301, 302, 303, 307, 308}:
        return ResponseInspection(path, method, status, findings, "protocol-no-body")
    content_type = headers.get("content-type", "")
    if not any(value in content_type for value in ("json", "text", "javascript")):
        if path.startswith("/api/"):
            return ResponseInspection(
                path,
                method,
                status,
                findings,
                "uninspected",
                error="unexpected-api-content-type",
            )
        return ResponseInspection(path, method, status, findings, "binary-asset-headers-only")
    try:
        text = response.text()
    except BrowserError:
        return ResponseInspection(
            path, method, status, findings, "uninspected", error="response-body-unavailable"
        )
    findings = sorted(set(findings + scan_boundary(text, forbidden)))
    return ResponseInspection(path, method, status, findings, "body-and-headers", len(text.encode()))


def wait_for_environment(page, label: str) -> None:
    page.get_by_test_id("environment").filter(has_text=label).wait_for(timeout=15000)


def wait_for_video(page, *, timeout: float = 65, clock=time.monotonic) -> dict:
    deadline = clock() + timeout
    while clock() < deadline:
        stats = page.evaluate("window.changeguard.readStats()")
        if stats["decodedFrames"] >= 10 and stats["renderedFrames"] >= 3:
            return stats
        if stats["closed"]:
            page.evaluate("window.changeguard.stop()")
            raise RuntimeError("Avatar ended before actual decoded video readiness")
        page.wait_for_timeout(250)
    raise RuntimeError("Actual decoded video readiness deadline exceeded")


def safe_diagnostic(error: Exception, forbidden: tuple[str, ...]) -> str:
    message = str(error).splitlines()[0][:600]
    if scan_boundary(message, forbidden) or re.search(
        r"cookie|authorization|password|a=ice-|a=candidate:", message, re.I
    ):
        return "Sensitive diagnostic withheld; inspect the safe phase and error type."
    return message


def inspect_clone(payload: dict, forbidden: tuple[str, ...]) -> ResponseInspection:
    path = urlsplit(payload["url"]).path
    method, status = payload["method"], payload["status"]
    findings = scan_boundary(payload["url"] + "\n" + json.dumps(payload["headers"]), forbidden)
    if payload.get("error") or not isinstance(payload.get("body"), str):
        return ResponseInspection(
            path, method, status, findings, "uninspected", error="same-response-clone-unavailable"
        )
    body = payload["body"]
    findings = sorted(set(findings + scan_boundary(body, forbidden)))
    return ResponseInspection(path, method, status, findings, "same-response-fetch-clone", len(body.encode()))


def clone_matches_response(clone: ResponseInspection | None, observed: tuple | None) -> bool:
    return clone is not None and clone.error is None and observed == (clone.path, clone.method, clone.status)
