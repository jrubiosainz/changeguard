"use strict";

const el = (id) => document.getElementById(id);
const state = {
  authenticated: false, mode: "", peer: null, sessionId: null, timer: null,
  started: 0, firstFrameMs: null, decodedFrames: 0, audioBytes: 0,
  renderedFrames: 0, width: 0, height: 0, closed: true, speaking: false, startPending: false,
};
let closePromise = null;
const DEMO_TEXT = "This is a real Azure avatar. Service authentication stays on the server. "
  + "My video reaches your browser directly. Evidence makes the change reviewable. "
  + "A human still makes the decision.";
const labels = {
  "operator-authentication-required": "An authenticated operator session is required.",
  "speech-capacity-or-throttle": "Azure reports capacity or throttling. No automatic retry was made.",
  "speech-authorization-failed": "Azure authorization failed. Check the scoped managed identity role.",
  "wait-before-next-session": "The start-rate guard is active. Wait at least 60 seconds between starts.",
  "hourly-demo-budget-reached": "The bounded hourly demo budget is exhausted.",
  "live-avatar-unavailable-in-fixture-mode": "Local fixture mode never pretends to provide a live avatar.",
  "demo-session-already-active": "Another bounded demonstration is active. Try after it has closed.",
  "speech-connect-deadline-exceeded": "Azure negotiation exceeded its deadline; the session was closed.",
  "speech-connection-failed": "The server could not establish the Azure Speech connection.",
  "response-boundary-rejected": "A response violated the server-side boundary guard and was withheld.",
  "operator-access-unavailable-in-preview": "Offline preview needs no login and cannot start Azure sessions.",
};

function ledger(path, status) {
  const row = document.createElement("li");
  const badge = document.createElement("span");
  badge.className = `status${status >= 400 ? " denied" : ""}`;
  badge.textContent = String(status);
  const text = document.createElement("span");
  text.className = "path";
  text.textContent = path;
  row.append(badge, text);
  el("request-ledger").append(row);
  while (el("request-ledger").children.length > 7) {
    el("request-ledger").firstElementChild.remove();
  }
}

