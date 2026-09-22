import base64
import json
import subprocess
import sys
from urllib.parse import quote

import pytest

from changeguard.policy import architecture_gate, scan_boundary, synthetic_comparison
from changeguard.settings import ROOT


def test_unmodified_and_plausible_wrong_fix_fail():
    report = synthetic_comparison()
    baseline, masked, boundary = report["cases"]
    assert report["execution"]["mode"] == "local-fixture"
    assert baseline["passed"] is False
    assert masked["passed"] is False
    assert "arm-resource-identifier" in baseline["findings"]
    assert "azure-authorization" in masked["findings"]
    assert boundary["passed"] is True
    assert boundary["fixtureOnly"] is True


@pytest.mark.parametrize(
    "encode", [lambda value: value, quote, lambda s: base64.b64encode(s.encode()).decode()]
)
def test_service_credential_is_rejected_even_when_obfuscated(encode):
    synthetic = "aad#/subscriptions/FAKE/resourceGroups/FAKE/providers/FAKE#NOT-A-TOKEN"
    assert scan_boundary(json.dumps({"value": encode(synthetic)}))


def test_transport_credentials_are_not_falsely_equated_to_service_credentials():
    assert not scan_boundary(
        "a=ice-pwd:transport-secret\r\na=ice-ufrag:transport-user\r\n"
        '{"credential":"transport-only","urls":["turn:relay.communication.microsoft.com:3478"]}'
    )


def test_jwt_claims_and_query_are_detected():
    encoded = base64.urlsafe_b64encode(b'{"tid":"synthetic-tenant"}').decode().rstrip("=")
    assert "azure-token-claim" in scan_boundary(encoded)
    assert "credential-query" in scan_boundary("wss://example.invalid/?Authorization=FAKE")


def test_known_provisioned_values_are_detected_without_publishing_them():
    assert scan_boundary("prefix known-test-identifier suffix", ("known-test-identifier",)) == [
        "actual-provisioned-identifier"
    ]


def test_architecture_gate_rejects_browser_auth_and_auto_approval():
    plan = json.loads((ROOT / "workflow/architecture.json").read_text())
    assert architecture_gate(plan) == []
    plan["proposedBoundary"]["azureAuthentication"] = "browser-with-masked-token"
    plan["reviewGate"]["humanApproval"] = "approved-by-agent"
    assert set(architecture_gate(plan)) == {"azureAuthentication", "no-invented-human-approval"}


def test_default_policy_command_does_not_rewrite_committed_evidence():
    original = (ROOT / "static/examples/recorded-session.json").read_bytes()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/policy_gate.py"), "--candidate", "server"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert (ROOT / "static/examples/recorded-session.json").read_bytes() == original
    generated = json.loads((ROOT / ".runtime/policy-last-run.json").read_text())
    assert generated["executionMode"] == "local-fixture"
    assert generated["approval"]["state"] == "pending"
