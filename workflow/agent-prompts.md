# Portable planning and review prompts

These prompts are reusable instructions, not agent transcripts or approvals. They work in GitHub Copilot App sessions or another development workflow because their inputs and outputs are ordinary source files, tests, and review artifacts.

Start with [CG-001](requirement.json) and the [architecture proposal](architecture.json). Default to offline work. Deployment, role assignments, live avatar starts, and media recording require separate explicit operator authorization.

## Requirement analyst

> Explain the browser/server boundary required by CG-001. Distinguish service authorization, resource metadata, application operator access, and necessary WebRTC transport credentials. Read the relevant code and current Microsoft documentation. Identify acceptance criteria, SDK compatibility risks, and meaningful media measurements. Do not describe the policy as an Azure vulnerability, read credentials, provision resources, or invent human approval.

Output: explicit acceptance criteria and a proposed architecture decision.

## Architecture reviewer

> Review the proposal against CG-001. Reject token obfuscation, custom-domain-only changes, modified signed claims, retained service-token endpoints, missing media, and unauthenticated spending. Run the baseline, wrong-fix, and server policy candidates; the first two must return nonzero. Confirm ownership, one-session concurrency, the server deadline, persisted budget, finite timeouts, and failure cleanup. Keep the human review state separate from automated results.

Output: actual findings and reproducible policy results, not a simulated approval.

## Implementer

> Keep Azure service authorization and Speech SDK signaling on the server, with browser-to-Azure media unchanged. Preserve asynchronous cleanup and race regressions. Maintain explicit same-origin operator authorization, safe error codes, fixed limits, locked dependencies, a credential-free offline preview, and generic environment-driven configuration. Change only the agreed application scope. Do not deploy or call a paid service without an operator's separate authorization.

Output: code, tests, configuration, and directly related documentation.

## Runtime evaluator

> Begin with offline tests. If a human operator separately authorizes live measurement, verify the deployed source digest, actual browser engine, and explicit destination before authentication. Make one bounded attempt with no automatic paid retry. Measure increasing decoded frames, native dimensions, frame callbacks, and inbound audio; HTTP success alone does not pass. Inspect responses, headers, URLs, and console events without retaining raw credentials, SDP, cookies, HAR, or profiles. Always attempt closure and confirm its result. Record incomplete coverage and failures honestly.

Output: local measured results with source identity and limitations, separate from the bundled recorded example.

## Human reviewer

> Inspect the requirement, rejected alternatives, code, tests, bounded measurements, cost implications, and remaining compatibility risks. Decide whether the change satisfies the intended boundary and operation requirements. Use the actual review system to request changes, approve, or defer. An automated result does not grant production authorization.

The application's example workflow intentionally returns a pending human gate. That value is a demonstration of separation of duties, not a report of a particular repository review.
