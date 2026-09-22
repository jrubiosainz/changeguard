# Optional authenticated Azure setup

Offline preview is the default and needs none of this configuration. This guide is for an operator who has separately approved resource creation, identity assignments, ongoing hosting costs, and any paid avatar execution.

## Prerequisites

Use Python 3.12, the locked runtime dependencies, Azure CLI with Bicep support, and an explicitly selected Azure subscription and dedicated application resource group. The caller needs permission to deploy the selected resources and assign the new application's managed identity the **Cognitive Services Speech User** role on the new Speech resource. Do not grant broader roles just to make deployment succeed.

Read the current [avatar region availability](https://learn.microsoft.com/azure/ai-services/speech-service/regions#tab/ttsavatar), [quota and limits](https://learn.microsoft.com/azure/ai-services/speech-service/speech-services-quotas-and-limits), [real-time avatar guidance](https://learn.microsoft.com/azure/ai-services/speech-service/text-to-speech-avatar/real-time-synthesis-avatar), and [Speech SDK platform requirements](https://learn.microsoft.com/azure/ai-services/speech-service/quickstarts/setup-platform). Region/SKU metadata does not establish available live capacity.

## Explicit configuration

The application does not read `.env` automatically. [`.env.example`](../.env.example) contains only generic settings and placeholders; inject real secrets through your chosen secret manager or deployment environment, not the repository or shell history.

| Variable | Use |
| --- | --- |
| `CHANGEGUARD_MODE` | `local-fixture` by default; `live-azure` explicitly enables the live provider |
| `PUBLIC_ORIGIN` | Exact query-free HTTPS origin for a live server |
| `OPERATOR_TOKEN_SHA256` | SHA-256 of the high-entropy application operator token; required by the live server |
| `SESSION_SIGNING_KEY` | Independent random signing value, at least 32 characters |
| `SPEECH_RESOURCE_ID` | Resource ID used server-side with Speech authorization |
| `SPEECH_HOST` | Speech custom subdomain ending in `.cognitiveservices.azure.com` |
| `SPEECH_REGION` | Selected supported region |
| `CHANGEGUARD_DATA_DIR` | Private persistent directory for the start budget |
| `AZURE_SUBSCRIPTION_ID` | Explicit subscription for management scripts; no CLI default-account changes |
| `AZURE_RESOURCE_GROUP` | Dedicated deployment scope; default `rg-changeguard` |
| `AZURE_LOCATION` | Deployment region; default `westeurope` |
| `CHANGEGUARD_OPERATOR_TOKEN` | Raw application token supplied only to the deployment/evaluation tools and authorized operator |

Generate the operator token and signing key independently with a cryptographically secure generator; 48 random bytes encoded for transport are suitable. The tools accept them only from the environment. The deployment script computes the operator hash rather than sending the raw operator token to the application.

## Infrastructure and deployment

The [Bicep template](../infra/main.bicep) creates Speech S0 with local/key authentication disabled, a single Linux App Service B1 instance running Python 3.12, and a system-assigned managed identity. Its role assignment is scoped to the newly created Speech account. HTTPS, TLS minimums, and disabled basic publishing credentials are configured by the template.

Use an existing approved dedicated resource group. For example, in a shell with the required permissions and injected secrets:

```bash
export AZURE_SUBSCRIPTION_ID="<your-subscription-id>"
export AZURE_RESOURCE_GROUP="rg-changeguard"
export AZURE_LOCATION="westeurope"

python scripts/preflight.py
python scripts/deploy.py --provision --confirm-costs
python scripts/verify_cloud.py
```

On PowerShell, set environment variables with `$env:AZURE_SUBSCRIPTION_ID = "<your-subscription-id>"` and the equivalent names above; the Python commands are the same.

`preflight.py` performs management-plane reads. `deploy.py --provision --confirm-costs` **creates or changes resources and incurs standing hosting charges**. It does not start an avatar. Re-running the deployment against the same dedicated scope updates the named deployment; inspect that scope before proceeding. To deploy only committed runtime source to an existing configured deployment, omit `--provision` but retain `--confirm-costs`.

The script refuses uncommitted runtime files, bundles a source digest, and records a private local receipt at `.runtime/deployment.json`. Secure infrastructure parameters pass through a restricted temporary file removed on exit, not command-line values. On Windows, the temporary directory also relies on the current user's filesystem ACL; use a private user environment. Do not enable shell tracing or upload deployment receipts.

Gunicorn uses **one worker process with threads**. Keep the host at one instance. The template's `/home/changeguard-data` directory persists the budget independently of deployment packages. Local offline preview uses Flask and does not require Gunicorn or a particular shell.

## Identity and operator access

Azure-hosted execution selects `ManagedIdentityCredential`. Other explicitly configured live environments use `DefaultAzureCredential` with interactive browser login disabled. Such an environment must provide its own approved identity and a verified runtime bundle; the offline launcher never selects this path.

Visit the configured HTTPS application as a read-only visitor. Only an authorized operator should use **Unlock operator** and enter the application token. Never enter an Azure service token or key in that dialog. The server issues a 30-minute HttpOnly, SameSite=Strict cookie and requires same-origin requests for avatar operations.

The operator mechanism is intentionally small and inspectable; it is not tenant SSO or a full multi-user authorization system. Scope access and rotate the application token and signing key according to your operating policy.

## A separately authorized live measurement

After reviewing hosting, identity, privacy, and cost, an operator can explicitly authorize one bounded run:

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install webkit
python scripts/evaluate.py --allow-billable --browser webkit
```

This command can incur Speech charges. It authenticates using the injected application token, validates the deployed source identity, inspects real browser responses, measures media, and attempts closure in cleanup. It makes no automatic start retry. The destination must match the local deployment receipt; the tool will not send the operator token to an arbitrary `--url`.

Reports and optional captures stay in `.runtime/evidence/`. `--record` is an additional opt-in for local media/UI capture and requires an operator's data-handling approval. Those files are not automatically served by the app or committed. Inspect them before any deliberate publication. A failed run remains a failure; do not substitute the bundled recorded example for a fresh measurement.
