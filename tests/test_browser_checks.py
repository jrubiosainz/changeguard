from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as BrowserError

from scripts.browser_checks import (
    clone_matches_response,
    inspect_clone,
    inspect_response,
    safe_diagnostic,
    wait_for_environment,
    wait_for_video,
)


class ResponseDouble:
    def __init__(self, *, method="GET", status=200, headers=None, body="{}", failure=False):
        self.url = "https://demo.invalid/api/health"
        self.request = SimpleNamespace(method=method)
        self.status = status
        self.headers = headers or {"content-type": "application/json"}
        self.body = body
        self.failure = failure
        self.reads = 0

    def text(self):
        self.reads += 1
        if self.failure:
            raise BrowserError("Response body unavailable")
        return self.body


@pytest.mark.parametrize(
    "method,status",
    [
        ("HEAD", 200),
        ("GET", 204),
        ("GET", 205),
        ("GET", 304),
        ("GET", 301),
        ("GET", 302),
        ("GET", 303),
        ("GET", 307),
        ("GET", 308),
    ],
)
def test_only_protocol_bodyless_responses_skip_body(method, status):
    response = ResponseDouble(method=method, status=status, failure=True)
    result = inspect_response(response, ())
    assert result.coverage == "protocol-no-body"
    assert result.error is None and response.reads == 0


def test_bodyless_headers_are_still_checked():
    response = ResponseDouble(
        status=302, headers={"location": "https://invalid.example/?Authorization=SYNTHETIC"}
    )
    assert "credential-query" in inspect_response(response, ()).findings


@pytest.mark.parametrize("status", [200, 400, 401, 404, 500])
def test_failed_expected_inspection_never_becomes_clean_evidence(status):
    result = inspect_response(ResponseDouble(status=status, failure=True), ())
    assert result.error == "response-body-unavailable"
    assert result.coverage == "uninspected"


def test_unknown_api_mime_does_not_bypass_inspection():
    result = inspect_response(ResponseDouble(headers={"content-type": "application/octet-stream"}), ())
    assert result.error == "unexpected-api-content-type"


def test_normal_body_identifiers_are_detected():
    result = inspect_response(
        ResponseDouble(body='{"value":"actual-fixture-identifier"}'), ("actual-fixture-identifier",)
    )
    assert result.findings == ["actual-provisioned-identifier"]


def test_csp_error_is_preserved_but_sensitive_diagnostics_are_not():
    error = BrowserError("EvalError: unsafe-eval blocked by script-src 'self'")
    assert "unsafe-eval" in safe_diagnostic(error, ())
    assert "withheld" in safe_diagnostic(BrowserError("a=ice-pwd:synthetic-transport"), ())


class PageDouble:
    def __init__(self, stats=None):
        self.stats = iter(stats or [])
        self.waited = False

    def get_by_test_id(self, identifier):
        assert identifier == "environment"
        return self

    def filter(self, *, has_text):
        assert has_text == "AZURE"
        return self

    def wait_for(self, *, timeout):
        self.waited = True

    def wait_for_function(self, *args, **kwargs):
        raise AssertionError("unsafe-eval-based wait must not be used under the deployed CSP")

    def evaluate(self, expression):
        if expression == "window.changeguard.stop()":
            return True
        assert expression == "window.changeguard.readStats()"
        return next(self.stats)

    def wait_for_timeout(self, milliseconds):
        assert milliseconds == 250


def test_waits_use_locators_and_native_stat_polling_not_string_function_eval():
    page = PageDouble(
        [
            {"decodedFrames": 0, "renderedFrames": 0, "closed": False},
            {"decodedFrames": 20, "renderedFrames": 15, "closed": False},
        ]
    )
    wait_for_environment(page, "AZURE")
    assert page.waited
    assert wait_for_video(page)["decodedFrames"] == 20


def test_media_wait_has_a_finite_deadline_and_rejects_early_closure():
    page = PageDouble([{"decodedFrames": 0, "renderedFrames": 0, "closed": True}])
    with pytest.raises(RuntimeError, match="ended before"):
        wait_for_video(page)
    ticks = iter([0, 100])
    with pytest.raises(RuntimeError, match="deadline"):
        wait_for_video(PageDouble(), clock=lambda: next(ticks))


def test_same_response_clone_has_real_body_and_matching_metadata():
    clone = inspect_clone(
        {
            "url": "https://demo.invalid/api/getSpeechToken",
            "method": "GET",
            "status": 404,
            "headers": {"content-type": "application/json"},
            "body": '{"error":"not-found"}',
        },
        (),
    )
    assert clone.body_bytes == 21
    assert clone_matches_response(clone, ("/api/getSpeechToken", "GET", 404))
    assert not clone_matches_response(clone, ("/api/getSpeechToken", "GET", 200))
    assert not clone_matches_response(clone, None)


def test_clone_missing_bytes_is_not_a_clean_response():
    clone = inspect_clone(
        {
            "url": "https://demo.invalid/api/getSpeechToken",
            "method": "GET",
            "status": 404,
            "headers": {},
            "error": "same-response-clone-unavailable",
        },
        (),
    )
    assert not clone_matches_response(clone, ("/api/getSpeechToken", "GET", 404))


def test_clone_scans_the_actual_body_not_an_expected_fixture():
    clone = inspect_clone(
        {
            "url": "https://demo.invalid/api/getSpeechToken",
            "method": "GET",
            "status": 404,
            "headers": {},
            "body": "aad#SYNTHETIC-NOT-A-VALID-CREDENTIAL",
        },
        (),
    )
    assert "azure-authorization" in clone.findings
