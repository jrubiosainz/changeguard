import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(args: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(args, input=input_text, capture_output=True, text=True, check=False, cwd=ROOT)
    if result.returncode:
        # CLI diagnostics may include resource IDs, secure parameters, or request payloads.
        category = "command-failed"
        for needle, safe in (
            ("AuthorizationFailed", "azure-authorization-failed-no-role-escalation"),
            ("Quota", "azure-quota-unavailable"),
            ("SkuNotAvailable", "azure-sku-unavailable"),
            ("Region", "azure-region-operation-failed"),
            ("Conflict", "azure-resource-conflict"),
        ):
            if needle in result.stderr:
                category = safe
                break
        raise RuntimeError(f"{category}: {args[0]} (exit {result.returncode}); raw output suppressed")
    return result.stdout


def azure(args: list[str], subscription: str):
    if not subscription:
        raise RuntimeError("An explicit Azure subscription is required")
    output = command(
        ["az", *args, "--subscription", subscription, "--only-show-errors", "--output", "json"],
    )
    return json.loads(output) if output.strip() else None


def secret(kind: str) -> str:
    variable = {"operator": "CHANGEGUARD_OPERATOR_TOKEN", "signing": "SESSION_SIGNING_KEY"}[kind]
    value = os.environ.get(variable)
    if not value or len(value) < 32:
        raise RuntimeError(
            f"Supply {variable} through your secret manager with at least 32 random characters"
        )
    return value


def source_commit() -> str:
    value = command(["git", "rev-parse", "HEAD"]).strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) or set(value) == {"0"}:
        raise RuntimeError("A real source commit is required before live deployment/evaluation")
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
