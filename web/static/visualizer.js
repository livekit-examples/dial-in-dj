// Visualizer host: one renderer, camera and studio lighting shared by every
// visualizer, plus drag-to-spin, click-to-poke, audio smoothing and the
// silver -> LiveKit blue tint that fades in only while music is playing.
import * as THREE from "three";
import { createMetalBlob } from "./vis/blob.js";
import { IDLE_FREQ, createParticles } from "./vis/particles.js";
import { PRESETS } from "./vis/presets.js";

// First entry is the default.
export const KINDS = [
  { id: "blob", label: "Metallic blob", create: createMetalBlob },
  { id: "orb", label: "Particle orb", create: () => createParticles(PRESETS.orb, 3) },
  { id: "shells", label: "Particle shells", create: () => createParticles(PRESETS.shells, 5) },
  { id: "filaments", label: "Filaments", create: () => createParticles(PRESETS.filaments, 9) },
  { id: "cell", label: "Cell", create: () => createParticles(PRESETS.cell, 11) },
];

function environment(renderer) {
  // Soft studio: a near-black dome with a few light panels,
  // prefiltered once so the chrome has something interesting to reflect.
  const scene = new THREE.Scene();
  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(10, 64, 32),
    new THREE.ShaderMaterial({
      side: THREE.BackSide,
      uniforms: {},
      vertexShader: `varying vec3 vP; void main(){ vP=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
      fragmentShader: `varying vec3 vP; void main(){
        float h=normalize(vP).y*0.5+0.5;
        vec3 low=vec3(0.0), high=vec3(0.08);
        gl_FragColor=vec4(mix(low,high,h),1.0);
      }`,
    }),
  );
  scene.add(dome);
  // Neutral white/grey light: silver when idle. The blue comes from the tint.
  const panels = [
    { c: [1, 1, 1], s: 6, p: [0, 6, 2], r: [Math.PI / 2, 0, 0], w: 6, h: 3 },
    { c: [0.8, 0.8, 0.8], s: 3, p: [-7, 1, 1], r: [0, Math.PI / 2, 0], w: 3, h: 8 },
    { c: [0.8, 0.8, 0.8], s: 3, p: [7, -1, 0], r: [0, -Math.PI / 2, 0], w: 3, h: 8 },
    { c: [0.5, 0.5, 0.5], s: 2, p: [0, -2, -7], r: [0, 0, 0], w: 9, h: 2 },
    { c: [1, 1, 1], s: 8, p: [-3, 3, 6], r: [0, Math.PI, 0.5], w: 0.25, h: 7 },
    { c: [1, 1, 1], s: 6, p: [4, 2, 6], r: [0, Math.PI, -0.3], w: 0.18, h: 6 },
  ];
  for (const d of panels) {
    const m = new THREE.Mesh(
      new THREE.PlaneGeometry(d.w, d.h),
      new THREE.MeshBasicMaterial({
        color: new THREE.Color(...d.c).multiplyScalar(d.s),
        side: THREE.DoubleSide,
      }),
    );
    m.position.set(...d.p);
    m.rotation.set(...d.r);
    scene.add(m);
  }
  const pmrem = new THREE.PMREMGenerator(renderer);
  const env = pmrem.fromScene(scene, 0.02).texture;
  pmrem.dispose();
  return env;
}

function makeRenderer(canvas, pixelRatio, preserve = false) {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
    preserveDrawingBuffer: preserve,
  });
  renderer.setPixelRatio(pixelRatio);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  return renderer;
}

function makeScene(renderer) {
  const scene = new THREE.Scene();
  scene.environment = environment(renderer);
  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100);
  camera.position.set(0, 0, 6.2);
  const spin = new THREE.Group(); // dragging turns this; visualizers live inside
  scene.add(spin);
  return { scene, camera, spin };
}

const idleState = () => ({
  dt: 1 / 60,
  time: 0,
  level: 0,
  bass: 0,
  glow: 0,
  tint: 0,
  freq: null,
  sampleRate: 48000,
  stretch: new THREE.Vector3(),
});

export function createVisualizer(canvas, initialKind = KINDS[0].id) {
  const renderer = makeRenderer(canvas, Math.min(window.devicePixelRatio, 2));
  const { scene, camera, spin } = makeScene(renderer);

  let vis = null;
  let kindId = null;
  function setKind(id) {
    const kind = KINDS.find((k) => k.id === id) || KINDS[0];
    if (kind.id === kindId) return;
    if (vis) {
      spin.remove(vis.root);
      vis.dispose();
    }
    vis = kind.create();
    kindId = kind.id;
    spin.add(vis.root);
  }
  setKind(initialKind);

  // ---------- Drag to spin, click to poke ----------
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const spinVel = new THREE.Vector2(); // radians per second around world y, x
  const stretch = new THREE.Vector3(); // world-space, smoothed (the blob uses it)
  const stretchTarget = new THREE.Vector3();
  const axisX = new THREE.Vector3(1, 0, 0);
  const axisY = new THREE.Vector3(0, 1, 0);
  const qTurn = new THREE.Quaternion();
  let drag = null; // { id, x, y, t }
  let flash = 0; // brief glow boost on click

  function hitTest(e) {
    const r = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    const sphere = new THREE.Sphere(new THREE.Vector3(), vis.radius);
    return raycaster.ray.intersectSphere(sphere, new THREE.Vector3());
  }
  function turn(dx, dy) {
    qTurn.setFromAxisAngle(axisY, dx);
    spin.quaternion.premultiply(qTurn);
    qTurn.setFromAxisAngle(axisX, dy);
    spin.quaternion.premultiply(qTurn);
  }

  canvas.style.touchAction = "none";
  canvas.addEventListener("pointerdown", (e) => {
    const hit = hitTest(e);
    if (!hit) return;
    vis.poke?.(hit);
    flash = 1;
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, t: performance.now() };
    try {
      canvas.setPointerCapture(e.pointerId); // keep the drag if the pointer leaves the canvas
    } catch {}
    canvas.style.cursor = "grabbing";
    spinVel.set(0, 0);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) {
      canvas.style.cursor = hitTest(e) ? "grab" : "";
      return;
    }
    if (e.pointerId !== drag.id) return;
    const now = performance.now();
    const dt = Math.max((now - drag.t) / 1000, 1 / 240);
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    const k = 0.009; // radians per pixel
    turn(dx * k, dy * k);
    // Blend in the latest velocity so release carries the flick.
    spinVel.lerp(new THREE.Vector2((dx * k) / dt, (dy * k) / dt), 0.5);
    const speed = Math.hypot(dx, dy) / dt; // px per second
    stretchTarget.set(dx, -dy, 0).normalize().multiplyScalar(Math.min(speed / 4000, 0.3));
    Object.assign(drag, { x: e.clientX, y: e.clientY, t: now });
  });
  const release = (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    drag = null;
    canvas.style.cursor = hitTest(e) ? "grab" : "";
    if (spinVel.length() > 12) spinVel.setLength(12);
  };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);

  function resize() {
    const { clientWidth: w, clientHeight: h } = canvas;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // Keep the visual a similar size on tall phone screens.
    camera.position.z = w < h ? 6.2 * Math.min(1.6, h / w / 1.1) : 6.2;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(canvas);
  resize();

  // ---------- Audio, tint and the frame loop ----------
  const state = idleState();
  let targetLevel = 0;
  let targetBass = 0;
  let lastSound = -Infinity; // clock time music was last heard
  let muted = false; // speaker toggled off: show silver even if audio arrives

  const clock = new THREE.Clock();
  renderer.setAnimationLoop(() => {
    const dt = Math.min(clock.getDelta(), 0.05);
    const now = clock.elapsedTime;
    // Fast attack, slow release, like a VU meter.
    state.level += (targetLevel - state.level) * (targetLevel > state.level ? 0.5 : 0.08);
    state.bass += (targetBass - state.bass) * (targetBass > state.bass ? 0.45 : 0.07);
    flash *= Math.exp(-dt * 3);
    state.glow = Math.min(1, state.level * 1.5 + state.bass + flash * 0.8);
    // Blue while music plays; back to silver ~1.5 s after it stops, pauses or is muted.
    if (targetLevel > 0.03) lastSound = now;
    const playing = !muted && now - lastSound < 1.5;
    state.tint += ((playing ? 1 : 0) - state.tint) * (1 - Math.exp(-dt * (playing ? 3 : 1.5)));
    canvas.style.setProperty("--tint", state.tint.toFixed(3));
    // Halo around the canvas (CSS drop-shadow) breathes with the music.
    canvas.style.setProperty("--glow", state.glow.toFixed(3));

    if (!drag) {
      // Inertia after a flick, easing to a stop.
      turn(spinVel.x * dt, spinVel.y * dt);
      spinVel.multiplyScalar(Math.exp(-dt * 2.2));
      stretchTarget.multiplyScalar(Math.exp(-dt * 6));
    } else {
      stretchTarget.multiplyScalar(Math.exp(-dt * 4)); // relax when held still
    }
    stretch.lerp(stretchTarget, 0.18); // wobbly follow
    state.stretch.copy(stretch);
    state.dt = dt;
    state.time = now;
    vis.update(state);
    renderer.render(scene, camera);
  });

  return {
    get kind() {
      return kindId;
    },
    setKind,
    setMuted(m) {
      muted = m;
    },
    // level/bass 0..1 drive the blob; freq (byte spectrum) drives the particle bands.
    setAudio(level, bass, freq, sampleRate) {
      targetLevel = Math.max(0, Math.min(1, level));
      targetBass = Math.max(0, Math.min(1, bass));
      state.freq = freq;
      if (sampleRate) state.sampleRate = sampleRate;
    },
  };
}

// Square previews for the picker, rendered from the real visualizers on a
// throwaway offscreen renderer, so they always match what you get. Silver, like
// the page at rest. Yields between kinds so the page stays responsive.
export async function renderThumbnails(size = 176) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const renderer = makeRenderer(canvas, 1, true);
  renderer.setSize(size, size, false);
  const { scene, camera, spin } = makeScene(renderer);
  camera.aspect = 1;
  camera.position.z = 5.4;
  camera.updateProjectionMatrix();
  spin.rotation.set(0.35, 0.6, 0);

  const out = {};
  for (const kind of KINDS) {
    const vis = kind.create();
    spin.add(vis.root);
    const s = { ...idleState(), level: 0.25, bass: 0.2, freq: IDLE_FREQ };
    for (let i = 0; i < 120; i++) {
      s.time = i / 60;
      vis.update(s);
    }
    renderer.render(scene, camera);
    out[kind.id] = canvas.toDataURL("image/png");
    spin.remove(vis.root);
    vis.dispose();
    await new Promise((r) => setTimeout(r, 0));
  }
  renderer.dispose();
  renderer.forceContextLoss();
  return out;
}
