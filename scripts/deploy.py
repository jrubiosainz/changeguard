import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from common import ROOT, azure, command, secret, source_commit, write_json
from preflight import preflight

sys.path.insert(0, str(ROOT))
from changeguard.provenance import runtime_paths, source_digest  # noqa: E402

DEPLOYMENT = "changeguard-v1"


def bundle(destination: Path, commit: str) -> str:
    digest = source_digest(ROOT)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in runtime_paths(ROOT):
            archive.write(path, str(path.relative_to(ROOT)))
        archive.writestr(
            "build-info.json",
            json.dumps({"sourceCommit": commit, "sourceDigest": digest}),
        )
    return digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subscription", default=os.environ.get("AZURE_SUBSCRIPTION_ID"))
    parser.add_argument("--group", default=os.environ.get("AZURE_RESOURCE_GROUP", "rg-changeguard"))
    parser.add_argument("--region", default=os.environ.get("AZURE_LOCATION", "westeurope"))
    parser.add_argument("--provision", action="store_true")
    parser.add_argument("--infra-only", action="store_true")
    parser.add_argument("--confirm-costs", action="store_true", help="Acknowledge hosting and Speech charges")
    args = parser.parse_args()
    if not args.confirm_costs:
        parser.error("Deployment requires --confirm-costs and an explicitly authorized Azure scope")
    if args.infra_only and not args.provision:
        parser.error("--infra-only requires --provision")
    preflight(args.subscription, args.group, args.region)
    commit = source_commit()
    dirty = command(
        [
            "git",
            "status",
            "--porcelain",
            "--",
            *[str(path) for path in runtime_paths(ROOT)],
        ]
    )
    if dirty.strip():
        raise RuntimeError("Commit all runtime source changes before deployment")
    if args.provision:
        token = secret("operator")
        signing = secret("signing")
        if token == signing:
            raise RuntimeError("Operator token and session signing key must be independent")
        parameters = {
            "location": {"value": args.region},
            "operatorTokenHash": {"value": hashlib.sha256(token.encode()).hexdigest()},
            "sessionSigningKey": {"value": signing},
            "sourceCommit": {"value": commit},
        }
        print("Provisioning the selected application scope; secure parameters and ARM output suppressed.")
        with tempfile.TemporaryDirectory(prefix="changeguard-parameters-") as directory:
            parameter_path = Path(directory) / "parameters.json"
            descriptor = os.open(parameter_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                json.dump(parameters, handle)
            deployment = azure(
                [
                    "deployment",
                    "group",
                    "create",
                    "--resource-group",
                    args.group,
                    "--name",
                    DEPLOYMENT,
                    "--template-file",
                    str(ROOT / "infra/main.bicep"),
                    "--parameters",
                    f"@{parameter_path}",
                ],
                args.subscription,
            )
    else:
        deployment = azure(
            ["deployment", "group", "show", "--resource-group", args.group, "--name", DEPLOYMENT],
            args.subscription,
        )
    outputs = {key: item["value"] for key, item in deployment["properties"]["outputs"].items()}
    if outputs["region"] != args.region:
        raise RuntimeError("Deployed region differs; review before changing any resource")
    app_name = outputs["appName"]
    if not args.infra_only:
        azure(
            [
                "webapp",
                "config",
                "appsettings",
                "set",
                "-g",
                args.group,
                "-n",
                app_name,
                "--settings",
                f"SOURCE_COMMIT={commit}",
            ],
            args.subscription,
        )
        with tempfile.TemporaryDirectory(prefix="changeguard-bundle-") as directory:
            archive = Path(directory) / "app.zip"
            runtime_digest = bundle(archive, commit)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            print("Deploying a source-identified application bundle; no credentials included.", flush=True)
            azure(
                [
                    "webapp",
                    "deploy",
                    "-g",
                    args.group,
                    "-n",
                    app_name,
                    "--src-path",
                    str(archive),
                    "--type",
                    "zip",
                    "--timeout",
                    "600000",
                    "--async",
                    "true",
                    "--track-status",
                    "false",
                ],
                args.subscription,
            )
        healthy = False
        for _ in range(60):
            try:
                with urlopen(f"{outputs['url']}/api/health", timeout=15) as response:
                    health = json.load(response)
                healthy = (
                    health.get("status") == "ok"
                    and health.get("sourceCommit") == commit
                    and health.get("sourceDigest") == runtime_digest
                )
            except (HTTPError, URLError, TimeoutError):
                healthy = False
            if healthy:
                break
            time.sleep(5)
        if not healthy:
            raise RuntimeError("Deployment HTTP health did not become ready; media not attempted")
    report = {
        "schemaVersion": 1,
        "applicationId": "changeguard",
        "generatedAt": datetime.now(UTC).isoformat(),
        "sourceCommit": commit,
        "url": outputs["url"],
        "healthUrl": f"{outputs['url']}/api/health",
        "region": outputs["region"],
        "resourceTypes": [
            {"kind": "SpeechServices", "sku": "S0", "authentication": "managed-identity-only"},
            {"kind": "Linux App Service", "sku": "B1", "instances": 1},
        ],
        "identityScope": "Speech User on the newly created Speech account only",
        "httpHealthy": not args.infra_only,
        "liveMediaProven": False,
        "sourceBundleSha256": digest if not args.infra_only else None,
        "runtimeSourceSha256": runtime_digest if not args.infra_only else None,
        "costNote": "B1 has a fixed standing charge; it does not scale to zero. Speech is metered.",
        "operatorAuthorization": "Explicit application operator token supplied by the deploying operator",
    }
    write_json(ROOT / ".runtime/deployment.json", report)
    print(f"Deployment ready: {outputs['url']} (HTTP health is not media verification).")


if __name__ == "__main__":
    main()
