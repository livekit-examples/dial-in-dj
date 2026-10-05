import { Room, RoomEvent, Track } from "livekit-client";
import { KINDS, createVisualizer, renderThumbnails } from "./visualizer.js";

const $ = (id) => document.getElementById(id);
// Remember the picked visualizer per browser; storage can be unavailable.
const VIS_KEY = "dial-in-dj.visualizer";
let savedKind = null;
try {
  savedKind = localStorage.getItem(VIS_KEY);
} catch {}
const viz = createVisualizer($("blob"), savedKind || KINDS[0].id);

// ---------- Audio analysis: every remote audio track drives the visualizer ----------
let audioCtx;
let analyser;
const freq = new Uint8Array(512);
const wave = new Uint8Array(1024);
const sources = new Map(); // track sid -> { track, node, el }
let speakerOn = true;

function ensureAudio() {
  if (!audioCtx) {
    audioCtx = new AudioContext();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.6;
  }
  if (audioCtx.state === "suspended") audioCtx.resume();
}
// Browsers only start audio after a gesture; any click will do.
addEventListener("pointerdown", ensureAudio);

function attachAudio(track) {
  ensureAudio();
  const el = track.attach();
  // livekit-client may play through Web Audio, where el.muted has no effect;
  // setVolume works on both paths.
  track.setVolume(speakerOn ? 1 : 0);
  document.body.appendChild(el);
  const node = audioCtx.createMediaStreamSource(new MediaStream([track.mediaStreamTrack]));
  node.connect(analyser);
  sources.set(track.sid, { track, node, el });
}

function detachAudio(track) {
  const s = sources.get(track.sid);
  if (!s) return;
  s.node.disconnect();
  track.detach(s.el);
  s.el.remove();
  sources.delete(track.sid);
}

function tick() {
  if (analyser && sources.size) {
    analyser.getByteTimeDomainData(wave);
    let sum = 0;
    for (const v of wave) sum += ((v - 128) / 128) ** 2;
    const rms = Math.sqrt(sum / wave.length);
    analyser.getByteFrequencyData(freq);
    // Bins are ~47 Hz wide at 48 kHz; the first four cover the kick and bass.
    const bass = (freq[1] + freq[2] + freq[3] + freq[4]) / (4 * 255);
    viz.setAudio(rms * 5, Math.pow(bass, 1.3) * 1.7, freq, audioCtx.sampleRate);
  } else {
    viz.setAudio(0, 0, null);
  }
  requestAnimationFrame(tick);
}
tick();

// ---------- UI helpers ----------
const ui = {
  dock: $("dock"),
  bar: $("bar"),
  dot: $("dot"),
  status: $("status"),
  mic: $("mic-btn"),
  spk: $("spk-btn"),
  end: $("end-btn"),
  callMsg: $("call-msg"),
  capUser: $("cap-user"),
  capAgent: $("cap-agent"),
  webMsg: $("web-msg"),
};
const WEB_HINT = ui.webMsg.textContent;
const CALL_HINT = ui.callMsg.textContent;

// Cross-fade between the dock and the session bar (see .controls in CSS).
function swapControls(show, hide) {
  for (const [el, on] of [[show, true], [hide, false]]) {
    el.classList.toggle("is-off", !on);
    el.inert = !on;
    el.setAttribute("aria-hidden", String(!on));
  }
}
function showBar(text, state = "wait") {
  swapControls(ui.bar, ui.dock);
  setStatus(text, state);
}
function setStatus(text, state = "live") {
  ui.status.textContent = text;
  ui.dot.className = `dot ${state === "live" ? "live" : state === "err" ? "err" : ""}`;
}
function showDock(msg, kind = "", from = "web") {
  swapControls(ui.dock, ui.bar);
  // Browser-session messages go under its button; phone-call ones under the number.
  const web = from === "web";
  ui.callMsg.textContent = !web && msg ? msg : CALL_HINT;
  ui.callMsg.className = `hint ${web ? "" : kind}`;
  ui.webMsg.textContent = web && msg ? msg : WEB_HINT;
  ui.webMsg.className = `hint ${web ? kind : ""}`;
  clearCaptions();
}

