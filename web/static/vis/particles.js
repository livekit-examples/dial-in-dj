// Particle-sphere visualizers ported from Particula
// (https://github.com/Humprt/particula, MIT (c) 2025 Humprt). See THIRD_PARTY_NOTICES.md.
// Each sphere listens to its own frequency band: particles drift on a noise
// field, get pushed outward on beats, and are held inside the sphere radius.
import * as THREE from "three";
import { createNoise3D } from "./simplex.js";

// Particula's camera sits at z=2.5; ours is further back, so points scale up
// to look the same. Presets are capped at MAX_PARTICLES total to keep the CPU
// loop cheap.
const SIZE_SCALE = 3;
const MAX_PARTICLES = 20000;
// While silent, drive the spheres with a gentle fixed spectrum so each preset
// holds its shape (Particula always has music; with none, particles clump).
// Also used for the picker previews.
export const IDLE_FREQ = new Uint8Array(512).map((_, i) => 150 * Math.exp(-i / 120) + 40);
const SILVER = new THREE.Color(0xffffff);
const BLUE = new THREE.Color(0x1fd5f9);

function bandEnergy(freq, sampleRate, lo, hi, gain) {
  if (!freq || !sampleRate) return 0;
  const binHz = sampleRate / 2 / freq.length;
  const a = Math.max(0, Math.round(lo / binHz));
  const b = Math.min(freq.length - 1, Math.round(hi / binHz));
  let sum = 0;
  for (let i = a; i <= b; i++) sum += Math.min(freq[i] * gain, 255);
  return b >= a ? sum / (b - a + 1) : 0; // 0..255, like Particula
}

function nextNoiseScale(p, last) {
  // Particula's jump: move a random number of noiseStep increments up or down.
  const min = p.minNoiseScale;
  const max = Math.max(p.maxNoiseScale, min + 0.1);
  const step = Math.min(p.noiseStep || 0.1, (max - min) / 2);
  const cur = Math.max(min, Math.min(last, max));
  const up = Math.floor((max - cur) / step);
  const down = Math.floor((cur - min) / step);
  const dir = Math.random() < 0.5 && down > 0 ? -1 : 1;
  const n = Math.floor(Math.random() * ((dir > 0 ? up : down) + 1));
  return Math.max(min, Math.min(cur + dir * n * step, max));
}

function spawn(pos, i3, p) {
  const radius = p.sphereRadius * p.innerSphereRadius;
  const theta = Math.random() * Math.PI * 2;
  const phi = Math.acos(2 * Math.random() - 1);
  const r = Math.cbrt(Math.random()) * radius;
  pos[i3] = r * Math.sin(phi) * Math.cos(theta);
  pos[i3 + 1] = r * Math.sin(phi) * Math.sin(theta);
  pos[i3 + 2] = r * Math.cos(phi);
}

