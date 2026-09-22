# Operations, costs, and data handling

## Offline is the safe default

`python scripts/run_local.py` forces loopback-only offline mode. It needs no Azure identity, reads no operator secret, and creates no Azure provider. The UI offers a synthetic policy comparison and a clearly labeled recorded still/report. Viewing them is not a new cloud operation.

The local application may create `.runtime/usage.sqlite3` for the same budget implementation used by live mode. This file contains timestamps, not credentials, SDP, or spoken text. The read-only preview does not consume a billable start.

## Limits are not a spending cap

| Control | Bound |
| --- | --- |
| Concurrent owned sessions | 1 per application process; deploy one process and one instance |
| Server session deadline | At most 90 seconds, independent of browser cleanup |
| Starts per rolling hour | At most 8; persisted in SQLite |
| Interval between starts | At least 60 seconds in live mode |
| Speech requests | At most 3 per session |
| Text length | 1 to 280 characters per request |
| Connect deadline | 35 seconds |
| Synthesis deadline | 25 seconds |
| Application operator cookie | 30 minutes |
| Automatic paid start retries | None |

Failed starts consume budget because service work may already have begun. B1 App Service has a standing charge and does not scale to zero. Speech usage, relay behavior, and regional pricing can vary. The application cannot enforce a subscription-wide currency budget; use separate Azure budget alerts and an operator-reviewed resource shutdown plan.

Closing a browser tab is not a resource teardown. Stopping the web application alone does not remove the hosting plan's standing cost. A resource owner must review and remove the dedicated resources when they are no longer needed; no cleanup command in this repository automatically deletes a group.

## Failed closure and recovery

The session manager marks a session as closing before native cleanup. It retains the slot if cancellation or SDK closure fails, makes bounded cleanup retries, and then logs `avatar-cleanup-quarantined operator-retry-required`. The UI retains the server handle and blocks new starts until a close succeeds. **Do not bypass this by adding workers or repeatedly starting more sessions.**

Inspect the allowlisted error codes, confirm the service session has ended, and review the affected deployment before operator recovery. A deadline is a best-effort service cleanup boundary, not an assurance against every process failure or network partition.

## Data minimization

The live server processes service authorization, resource metadata, relay configuration, SDP, and short sample text in memory. It does not return Azure authorization tokens or resource identifiers to browser code. Expected public errors use fixed codes; raw SDK diagnostics are not logged.

Authorized browsers necessarily see ephemeral SDP and TURN/ICE transport credentials and network addresses. An operator token is an application credential, distinct from Azure service credentials. Do not collect cookies, raw SDP, authorization headers, browser profiles, HAR files, or unrestricted traces as evidence.

The evaluator scans response bodies, headers, URLs, and console events in memory. It records categories, counts, inspection failures, measured media counters, and source identity. It does not prove absence of data in all browser memory, and a zero-finding result is meaningful only within its recorded coverage.

Local output directories and deployment archives are ignored by Git. That is a guardrail, not a publication review: always inspect filenames, contents, images, and metadata before intentionally sharing an artifact. Use only synthetic inputs for offline tests.

## Compatibility and production boundaries

This reference has one-process session ownership, a scoped operator token rather than SSO, an internal Speech SDK signaling dependency, and conservative fixed limits. Production use needs its own threat model, identity design, distributed concurrency controls if scaling, monitoring, service compatibility checks, and operational review.

The included recorded example covers **WebKit 26.6 on macOS in one bounded run**. It does not establish an all-browser SLA, independently certified security, current service capacity, or live success for a new source revision. Inspect the actual browser/SDK combination and network policy you intend to use.