// Keep the blob centred in the space the controls leave free.
const fitBlob = new ResizeObserver(() => {
  // The stacked controls are as tall as the taller panel, so this stays steady.
  const h = $("controls").offsetHeight + $("captions").offsetHeight + 40;
  document.documentElement.style.setProperty("--controls-h", `${h}px`);
});
for (const el of [$("controls"), $("captions"), document.body]) fitBlob.observe(el);

// ---------- Captions from lk.transcription text streams ----------
let capTimer;
function clearCaptions() {
  ui.capUser.textContent = "";
  ui.capAgent.textContent = "";
}
function caption(el, text) {
  el.textContent = text;
  clearTimeout(capTimer);
  capTimer = setTimeout(clearCaptions, 7000);
}

// ---------- Session ----------
let room = null;
let mode = "idle"; // idle | web
let micBlocked = false;

const isAgent = (p) => p?.identity?.startsWith("agent-") || p?.isAgent;

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

async function join({ url, token }, { publishMic }) {
  ensureAudio();
  room = new Room({ adaptiveStream: true, dynacast: true });

  room
    .on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === Track.Kind.Audio) attachAudio(track);
    })
    .on(RoomEvent.TrackUnsubscribed, (track) => detachAudio(track))
    .on(RoomEvent.ParticipantConnected, (p) => {
      if (mode === "web" && isAgent(p) && !micBlocked) {
        setStatus("DJ joined. Say what you want to hear.");
      }
    })
    .on(RoomEvent.ParticipantDisconnected, () => {
      if (room && room.remoteParticipants.size === 0) leave("The DJ left.");
    })
    .on(RoomEvent.ParticipantAttributesChanged, (changed, p) => {
      const state = changed["lk.agent.state"];
      if (!state || !isAgent(p)) return;
      const words = {
        listening: "Listening. Ask for any vibe.",
        thinking: "Mixing…",
        speaking: "DJ on the mic.",
        initializing: "DJ warming up…",
      };
      if (words[state] && !micBlocked) setStatus(words[state]);
    })
    .on(RoomEvent.Disconnected, () => {
      if (mode !== "idle") leave();
    });

  room.registerTextStreamHandler("lk.transcription", async (reader, info) => {
    const fromAgent = info.identity?.startsWith("agent-");
    const el = fromAgent ? ui.capAgent : ui.capUser;
    let text = "";
    for await (const chunk of reader) {
      text += chunk;
      caption(el, text);
    }
  });

  await room.connect(url, token);
  if (publishMic) {
    try {
      await room.localParticipant.setMicrophoneEnabled(true);
    } catch {
      // Still worth staying: the DJ starts the music without hearing you.
      micBlocked = true;
      ui.mic.setAttribute("aria-pressed", "false");
    }
  }
  for (const p of room.remoteParticipants.values()) {
    for (const pub of p.audioTrackPublications.values()) {
      if (pub.track && pub.isSubscribed) attachAudio(pub.track);
    }
  }
}

async function leave(msg, kind) {
  const r = room;
  const from = mode;
  room = null;
  mode = "idle";
  for (const { node, el } of sources.values()) {
    node.disconnect();
    el.remove();
  }
  sources.clear();
  showDock(msg, kind, from);
  if (r) await r.disconnect();
}

// The visualizer goes silver while the speaker is muted.
function setSpeaker(on) {
  speakerOn = on;
  viz.setMuted(!on);
  ui.spk.setAttribute("aria-pressed", String(on));
  ui.spk.title = on ? "Mute speaker" : "Unmute speaker";
  for (const { track } of sources.values()) track.setVolume(on ? 1 : 0);
}