function createSphere(params, noise, index, density) {
  const p = { ...params };
  const count = Math.max(500, Math.round(p.particleCount * density));
  const pos = new Float32Array(count * 3);
  const vel = new Float32Array(count * 3);
  const life = new Float32Array(count);
  const beat = new Float32Array(count);
  const colors = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    spawn(pos, i * 3, p);
    life[i] = Math.random() * p.particleLifetime;
    // Grey gradient across the sphere; the material colour adds the tint.
    const g = 0.5 + 0.5 * (i / count);
    colors[i * 3] = colors[i * 3 + 1] = colors[i * 3 + 2] = g;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  const material = new THREE.PointsMaterial({
    size: p.particleSize * SIZE_SCALE,
    vertexColors: true,
    transparent: true,
    opacity: 0.85,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    // ACES tone mapping bleaches bright additive blue towards white; skip it.
    toneMapped: false,
  });
  const points = new THREE.Points(geometry, material);
  const baseSize = material.size;

  const history = [];
  let lastPeak = 0;
  let rotSpeed = 0;
  let pokeDir = null;
  let pokeAge = 0;

  return {
    points,
    material,
    poke(worldHit) {
      // In this sphere's own frame, since each sphere spins on its own.
      pokeDir = points.worldToLocal(worldHit.clone()).normalize();
      pokeAge = 0;
    },
    update(s) {
      const k = Math.min(s.dt * 60, 3); // Particula steps once per 60 fps frame
      const freq = s.freq || IDLE_FREQ;
      const energy = bandEnergy(freq, s.sampleRate, p.minFrequency, p.maxFrequency, p.gainMultiplier);
      const beatEnergy = bandEnergy(
        freq, s.sampleRate, p.minFrequencyBeat, p.maxFrequencyBeat, p.gainMultiplier,
      );
      history.push(energy);
      if (history.length > 30) history.shift();
      const avg = history.reduce((a, b) => a + b, 0) / history.length;
      const now = performance.now();
      if (energy > avg * p.peakSensitivity && energy > 8 && now - lastPeak > 200) {
        lastPeak = now;
        if (p.dynamicNoiseScale) p.noiseScale = nextNoiseScale(p, p.noiseScale);
      }
      const beatHit = beatEnergy > p.beatThreshold;

      const ns = p.noiseScale;
      const tf = s.time * p.noiseSpeed;
      const turb = p.turbulenceStrength * k;
      const beatForce = p.beatStrength * k;
      const R = p.sphereRadius;
      const damp = Math.pow(0.98, k);
      const pokeOn = pokeDir && pokeAge < 0.6;
      const pokeF = pokeOn ? 0.02 * (1 - pokeAge / 0.6) * k : 0;
      for (let i = 0; i < count; i++) {
        const i3 = i * 3;
        let x = pos[i3], y = pos[i3 + 1], z = pos[i3 + 2];
        let vx = vel[i3], vy = vel[i3 + 1], vz = vel[i3 + 2];
        vx += noise(x * ns + tf, y * ns, z * ns) * turb;
        vy += noise(x * ns, y * ns + tf, z * ns) * turb;
        vz += noise(x * ns, y * ns, z * ns + tf) * turb;
        let be = beatHit ? 1 : beat[i] * Math.pow(0.95, k);
        const d = Math.sqrt(x * x + y * y + z * z) || 1e-6;
        if (be > 0.01) {
          const f = be * beatForce;
          vx += (x / d) * f;
          vy += (y / d) * f;
          vz += (z / d) * f;
        }
        if (pokeOn) {
          // Click: particles facing the click burst outward.
          const facing = (x * pokeDir.x + y * pokeDir.y + z * pokeDir.z) / d;
          if (facing > 0.5) {
            const f = pokeF * (facing - 0.5) * 2;
            vx += (x / d) * f;
            vy += (y / d) * f;
            vz += (z / d) * f;
          }
        }
        x += vx * k;
        y += vy * k;
        z += vz * k;
        vx *= damp;
        vy *= damp;
        vz *= damp;
        const d2 = Math.sqrt(x * x + y * y + z * z);
        if (d2 > R) {
          const pull = (d2 - R) * 0.1;
          x -= (x / d2) * pull;
          y -= (y / d2) * pull;
          z -= (z / d2) * pull;
          vx *= 0.9;
          vy *= 0.9;
          vz *= 0.9;
        }
        let lt = life[i] - s.dt;
        if (lt <= 0) {
          spawn(pos, i3, p);
          x = pos[i3];
          y = pos[i3 + 1];
          z = pos[i3 + 2];
          vx = vy = vz = 0;
          lt = Math.random() * p.particleLifetime;
          be = 0;
        }
        pos[i3] = x;
        pos[i3 + 1] = y;
        pos[i3 + 2] = z;
        vel[i3] = vx;
        vel[i3 + 1] = vy;
        vel[i3 + 2] = vz;
        life[i] = lt;
        beat[i] = be;
      }
      geometry.attributes.position.needsUpdate = true;
      if (pokeDir) pokeAge += s.dt;

      // Spin faster when this sphere's band is loud.
      const target = p.rotationSpeedMin + (p.rotationSpeedMax - p.rotationSpeedMin) * (energy / 255);
      rotSpeed += (target - rotSpeed) * (p.rotationSmoothness ?? 0.3);
      points.rotation.y += rotSpeed * k * (index % 2 ? -1 : 1) * 0.5;

      // Bigger points while playing make the blue read as a glow; the colour
      // itself stays at full-strength blue (brighter saturates to white).
      material.color.copy(SILVER).lerp(BLUE, s.tint);
      material.size = baseSize * (1 + 0.6 * s.tint);
      material.opacity = 0.7 + 0.3 * s.tint;
    },
    dispose() {
      geometry.dispose();
      material.dispose();
    },
  };
}

export function createParticles(spheres, seed = 7) {
  const noise = createNoise3D(seed);
  const root = new THREE.Group();
  const total = spheres.reduce((n, sp) => n + sp.particleCount, 0);
  const density = Math.min(1, MAX_PARTICLES / total);
  const parts = spheres.map((params, i) => createSphere(params, noise, i, density));
  for (const part of parts) root.add(part.points);
  // Scale every preset to roughly the blob's size so switching doesn't jump.
  const radius = Math.max(...spheres.map((sp) => sp.sphereRadius));
  root.scale.setScalar(1.4 / radius);
  // Particles spawn near the centre; run the sim for a moment so it opens fully formed.
  const warm = { dt: 1 / 60, time: 0, tint: 0, freq: null, sampleRate: 48000 };
  for (let i = 0; i < 90; i++) {
    warm.time = i / 60;
    for (const part of parts) part.update(warm);
  }
  return {
    root,
    radius: 1.4 * 1.1,
    update(s) {
      for (const part of parts) part.update(s);
    },
    poke(worldHit) {
      for (const part of parts) part.poke(worldHit);
    },
    dispose() {
      for (const part of parts) part.dispose();
    },
  };
}
