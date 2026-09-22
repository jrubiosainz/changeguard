import base64
import json
import re
from urllib.parse import unquote

PATTERNS = {
    "azure-authorization": re.compile(r"aad#|Ocp-Apim-Subscription-Key", re.IGNORECASE),
    "arm-resource-identifier": re.compile(r"/subscriptions/[^/\s]+/resourcegroups/", re.I),
    "azure-token-claim": re.compile(r'["\'](?:azure-resource-id|xms_mirid|tid)["\']\s*:', re.I),
    "credential-query": re.compile(r"[?&](?:authorization|subscription-key|access_token)=", re.I),
    "jwt-credential": re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
}


def scan_boundary(text: str, forbidden_values: tuple[str, ...] = ()) -> list[str]:
    """Inspect raw, URL-encoded and bounded base64 content without retaining values."""
    candidates = [text, unquote(text)]
    for _ in range(2):
        decoded = []
        for candidate in candidates[-12:]:
            for encoded in re.findall(r"[A-Za-z0-9+/_-]{24,}={0,2}", candidate)[:64]:
                if len(encoded) > 131072:
                    continue
                try:
                    value = base64.b64decode(
                        encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
                    ).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    continue
                if value not in candidates and value not in decoded:
                    decoded.append(value)
        candidates.extend(decoded)
    findings = set()
    for candidate in candidates:
        for name, pattern in PATTERNS.items():
            if pattern.search(candidate):
                findings.add(name)
        if any(value and value.lower() in candidate.lower() for value in forbidden_values):
            findings.add("actual-provisioned-identifier")
    return sorted(findings)


def synthetic_comparison() -> dict:
    # Invented names deliberately cannot authorize any Azure resource.
    credential = (
        "aad#/subscriptions/SYNTHETIC-SUBSCRIPTION/resourceGroups/SYNTHETIC-GROUP/"
        "providers/Microsoft.CognitiveServices/accounts/SYNTHETIC-SPEECH"
        "#SYNTHETIC-NOT-A-VALID-TOKEN"
    )
    fixtures = {
        "unmodified-browser-auth": json.dumps({"speechAuthorization": credential}),
        "wrong-fix-base64-mask": json.dumps({"masked": base64.b64encode(credential.encode()).decode()}),
        "server-boundary-fixture": json.dumps(
            {
                "type": "answer",
                "sdp": "v=0\r\na=ice-ufrag:synthetic\r\na=ice-pwd:synthetic-transport-only\r\n",
            }
        ),
    }
    return {
        "label": "SYNTHETIC COMPARISON - no real Azure credential or service execution",
        "execution": {"mode": "local-fixture"},
        "cases": [
            {
                "id": name,
                "passed": not (findings := scan_boundary(body)),
                "findings": findings,
                "fixtureOnly": True,
            }
            for name, body in fixtures.items()
        ],
        "warning": "The passing fixture proves the scanner, not live avatar playback.",
    }


def architecture_gate(plan: dict) -> list[str]:
    required = {
        "azureAuthentication": "server-managed-identity",
        "signaling": "server-speech-sdk",
        "media": "browser-to-azure-webrtc",
        "tokenIssuingRoute": "removed",
        "billableOperations": "authenticated-operator-only",
        "turnConfiguration": "protected-transport-only",
        "errorDetails": "allowlisted-public-codes-only",
    }
    failures = [key for key, value in required.items() if plan.get("proposedBoundary", {}).get(key) != value]
    if plan.get("reviewGate", {}).get("humanApproval") != "pending":
        failures.append("no-invented-human-approval")
    return failures
