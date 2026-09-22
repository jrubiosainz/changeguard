# Recorded example and measurement scope

The offline UI includes a still and a derived report from one existing Azure stock-avatar session observed in **WebKit 26.6 on macOS**. They illustrate the experience and the shape of a bounded measurement. They are not a new execution of the current repository checkout.

The [example JSON](../static/examples/recorded-session.json) keeps the measured values while adapting public metadata. Its provenance includes the original report's SHA-256, the original report timestamp, and `transformation: "public metadata adaptation only"`. It does not attach a new source commit to that execution.

| Observation | Recorded value and interpretation |
| --- | --- |
| Decoded video frames | **606**, from inbound RTP `framesDecoded` |
| Native total-video-frame counter | **610**, from `getVideoPlaybackQuality().totalVideoFrames`; can include dropped frames and is not a decoded-frame count |
| Native video dimensions | **1920 x 1080** |
| Inbound audio | **150,615 bytes**, with an increase during synthesis |
| First frame callback | **12,216 ms** from the operator start |
| Bounded evaluation duration | **73.36 seconds**, including authorization, checks, and closure |
| Application response bodies inspected | **12**, with headers, URLs, and console events also inspected |
| Boundary findings / inspection errors | **0 / 0** within that run's observed coverage |
| Synthesis and closure | Synthesis request completed; authenticated status confirmed the owned session closed |

The report timestamp is `2026-09-21T20:12:55.775342+00:00`. It is a recorded report timestamp, not the time the current page was loaded.

## Artifact identity

| Artifact | SHA-256 |
| --- | --- |
| Public derived JSON | `701ce1b0b0dc27e01b0156e56cf8345cd4334cb9060d9fb1534bfca25e763059` |
| Original report identified by provenance | `9e7df206099d3983126c1b4848a2e2be14aa7375388f6512cc839bae449990f1` |
| Recorded-avatar still | `86c0f7f7b152cdc7d567976ae51922c3e29c4613c9252f4e48b4d33a085f0c6b` |

The original report's hash does **not** describe the derived JSON bytes. The 730 x 411 PNG is a lossless crop of recorded media pixels, without interface panels, added pixels, or embedded metadata. It is Azure stock-avatar service output, not original filmed footage or a newly generated replacement.

## What the example does not establish

A still image alone cannot demonstrate video continuity or audible playback. Audio transport bytes do not guarantee that a listener heard the output. The metrics describe the recorded measurement, not continuous monitoring, all browsers, every network, or current Azure availability.

The synthetic browser-authentication baseline and encoded-token comparison never used a working credential. They test the policy scanner, not live before/after credential leakage.

Fresh offline tests exercise current source independently. New live claims require a separately authorized run that records its actual source identity, engine, timing, inspection coverage, and cleanup outcome. Generated results remain under `.runtime/` until explicitly reviewed for sharing.
