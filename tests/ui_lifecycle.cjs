const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const response = (ok, body) => ({
  ok, status: ok ? 200 : 502, json: async () => body,
});

function harness() {
  const elements = new Map();
  const element = () => ({
    children: [], classList: { toggle() {}, remove() {}, add() {} },
    addEventListener() {}, append(...items) { this.children.push(...items); },
    textContent: "", disabled: false,
  });
  const requests = [];
  const context = vm.createContext({
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
      },
      createElement: element,
    },
    window: { addEventListener() {} },
    fetch(url, options) {
      const pending = deferred();
      requests.push({ url, options, pending });
      return pending.promise;
    },
    performance: { now: () => 0 },
    clearInterval() {}, setInterval() {}, setTimeout, clearTimeout,
    console,
    RTCPeerConnection: class {
      constructor() { throw new Error("Canceled preparation must not construct a media peer"); }
    },
  });
  vm.runInContext(source + "\nglobalThis.testApi = { state, controls, startAvatar, stopAvatar, hasRelayForEveryMediaSection, waitForIce };", context);
  const api = context.testApi;
  Object.assign(api.state, { authenticated: true, mode: "live-azure" });
  return { api, elements, requests };
}

test("pending and failed closes retain ownership and prevent restart", async () => {
  const { api, elements, requests } = harness();
  Object.assign(api.state, {
    sessionId: "synthetic-original-session", closed: false, peer: { close() {} },
  });
  const closing = api.stopAvatar();
  assert.equal(api.stopAvatar(), closing);
  assert.equal(api.state.sessionId, "synthetic-original-session");
  assert.equal(elements.get("start-avatar").disabled, true);
  await api.startAvatar();
  assert.equal(requests.filter(item => item.url === "/api/avatar/prepare").length, 0);

  const firstStop = requests.find(item => item.url === "/api/avatar/stop");
  firstStop.pending.resolve(response(false, { error: "speech-close-failed" }));
  assert.equal(await closing, false);
  assert.equal(api.state.sessionId, "synthetic-original-session");
  assert.equal(elements.get("start-avatar").disabled, true);

  const retry = api.stopAvatar();
  requests.at(-1).pending.resolve(response(true, { closed: true }));
  assert.equal(await retry, true);
  assert.equal(api.state.sessionId, null);
  assert.equal(elements.get("start-avatar").disabled, false);
});

test("stale close response cannot overwrite another handle", async () => {
  const { api, requests } = harness();
  Object.assign(api.state, { sessionId: "synthetic-old-session", closed: false });
  const closing = api.stopAvatar();
  api.state.sessionId = "synthetic-new-session";
  api.state.closed = false;
  requests.at(-1).pending.resolve(response(false, { error: "speech-close-failed" }));
  await closing;
  assert.equal(api.state.sessionId, "synthetic-new-session");
});

test("canceled preparation stays blocked until its eventual server handle closes", async () => {
  const { api, elements, requests } = harness();
  const starting = api.startAvatar();
  const preparation = requests.find(item => item.url === "/api/avatar/prepare");
  await api.stopAvatar();
  assert.equal(elements.get("start-avatar").disabled, true);
  preparation.pending.resolve(response(true, { sessionId: "synthetic-prepared-session", iceServers: [] }));
  for (let index = 0; index < 8; index++) await Promise.resolve();
  const stop = requests.find(item => item.url === "/api/avatar/stop");
  assert.ok(stop);
  assert.equal(JSON.parse(stop.options.body).sessionId, "synthetic-prepared-session");
  stop.pending.resolve(response(true, { closed: true }));
  await starting;
  assert.equal(api.state.sessionId, null);
  assert.equal(elements.get("start-avatar").disabled, false);
});

const relay = "a=candidate:1 1 udp 1 192.0.2.1 9 typ relay\r\n";
const section = (kind, candidate = "") => `m=${kind} 9 UDP/TLS/RTP/SAVPF 96\r\n${candidate}`;
const validOffer = `v=0\r\n${section("video", relay)}${section("audio", relay)}${section("application", relay)}`;

function peerDouble(sdp, state = "gathering") {
  const listeners = new Map();
  return {
    localDescription: { sdp }, iceGatheringState: state, connectionState: "new",
    addEventListener(name, handler) { listeners.set(name, handler); },
    removeEventListener(name) { listeners.delete(name); },
    emit(name) { listeners.get(name)?.(); },
    listeners,
  };
}

test("nontrickle SDP requires a real relay candidate in every active media section", () => {
  const { api } = harness();
  assert.equal(api.hasRelayForEveryMediaSection(validOffer), true);
  assert.equal(api.hasRelayForEveryMediaSection("v=0\r\n"), false);
  assert.equal(api.hasRelayForEveryMediaSection(
    `v=0\r\n${section("video", relay)}${section("audio")}`
  ), false);
  assert.equal(api.hasRelayForEveryMediaSection(validOffer.replaceAll("typ relay", "typ host")), false);
});

test("usable relays unblock while other ICE probes are still gathering", async () => {
  const { api } = harness();
  const peer = peerDouble(`v=0\r\n${section("video")}${section("audio")}`);
  const ready = api.waitForIce(peer, 100);
  peer.localDescription.sdp = validOffer;
  peer.emit("icecandidate");
  await ready;
  assert.equal(peer.iceGatheringState, "gathering");
  assert.equal(peer.listeners.size, 0);
});

test("already gathered usable relays cannot miss the readiness event", async () => {
  const { api } = harness();
  const peer = peerDouble(validOffer);
  await api.waitForIce(peer, 100);
  assert.equal(peer.listeners.size, 0);
});

test("empty or partial relay offers still fail at a finite deadline or completion", async () => {
  const { api } = harness();
  const empty = peerDouble("v=0\r\n");
  await assert.rejects(api.waitForIce(empty, 5), /deadline/);
  assert.equal(empty.listeners.size, 0);
  const partial = peerDouble(`v=0\r\n${section("video", relay)}${section("audio")}`, "complete");
  await assert.rejects(api.waitForIce(partial, 100), /every active media/);
  assert.equal(partial.listeners.size, 0);
});

test("a closed or different peer's old candidates cannot satisfy current readiness", async () => {
  const { api } = harness();
  const old = peerDouble(validOffer);
  old.connectionState = "closed";
  await assert.rejects(api.waitForIce(old, 100), /peer closed/);
  const current = peerDouble("v=0\r\n");
  const pending = api.waitForIce(current, 5);
  old.emit("icecandidate");
  await assert.rejects(pending, /deadline/);
});

test("BUNDLE declaration alone does not substitute a candidate from another media section", () => {
  const { api } = harness();
  const offered = "v=0\r\na=group:BUNDLE 0 1\r\n"
    + section("video", `a=mid:0\r\n${relay}`)
    + section("audio", "a=mid:1\r\n");
  assert.equal(api.hasRelayForEveryMediaSection(offered), false);
  assert.equal(api.hasRelayForEveryMediaSection(offered + relay), true);
});