async function api(path, body) {
  const options = { credentials: "same-origin", cache: "no-store" };
  if (body !== undefined) {
    options.method = "POST";
    options.headers = { "Content-Type": "application/json", "X-ChangeGuard-Request": "1" };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  ledger(path, response.status);
  const result = await response.json();
  if (!response.ok) throw new Error(labels[result.error] || result.error || "Request failed.");
  return result;
}

function message(text, error = false) {
  el("session-message").textContent = text;
  el("session-message").classList.toggle("error", error);
}

function controls() {
  el("start-avatar").disabled = !state.authenticated || state.mode !== "live-azure"
    || !state.closed || state.sessionId !== null || state.startPending || closePromise !== null;
  el("speak-avatar").disabled = state.closed || state.decodedFrames < 2 || state.speaking;
  el("stop-avatar").disabled = closePromise !== null || (state.closed && state.sessionId === null);
  el("sound-toggle").disabled = state.closed;
  el("operator-status").textContent = state.authenticated ? "Operator authenticated" : "Read-only visitor";
  el("operator-button").textContent = state.authenticated ? "Operator authenticated" : "Unlock operator";
  el("operator-button").disabled = state.authenticated;
  el("operator-button").hidden = state.mode !== "live-azure";
}

async function refreshOperator() {
  const result = await api("/api/operator");
  state.authenticated = result.authenticated;
  state.mode = result.executionMode;
  controls();
  if (state.mode !== "live-azure") {
    el("player-state").textContent = "Recorded example / offline preview";
    message("Read-only preview: a recorded still and synthetic policy fixtures. No Azure connection or login.");
  } else {
    message("The still is a recorded example. A new billable session requires authenticated operator access.");
  }
}

async function refreshEvidence() {
  const report = await api("/api/evidence");
  el("evidence-checks").replaceChildren();
  if (report.kind !== "recorded-example") {
    el("evidence-summary").textContent = "No live browser report has been published yet. "
      + "An HTTP health check is not proof of playback.";
    return;
  }
  const recordedAt = new Date(report.provenance.recordedAt).toISOString().slice(0, 19).replace("T", " ");
  el("evidence-summary").textContent = `RECORDED EXAMPLE / WebKit ${report.browser.version} / ${recordedAt} UTC. `
    + "One bounded service session, not an execution of this checkout or continuous monitoring.";
  report.checks.forEach((check) => {
    const card = document.createElement("article");
    card.className = "check-card";
    const top = document.createElement("div");
    top.className = "check-top";
    const title = document.createElement("strong");
    title.textContent = check.id;
    const badge = document.createElement("span");
    badge.className = `badge ${check.status === "pass" ? "green" : "amber"}`;
    badge.textContent = check.status.toUpperCase();
    const detail = document.createElement("p");
    detail.textContent = check.details;
    top.append(title, badge);
    card.append(top, detail);
    el("evidence-checks").append(card);
  });
}

async function runComparison() {
  const report = await api("/api/comparison");
  el("comparison-results").replaceChildren();
  report.cases.forEach((item) => {
    const row = document.createElement("span");
    row.className = `comparison-case${item.passed ? " safe" : ""}`;
    const status = document.createElement("strong");
    status.textContent = item.passed ? "FIXTURE PASS" : "REJECTED";
    const name = document.createElement("span");
    name.textContent = item.id;
    row.append(status, name);
    el("comparison-results").append(row);
  });
  const note = document.createElement("span");
  note.textContent = "SYNTHETIC ONLY. The passing fixture does not prove live media.";
  el("comparison-results").append(note);
}

function hasRelayForEveryMediaSection(sdp) {
  if (typeof sdp !== "string") return false;
  const sections = sdp.split(/\r?\nm=/).slice(1)
    .filter(section => section.split(/\s+/)[1] !== "0");
  return sections.some(section => section.startsWith("video "))
    && sections.some(section => section.startsWith("audio "))
    && sections.every(section => /^a=candidate:[^\r\n]+ typ relay(?:[ \r\n]|$)/m.test(section));
}

function waitForIce(peer, timeoutMs = 12000) {
  return new Promise((resolve, reject) => {
    let deadline;
    const finish = (error) => {
      clearTimeout(deadline);
      peer.removeEventListener("icecandidate", changed);
      peer.removeEventListener("icegatheringstatechange", changed);
      if (error) reject(error); else resolve();
    };
    function changed() {
      // Non-trickle server signaling needs usable relay paths, not every possible network probe.
      if (peer.connectionState === "closed") {
        finish(new Error("The peer closed before ICE relay readiness."));
      } else if (hasRelayForEveryMediaSection(peer.localDescription?.sdp)) {
        finish();
      } else if (peer.iceGatheringState === "complete") {
        finish(new Error("ICE ended without a relay candidate for every active media section."));
      }
    }
    deadline = setTimeout(() => {
      finish(new Error("ICE relay readiness exceeded its 12-second deadline."));
    }, timeoutMs);
    peer.addEventListener("icecandidate", changed);
    peer.addEventListener("icegatheringstatechange", changed);
    changed();
  });
}

function frameCallback() {
  if (state.closed) return;
  state.renderedFrames += 1;
  if (state.firstFrameMs === null) state.firstFrameMs = performance.now() - state.started;
  el("player-placeholder").hidden = true;
  el("avatar-video").requestVideoFrameCallback(frameCallback);
}

async function updateStats() {
  if (state.closed || !state.peer) return;
  const statistics = await state.peer.getStats();
  statistics.forEach((entry) => {
    if (entry.type === "inbound-rtp" && entry.kind === "video") {
      state.decodedFrames = entry.framesDecoded || 0;
    }
    if (entry.type === "inbound-rtp" && entry.kind === "audio") {
      state.audioBytes = entry.bytesReceived || 0;
    }
  });
  state.width = el("avatar-video").videoWidth;
  state.height = el("avatar-video").videoHeight;
  el("frames").textContent = state.decodedFrames.toLocaleString();
  el("dimensions").textContent = state.width ? `${state.width} x ${state.height}` : "--";
  el("audio-bytes").textContent = state.audioBytes.toLocaleString();
  el("first-frame").textContent = state.firstFrameMs === null ? "--" : `${(state.firstFrameMs / 1000).toFixed(1)}s`;
  const remaining = Math.max(0, 90 - Math.floor((performance.now() - state.started) / 1000));
  el("session-clock").textContent = `${remaining}s remaining`;
  if (state.decodedFrames > 1 && state.width > 0) {
    el("player-state").textContent = "Live Azure media / decoded frames verified";
    el("live-dot").classList.add("active");
  }
  controls();
  if (remaining === 0) await stopAvatar("90-second deadline reached. Session closed.");
}

async function startAvatar() {
  if (state.startPending || closePromise || state.sessionId || !state.closed) {
    message("Finish closing the existing session before starting another.", true);
    return;
  }
  state.startPending = true;
  state.closed = false;
  state.started = performance.now();
  state.firstFrameMs = null;
  state.renderedFrames = state.decodedFrames = state.audioBytes = state.width = state.height = 0;
  el("recorded-avatar").hidden = true;
  el("recorded-label").hidden = true;
  el("player-placeholder").hidden = false;
  controls();
  el("player-state").textContent = "Preparing authenticated transport";
  message("Server-managed authentication. Azure service credentials never enter this browser.");
  try {
    const prepared = await api("/api/avatar/prepare", {});
    state.sessionId = prepared.sessionId;
    if (state.closed) {
      await stopAvatar("Preparation canceled; server session closed.");
      return;
    }
    const peer = new RTCPeerConnection({ iceServers: prepared.iceServers, iceTransportPolicy: "relay" });
    state.peer = peer;
    peer.addEventListener("track", (event) => {
      const target = event.track.kind === "video" ? el("avatar-video") : el("avatar-audio");
      target.srcObject = new MediaStream([event.track]);
      target.muted = true;
      target.play().catch(() => message("Browser blocked autoplay. Use the sound control if needed."));
      if (event.track.kind === "video") target.requestVideoFrameCallback(frameCallback);
    });
    peer.addEventListener("connectionstatechange", () => {
      if (peer.connectionState === "failed") {
        stopAvatar("WebRTC connectivity failed. Check TURN/UDP firewall policy.").catch(reportError);
      }
    });
    peer.addEventListener("datachannel", (event) => { event.channel.onmessage = () => {}; });
    peer.createDataChannel("eventChannel");
    peer.addTransceiver("video", { direction: "sendrecv" });
    peer.addTransceiver("audio", { direction: "sendrecv" });
    await peer.setLocalDescription(await peer.createOffer());
    await waitForIce(peer);
    if (state.closed || state.peer !== peer) throw new Error("Session ended before SDP negotiation.");
    el("player-state").textContent = "Server negotiating SDP / awaiting real media";
    const answer = await api("/api/avatar/connect", {
      sessionId: state.sessionId,
      offer: { type: peer.localDescription.type, sdp: peer.localDescription.sdp },
    });
    if (state.closed) throw new Error("Session was ended during negotiation.");
    await peer.setRemoteDescription(answer);
    state.timer = setInterval(() => updateStats().catch(reportError), 500);
    setTimeout(() => {
      if (!state.closed && state.decodedFrames < 2) {
        stopAvatar("No decoded video frames within 25 seconds. HTTP success was not accepted as playback.").catch(reportError);
      }
    }, 25000);
    await updateStats();
  } catch (error) {
    if (await stopAvatar("Start failed; transport closed.")) reportError(error);
  } finally {
    state.startPending = false;
    controls();
  }
}

async function speak() {
  state.speaking = true;
  controls();
  el("spoken-caption").textContent = DEMO_TEXT;
  try {
    await api("/api/avatar/speak", { sessionId: state.sessionId, text: DEMO_TEXT });
    message("Speech synthesis completed. Confirm the continuing decoded frames and inbound audio.");
  } catch (error) {
    reportError(error);
  } finally {
    state.speaking = false;
    controls();
  }
}

function stopAvatar(reason = "Session closed. Final browser metrics are retained for inspection.") {
  if (closePromise) return closePromise;
  if (state.closed && state.sessionId === null) return Promise.resolve(true);
  state.closed = true;
  clearInterval(state.timer);
  const id = state.sessionId;
  if (state.peer) state.peer.close();
  state.peer = null;
  el("avatar-audio").srcObject = null;
  el("live-dot").classList.remove("active");
  el("player-state").textContent = "Session ended / final measured values retained";
  el("session-clock").textContent = "Closed";
  closePromise = (async () => {
    try {
      if (id) await api("/api/avatar/stop", { sessionId: id });
      if (state.sessionId === id) {
        state.sessionId = null;
        message(reason);
      }
      return true;
    } catch (error) {
      if (state.sessionId === id) {
        message(`Close request failed: ${error.message} New starts are blocked; retry End session.`, true);
      }
      return false;
    }
  })().finally(() => {
    closePromise = null;
    controls();
  });
  controls();
  return closePromise;
}

function reportError(error) {
  message(error instanceof Error ? error.message : "Unexpected browser operation failure.", true);
}

el("operator-button").addEventListener("click", () => el("operator-dialog").showModal());
el("close-dialog").addEventListener("click", () => el("operator-dialog").close());
el("operator-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/operator/login", { token: el("operator-token").value });
    el("operator-token").value = "";
    el("operator-dialog").close();
    await refreshOperator();
  } catch (error) {
    el("operator-token").value = "";
    el("login-error").textContent = error.message;
  }
});
el("run-comparison").addEventListener("click", () => runComparison().catch(reportError));
el("evidence-refresh").addEventListener("click", () => refreshEvidence().catch(reportError));
el("start-avatar").addEventListener("click", () => startAvatar().catch(reportError));
el("speak-avatar").addEventListener("click", () => speak().catch(reportError));
el("stop-avatar").addEventListener("click", () => stopAvatar().catch(reportError));
el("sound-toggle").addEventListener("click", () => {
  el("avatar-audio").muted = !el("avatar-audio").muted;
  el("sound-toggle").textContent = el("avatar-audio").muted ? "Sound off" : "Sound on";
  el("sound-toggle").setAttribute("aria-pressed", String(!el("avatar-audio").muted));
});
window.addEventListener("pagehide", () => {
  if (state.sessionId) {
    fetch("/api/avatar/stop", {
      method: "POST", credentials: "same-origin", keepalive: true,
      headers: { "Content-Type": "application/json", "X-ChangeGuard-Request": "1" },
      body: JSON.stringify({ sessionId: state.sessionId }),
    }).catch(() => {
      console.warn("Navigation interrupted the close request; the server deadline remains enforced.");
    });
  }
  if (state.peer) state.peer.close();
});
window.changeguard = Object.freeze({
  readStats: () => ({
    decodedFrames: state.decodedFrames, renderedFrames: state.renderedFrames,
    width: state.width, height: state.height, inboundAudioBytes: state.audioBytes,
    firstFrameMs: state.firstFrameMs, closed: state.closed,
    connectionState: state.peer ? state.peer.connectionState : "closed",
  }),
  stop: stopAvatar,
});
(async () => {
  const health = await api("/api/health");
  el("environment").textContent = health.executionMode === "live-azure" ? "AZURE / OPERATOR-GATED" : "LOCAL / OFFLINE PREVIEW";
  el("build-label").textContent = `Source ${health.sourceCommit.slice(0, 12)}`;
  await refreshOperator();
  await refreshEvidence();
  const obsolete = await fetch("/api/getSpeechToken", { cache: "no-store" });
  ledger("/api/getSpeechToken", obsolete.status);
  if (obsolete.status !== 404) throw new Error("The obsolete token route is unexpectedly reachable.");
})().catch(reportError);
