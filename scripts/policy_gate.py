import argparse
import json
import sys
from datetime import UTC, datetime

from common import ROOT, source_commit, write_json

sys.path.insert(0, str(ROOT))
from changeguard.policy import architecture_gate, synthetic_comparison  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", choices=["baseline", "wrong-fix", "server"], default="server")
    parser.add_argument("--output", default=".runtime/policy-last-run.json")
    args = parser.parse_args()
    cases = synthetic_comparison()["cases"]
    selected = cases[{"baseline": 0, "wrong-fix": 1, "server": 2}[args.candidate]]
    gate = architecture_gate(json.loads((ROOT / "workflow/architecture.json").read_text()))
    passing = selected["passed"] and not gate
    report = {
        "schemaVersion": 1,
        "applicationId": "changeguard",
        "runId": f"policy-{args.candidate}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "generatedAt": datetime.now(UTC).isoformat(),
        "sourceCommit": source_commit(),
        "executionMode": "local-fixture",
        "execution": {"mode": "local-fixture"},
        "candidate": args.candidate,
        "metrics": [
            {
                "name": "boundary_findings",
                "value": len(selected["findings"]),
                "unit": "categories",
                "kind": "synthetic",
                "method": "Identical raw/URL/base64 boundary scanner over an invented fixture",
            }
        ],
        "checks": [
            {
                "id": "candidate-boundary",
                "status": "pass" if selected["passed"] else "fail",
                "passed": selected["passed"],
                "details": (
                    "Synthetic fixture only; not evidence of live playback. "
                    + (", ".join(selected["findings"]) or "No prohibited boundary material found.")
                ),
            },
            {
                "id": "architecture-review",
                "status": "pass" if not gate else "fail",
                "passed": not gate,
                "details": "Required server boundary and pending human gate checked.",
            },
        ],
        "approval": {"state": "pending", "actorType": "none", "reason": "Human review required"},
        "artifacts": [
            {"label": "Business requirement", "path": "workflow/requirement.json"},
            {"label": "Architecture proposal", "path": "workflow/architecture.json"},
        ],
    }
    path = (ROOT / args.output).resolve()
    if not path.is_relative_to((ROOT / ".runtime").resolve()):
        raise RuntimeError("Generated reports must remain inside the untracked .runtime directory")
    write_json(path, report)
    print(f"SYNTHETIC {args.candidate}: {'PASS' if passing else 'REJECTED'}")
    raise SystemExit(0 if passing else 1)


if __name__ == "__main__":
    main()
