import argparse
import os
import sys
from datetime import UTC, datetime

from common import ROOT, azure, write_json

sys.path.insert(0, str(ROOT))
from changeguard.policy import architecture_gate  # noqa: E402

SUPPORTED = {"westeurope", "swedencentral", "northeurope", "westus2", "eastus"}


def preflight(subscription: str, group: str, region: str) -> dict:
    import json

    if not subscription:
        raise RuntimeError("AZURE_SUBSCRIPTION_ID is required; the CLI default is never changed")
    if not group or not group.strip():
        raise RuntimeError("An explicit application resource group is required")
    if region not in SUPPORTED:
        raise RuntimeError("Region is outside the explicitly reviewed real-time avatar region set")
    if architecture_gate(json.loads((ROOT / "workflow/architecture.json").read_text())):
        raise RuntimeError("Architecture review gate rejected the proposed deployment")
    group_info = azure(["group", "show", "--name", group], subscription)
    skus = azure(
        ["cognitiveservices", "account", "list-skus", "--kind", "SpeechServices", "-l", region],
        subscription,
    )
    s0 = next((sku for sku in skus if sku["name"] == "S0"), None)
    if s0 is None or s0.get("restrictions"):
        raise RuntimeError("Speech S0 not available without restrictions in requested region")
    locations = azure(
        ["appservice", "list-locations", "--sku", "B1", "--linux-workers-enabled"], subscription
    )
    if not any(item["name"].lower().replace(" ", "") == region for item in locations):
        raise RuntimeError("Linux B1 is unavailable in requested hosting region")
    usages = azure(["cognitiveservices", "usage", "list", "--location", region], subscription)
    avatar_quotas = [
        {"name": item["name"]["value"], "current": item["currentValue"], "limit": item["limit"]}
        for item in usages
        if "avatar" in item["name"]["value"].lower()
    ]
    report = {
        "schemaVersion": 1,
        "applicationId": "changeguard",
        "generatedAt": datetime.now(UTC).isoformat(),
        "groupMetadataRegion": group_info["location"],
        "selectedRegion": region,
        "speechSku": "S0",
        "hostingSku": "Linux B1",
        "s0Restrictions": [],
        "avatarQuotaReportedByManagementApi": avatar_quotas,
        "publishedDefaultNewConnectionsPerMinute": 2,
        "capacityProven": False,
        "note": "Regional/SKU availability is not live avatar capacity or media readiness.",
        "sources": [
            "https://learn.microsoft.com/azure/ai-services/speech-service/regions#tab/ttsavatar",
            "https://learn.microsoft.com/azure/ai-services/speech-service/speech-services-quotas-and-limits",
        ],
    }
    write_json(ROOT / ".runtime/preflight.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subscription", default=os.environ.get("AZURE_SUBSCRIPTION_ID"))
    parser.add_argument("--group", default=os.environ.get("AZURE_RESOURCE_GROUP", "rg-changeguard"))
    parser.add_argument("--region", default=os.environ.get("AZURE_LOCATION", "westeurope"))
    args = parser.parse_args()
    report = preflight(args.subscription, args.group, args.region)
    print(
        f"Preflight passed: {report['selectedRegion']}, S0 + Linux B1. "
        "Live capacity still requires bounded execution."
    )


if __name__ == "__main__":
    main()
