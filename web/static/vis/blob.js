// Liquid-metal blob: a chrome sphere displaced by 3D simplex noise, with
// normals recomputed in the vertex shader so reflections follow the ripples.
// Clicks send ripples across the surface; drags stretch it along the motion.
import * as THREE from "three";

// GLSL 3D simplex noise from webgl-noise (https://github.com/ashima/webgl-noise),
// MIT (c) 2011 Ashima Arts and 2011-2016 Stefan Gustavson. See THIRD_PARTY_NOTICES.md.
const NOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0);
  const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy));
  vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);
  vec3 l=1.0-g;
  vec3 i1=min(g.xyz,l.zxy);
  vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;
  vec3 x2=x0-i2+C.yyy;
  vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857;
  vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z);
  vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy;
  vec4 y=y_*ns.x+ns.yyyy;
  vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);
  vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0;
  vec4 s1=floor(b1)*2.0+1.0;
  vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;
  vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);
  vec3 p1=vec3(a0.zw,h.y);
  vec3 p2=vec3(a1.xy,h.z);
  vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);
  m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
uniform float uTime;
uniform float uLevel;
uniform float uBass;
uniform vec3 uStretch; // object-space drag direction, length = amount
uniform vec4 uRipples[4]; // xyz = object-space hit direction, w = age in seconds (<0 = off)
float rippleDisp(vec3 n){
  float sum=0.0;
  for(int i=0;i<4;i++){
    float age=uRipples[i].w;
    if(age<0.0) continue;
    float dist=acos(clamp(dot(n,uRipples[i].xyz),-1.0,1.0)); // 0..pi across the surface
    float front=age*2.6;                                       // ring travels outward
    float env=exp(-age*1.5)*exp(-pow(dist-front,2.0)*6.0);     // fades with age, lives near the ring
    sum+=sin((dist-front)*12.0)*env*0.15;
  }
  return sum;
}
float blobDisp(vec3 p){
  float t=uTime;
  float slow=snoise(p*0.9+vec3(0.0,t*0.18,t*0.11));
  float fast=snoise(p*(1.6+uBass*1.0)+vec3(t*0.55,0.0,-t*0.4));
  float s=length(uStretch);
  float d=s>0.0001 ? dot(p,uStretch/s) : 0.0;
  return slow*(0.12+uBass*0.3)+fast*(0.012+uLevel*0.16)+s*(d*d-0.33)+rippleDisp(p);
}
vec3 blobPos(vec3 p){
  vec3 n=normalize(p);
  return p+n*blobDisp(n);
}
`;

const SILVER = new THREE.Color(0xffffff);
const BLUE = new THREE.Color(0x4fd8ff);
const RADIUS = 1.25;

export function createMetalBlob() {
  const uniforms = {
    uTime: { value: 0 },
    uLevel: { value: 0 },
    uBass: { value: 0 },
    uStretch: { value: new THREE.Vector3() },
    uGlow: { value: 0 },
    uTint: { value: 0 }, // 0 = silver (silent), 1 = LiveKit blue (music playing)
    uRipples: { value: Array.from({ length: 4 }, () => new THREE.Vector4(0, 0, 1, -1)) },
  };
  let nextRipple = 0;
  const material = new THREE.MeshPhysicalMaterial({
    color: 0xffffff,
    metalness: 1,
    roughness: 0.16,
    clearcoat: 1,
    clearcoatRoughness: 0.05,
    envMapIntensity: 1.25,
  });
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", `#include <common>\n${NOISE}`)
      .replace(
        "#include <beginnormal_vertex>",
        /* glsl */ `
        vec3 bN=normalize(position);
        vec3 bT=normalize(cross(bN, abs(bN.y)<0.99 ? vec3(0.0,1.0,0.0) : vec3(1.0,0.0,0.0)));
        vec3 bB=normalize(cross(bN,bT));
        float bR=length(position);
        float bE=0.015;
        vec3 bP0=blobPos(position);
        vec3 bP1=blobPos(normalize(bN+bT*bE)*bR);
        vec3 bP2=blobPos(normalize(bN+bB*bE)*bR);
        vec3 objectNormal=normalize(cross(bP1-bP0,bP2-bP0));
        #ifdef USE_TANGENT
          vec3 objectTangent=vec3(tangent.xyz);
        #endif`,
      )
      .replace("#include <begin_vertex>", "vec3 transformed=bP0;");
    // Cyan rim light: strongest where the surface turns away from the camera.
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform float uGlow;\nuniform float uTint;")
      .replace(
        "#include <emissivemap_fragment>",
        /* glsl */ `#include <emissivemap_fragment>
        float rim=pow(1.0-abs(dot(normal,normalize(vViewPosition))),2.5);
        totalEmissiveRadiance+=vec3(0.12,0.84,0.98)*rim*(0.9+uGlow*1.6)*uTint;`,
      );
  };
  const mesh = new THREE.Mesh(new THREE.IcosahedronGeometry(RADIUS, 64), material);
  const root = new THREE.Group();
  root.add(mesh);
  const qInv = new THREE.Quaternion();

  return {
    root,
    get radius() {
      // The displaced surface bulges past the base sphere, so hit-test a bigger one.
      return RADIUS * mesh.scale.x * 1.15;
    },
    poke(worldHit) {
      // Store the hit in the mesh's own frame so the ripple rides along as it spins.
      const dir = mesh.worldToLocal(worldHit.clone()).normalize();
      uniforms.uRipples.value[nextRipple].set(dir.x, dir.y, dir.z, 0);
      nextRipple = (nextRipple + 1) % 4;
    },
    update(s) {
      for (const r of uniforms.uRipples.value) if (r.w >= 0) r.w = r.w > 4 ? -1 : r.w + s.dt;
      uniforms.uLevel.value = s.level;
      uniforms.uBass.value = s.bass;
      uniforms.uGlow.value = s.glow;
      uniforms.uTint.value = s.tint;
      uniforms.uTime.value += s.dt * (0.6 + s.level * 3);
      material.color.copy(SILVER).lerp(BLUE, s.tint);
      mesh.rotation.y += s.dt * (0.08 + s.bass * 0.35);
      mesh.rotation.x = Math.sin(uniforms.uTime.value * 0.2) * 0.15;
      mesh.scale.setScalar(1 + s.bass * 0.18);
      // Drag stretch arrives in world space; express it in the mesh's frame.
      mesh.getWorldQuaternion(qInv).invert();
      uniforms.uStretch.value.copy(s.stretch).applyQuaternion(qInv);
    },
    dispose() {
      mesh.geometry.dispose();
      material.dispose();
    },
  };
}
