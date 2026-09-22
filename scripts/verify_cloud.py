import argparse
import os
from datetime import UTC, datetime

from common import ROOT, azure, source_commit, write_json

SPEECH_USER = "f2dc8367-1007-4938-bd23-fe263f013447"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subscription", default=os.environ.get("AZURE_SUBSCRIPTION_ID"))
    parser.add_argument("--group", default=os.environ.get("AZURE_RESOURCE_GROUP", "rg-changeguard"))
    args = parser.parse_args()
    if not args.subscription:
        raise RuntimeError("Explicit AZURE_SUBSCRIPTION_ID is required")
    group = args.group
    deployment = azure(
        ["deployment", "group", "show", "-g", group, "-n", "changeguard-v1"], args.subscription
    )
    outputs = {name: value["value"] for name, value in deployment["properties"]["outputs"].items()}
    speech = azure(
        ["cognitiveservices", "account", "show", "-g", group, "-n", outputs["speechName"]],
        args.subscription,
    )
    app = azure(["webapp", "show", "-g", group, "-n", outputs["appName"]], args.subscription)
    config = azure(["webapp", "config", "show", "-g", group, "-n", outputs["appName"]], args.subscription)
    plan = azure(["appservice", "plan", "show", "-g", group, "-n", outputs["planName"]], args.subscription)
    roles = azure(
        [
            "role",
            "assignment",
            "list",
            "--scope",
            speech["id"],
            "--query",
            f"[?principalId=='{app['identity']['principalId']}']",
        ],
        args.subscription,
    )
    controls = [
        (
            "speech-s0-and-keyless",
            speech["sku"]["name"] == "S0" and speech["properties"]["disableLocalAuth"] is True,
            "Actual new Speech account is S0 with local/key authentication disabled.",
        ),
        (
            "least-privilege-resource-role",
            len(roles) == 1
            and roles[0]["roleDefinitionId"].endswith(SPEECH_USER)
            and roles[0]["scope"].lower() == speech["id"].lower(),
            "New app identity has one Speech User assignment at the new Speech-resource scope.",
        ),
        (
            "small-linux-hosting",
            plan["sku"]["name"] == "B1"
            and plan["sku"]["capacity"] == 1
            and plan["properties"]["reserved"] is True,
            "Actual App Service plan is Linux B1, one instance; standing charges apply.",
        ),
        (
            "tls-and-python",
            app["httpsOnly"] is True
            and config["minTlsVersion"] == "1.2"
            and config["linuxFxVersion"] == "PYTHON|3.12",
            "HTTPS-only application, TLS 1.2 minimum, Python 3.12 runtime.",
        ),
        (
            "resource-region",
            all(
                item["location"].lower().replace(" ", "") == outputs["region"] for item in (speech, app, plan)
            ),
            "New Speech and hosting resources use the explicitly selected supported region.",
        ),
    ]
    report = {
        "schemaVersion": 1,
        "applicationId": "changeguard",
        "runId": "cloud-controls-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "generatedAt": datetime.now(UTC).isoformat(),
        "sourceCommit": source_commit(),
        "executionMode": "live-azure",
        "execution": {"mode": "live-azure", "billableAvatarStarted": False},
        "metrics": [
            {
                "name": "scoped_identity_role_assignments",
                "value": len(roles),
                "unit": "assignments",
                "kind": "measured",
                "method": "Azure role assignment query at the new Speech-resource scope",
            }
        ],
        "checks": [
            {"id": identifier, "status": "pass" if passed else "fail", "details": details}
            for identifier, passed, details in controls
        ],
        "approval": {"state": "pending", "actorType": "none", "reason": "Human review required"},
        "artifacts": [{"label": "Local deployment manifest", "path": ".runtime/deployment.json"}],
        "note": "Management-plane control verification, not avatar media proof. No raw ARM IDs retained.",
    }
    write_json(ROOT / ".runtime/cloud-controls.json", report)
    passed = all(item[1] for item in controls)
    print(f"Scoped cloud controls: {'PASS' if passed else 'FAIL'}; no avatar started.")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
