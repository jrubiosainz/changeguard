# Architecture and boundaries

ChangeGuard is a small, inspectable implementation of [CG-001](../workflow/requirement.json): keep Azure Speech service authorization and resource identifiers on the application server, while preserving browser-to-Azure real-time avatar media.

This is an application architecture policy. Resource, subscription, and tenant identifiers are metadata, not authentication secrets. The implementation does not imply that supported browser-side Azure authentication is inherently an Azure vulnerability.

## Three different kinds of authorization data

| Data | Where it belongs in this design |
| --- | --- |
| Azure service keys and Entra/Speech service authorization tokens | Server only; the reference live configuration uses identity, not service keys |
| Speech resource ID and configured resource identifiers | Server only because CG-001 explicitly requires it |
| Application operator token and session cookie | Token entered only by an authorized operator; a signed HttpOnly cookie authorizes application operations |
| SDP, ICE usernames/passwords, TURN relay credentials, DTLS fingerprints, media endpoint addresses | Necessarily available to the authorized browser for WebRTC |

Base64, URL encoding, or hiding fields in JavaScript does not change the credential holder. The synthetic comparison uses invented nonfunctional values to demonstrate this without exposing a working service credential.

## Request flow

1. Read-only pages, the requirement, and the recorded example load without authentication. Offline mode creates no Azure provider and cannot obtain Azure credentials.
2. On a configured live HTTPS server, an operator supplies a high-entropy application token. The server compares its SHA-256 with the configured digest and issues a 30-minute signed cookie.
3. `POST /api/avatar/prepare` reserves the sole session slot, consumes the persistent start budget, arms the 90-second deadline, and obtains ephemeral relay configuration on the server.
4. The browser creates a relay-only `RTCPeerConnection`, gathers a relay candidate for every active media section, and posts its SDP offer to `POST /api/avatar/connect`.
5. Python obtains a server-side Entra token and uses the Speech SDK to negotiate. Only the SDP answer returns to the browser.
6. Audio and video travel over WebRTC between the browser and Azure, potentially through Azure TURN relays. `POST /api/avatar/speak` invokes server-side synthesis.
7. `POST /api/avatar/stop`, logout, a failure path, or the independent server deadline closes the session. A failed native close retains ownership, retries bounded cleanup, and prevents a new reservation.

## Read-only and protected endpoints

| Endpoint | Behavior |
| --- | --- |
| `GET /api/health` | Execution mode and source identity, never an assertion of media readiness |
| `GET /api/manifest` | Boundary, limits, and the example workflow's pending human review state |
| `GET /api/workflow` | Portable requirement and machine architecture-policy result |
| `GET /api/comparison` | Synthetic baseline, encoded wrong fix, and server-boundary fixture |
| `GET /api/evidence` | Bundled, labeled recorded example, not an automatically published new run |
| `GET /api/operator` | Whether the current browser has application operator access |
| `POST /api/operator/login` | Available only when operator authorization is configured; no preview login |
| `POST /api/operator/logout` | Closes the owned session before deleting its cookie |
| `/api/avatar/prepare`, `/connect`, `/speak`, `/stop`, `/status` | Authenticated operator operations; only `/status` uses GET |

The obsolete `/api/getSpeechToken`, `/api/speech/token`, `/api/token`, and `/api/getIceToken` routes return 404, including for authenticated callers. API query parameters are rejected rather than reflected. State-changing requests need the exact configured Origin and the `X-ChangeGuard-Request: 1` marker. The application provides no cross-origin credential exchange.

## Errors and lifecycle

SDK diagnostics, raw SDP, cookies, and tokens must not be logged. Expected failures use public error codes; unexpected exceptions log only their type and return `internal-error`. The response boundary withholds an API JSON response if it matches service-authorization patterns or configured protected identifiers.

The scanner inspects raw text, URL-decoded text, and a bounded set of base64 candidates. That is useful for the declared policy, not proof against every encoding, side channel, extension, or browser-memory exposure. Browser inspection failures are recorded as failed coverage, never converted into zero findings.

The Python and JavaScript regression suites cover cancellation during preparation, pending and failed closes, stale ownership handles, finite ICE readiness, connect deadlines, native SDK launch/close ordering, and persisted start limits. They protect the asynchronous state transitions beyond the happy path.

## Source identity

Live configuration requires a deployment-bundled `build-info.json` containing a real source commit and a digest of the runtime files. Startup recomputes that digest; changing an environment label alone is insufficient. The optional evaluator checks the served identity against its local runtime source.

`SOURCE_COMMIT` in offline mode is only a display label. It is not evidence that Azure executed that source. The bundled [recorded example](recorded-example.md) has its own digest and an original-report digest, and explicitly makes no current-checkout execution claim.

## Deliberate constraints

Run one application process and one hosting instance. SQLite persists hourly start timestamps; in-memory ownership is not a distributed lock. Multiple workers or scaled-out replicas would violate the concurrency model and require a different design.

The Speech SDK is pinned because negotiation uses `SpeechSDKInternal-ExtraTurnStartMessage`, an internal sample-dependent property. Upgrading the SDK requires a separately authorized compatibility run. The UI measures inbound decoded frames and audio bytes, but a successful run in one browser is not an all-browser compatibility guarantee.

See [operations](operations.md) for cleanup failures, logging boundaries, standing costs, and deployment shutdown.