// ---------- Actions ----------
$("talk-btn").addEventListener("click", async () => {
  mode = "web";
  micBlocked = false;
  ui.mic.hidden = false;
  ui.mic.setAttribute("aria-pressed", "true");
  ui.end.textContent = "Leave";
  setSpeaker(true);
  showBar("Starting the DJ…");
  try {
    const t = await api("/api/token", {});
    await join({ url: t.server_url, token: t.participant_token }, { publishMic: true });
    const agentHere = [...room.remoteParticipants.values()].some(isAgent);
    if (micBlocked) setStatus("Mic blocked: listening only. Allow the mic to talk.", "wait");
    else if (!agentHere) setStatus("Waiting for the DJ…", "wait");
  } catch (e) {
    leave(`Couldn't start: ${e.message}`, "error");
  }
});

ui.end.addEventListener("click", () => leave());

ui.mic.addEventListener("click", async () => {
  if (!room) return;
  const on = !room.localParticipant.isMicrophoneEnabled;
  try {
    await room.localParticipant.setMicrophoneEnabled(on);
    micBlocked = false;
  } catch {
    setStatus("Mic blocked: allow it in the browser's site settings.", "err");
    return;
  }
  ui.mic.setAttribute("aria-pressed", String(on));
  ui.mic.title = on ? "Mute mic" : "Unmute mic";
});

ui.spk.addEventListener("click", () => setSpeaker(!speakerOn));

// ---------- Visualizer picker: square previews, no text ----------
const vizBtn = $("viz-btn");
const vizPanel = $("viz-panel");
let thumbs = null;

function markCurrent() {
  for (const b of vizPanel.querySelectorAll("button")) {
    b.setAttribute("aria-pressed", String(b.dataset.kind === viz.kind));
  }
}
function buildPicker() {
  for (const kind of KINDS) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "viz-option";
    b.dataset.kind = kind.id;
    b.setAttribute("aria-label", kind.label); // for screen readers; nothing drawn on the image
    b.title = kind.label;
    const img = document.createElement("img");
    img.alt = "";
    img.width = img.height = 88;
    b.appendChild(img);
    b.addEventListener("click", () => {
      viz.setKind(kind.id);
      try {
        localStorage.setItem(VIS_KEY, kind.id);
      } catch {}
      markCurrent();
      togglePicker(false);
    });
    vizPanel.appendChild(b);
  }
  markCurrent();
}
async function fillThumbs() {
  thumbs ??= renderThumbnails();
  const urls = await thumbs;
  for (const b of vizPanel.querySelectorAll("button")) {
    b.querySelector("img").src = urls[b.dataset.kind];
  }
}
function togglePicker(open = vizPanel.hidden) {
  vizPanel.hidden = !open;
  vizBtn.setAttribute("aria-expanded", String(open));
  if (open) {
    markCurrent();
    fillThumbs();
  }
}
buildPicker();
vizBtn.addEventListener("click", () => togglePicker());
addEventListener("pointerdown", (e) => {
  if (!vizPanel.hidden && !vizPanel.contains(e.target) && !vizBtn.contains(e.target)) {
    togglePicker(false);
  }
});
addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !vizPanel.hidden) togglePicker(false);
});
// Render the previews once the page has settled, so opening the picker is instant.
setTimeout(() => fillThumbs().catch(() => {}), 1500);

// ---------- Boot ----------
// The "Call the DJ" column only shows when the server has a number configured
// (DJ_DEMO_NUMBER); otherwise the page is browser-only.
api("/api/config")
  .then(({ phone_number: n }) => {
    if (!n) return;
    const a = $("number");
    a.href = `tel:${n}`;
    const m = n.match(/^\+1(\d{3})(\d{3})(\d{4})$/);
    a.textContent = m ? `+1 (${m[1]}) ${m[2]}-${m[3]}` : n;
    $("dock-call").hidden = false;
    $("dock-divider").hidden = false;
  })
  .catch(() => {});
