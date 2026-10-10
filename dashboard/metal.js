/*
 * Procedural exterior metal for the transformer model (no image files, works offline).
 *
 * The outside paint (Tank_Paint_RAL7033: tank, cover, conservator, radiators, boxes) becomes
 * brushed, scratched grey steel plate with rows of rivets along the plate seams and a light
 * touch of rust at the seams and under the rivets. The texture is drawn once on a canvas and
 * applied with triplanar mapping in object space, so it does not depend on the model's UVs
 * and stays put on parts when the model is exploded.
 *
 * The coils and windings (with their pressboard tubes and end rings) and every other copper
 * part get an aged, brushed copper texture, drawn and mapped the same way.
 */
(function (root) {
  'use strict';
  const SIZE = 1024;          // texture px
  const PLATE = SIZE / 2;     // two plates per tile
  const TILE_M = 0.9;         // one texture tile covers this many model units

  // deterministic noise so every load looks the same
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);

  function drawTextures() {
    const col = document.createElement('canvas'), rough = document.createElement('canvas');
    col.width = col.height = rough.width = rough.height = SIZE;
    const c = col.getContext('2d'), r = rough.getContext('2d');

    // base steel + roughness
    c.fillStyle = '#8b9096'; c.fillRect(0, 0, SIZE, SIZE);
    r.fillStyle = 'rgb(0,110,0)'; r.fillRect(0, 0, SIZE, SIZE);   // roughness in green, ~0.43

    // brushed grain: many long thin horizontal strokes
    for (let i = 0; i < 2600; i++) {
      const y = rnd() * SIZE, x = rnd() * SIZE, len = 60 + rnd() * 400, light = rnd() > 0.5;
      c.strokeStyle = light ? `rgba(255,255,255,${0.03 + rnd() * 0.07})` : `rgba(0,0,0,${0.03 + rnd() * 0.08})`;
      c.lineWidth = 0.6 + rnd() * 1.2;
      c.beginPath(); c.moveTo(x, y); c.lineTo(x + len, y + (rnd() - 0.5) * 2); c.stroke();
      r.strokeStyle = `rgba(0,${light ? 80 : 150},0,0.25)`; r.lineWidth = c.lineWidth;
      r.beginPath(); r.moveTo(x, y); r.lineTo(x + len, y); r.stroke();
    }
    // (the tile edges fall on plate seams, which hide any grain mismatch)

    // scratches
    for (let i = 0; i < 140; i++) {
      const x = rnd() * SIZE, y = rnd() * SIZE, a = rnd() * Math.PI, len = 10 + rnd() * 70;
      c.strokeStyle = `rgba(225,230,235,${0.15 + rnd() * 0.25})`; c.lineWidth = 0.6 + rnd() * 0.8;
      c.beginPath(); c.moveTo(x, y); c.lineTo(x + Math.cos(a) * len, y + Math.sin(a) * len); c.stroke();
      r.strokeStyle = 'rgba(0,60,0,0.6)'; r.lineWidth = c.lineWidth;
      r.beginPath(); r.moveTo(x, y); r.lineTo(x + Math.cos(a) * len, y + Math.sin(a) * len); r.stroke();
    }

    // light rust: soft blotches hugging the seams
    const rust = (x, y, rad, a) => {
      const g = c.createRadialGradient(x, y, 0, x, y, rad);
      g.addColorStop(0, `rgba(140,62,22,${a})`); g.addColorStop(1, 'rgba(140,62,22,0)');
      c.fillStyle = g; c.fillRect(x - rad, y - rad, rad * 2, rad * 2);
      const h = r.createRadialGradient(x, y, 0, x, y, rad);
      h.addColorStop(0, `rgba(0,230,0,${a})`); h.addColorStop(1, 'rgba(0,230,0,0)');
      r.fillStyle = h; r.fillRect(x - rad, y - rad, rad * 2, rad * 2);
    };
    for (let i = 0; i < 70; i++) {
      const along = rnd() * SIZE, off = (rnd() - 0.5) * 26, seam = PLATE * Math.floor(rnd() * 3);
      if (rnd() > 0.5) rust(along, seam + off, 8 + rnd() * 22, 0.12 + rnd() * 0.2);
      else rust(seam + off, along, 8 + rnd() * 22, 0.12 + rnd() * 0.2);
    }

    // plate seams: a dark groove with a bright lip
    for (let k = 0; k <= 2; k++) {
      const p = k * PLATE;
      for (const [x0, y0, x1, y1] of [[0, p, SIZE, p], [p, 0, p, SIZE]]) {
        c.strokeStyle = 'rgba(20,22,25,0.85)'; c.lineWidth = 4;
        c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke();
        c.strokeStyle = 'rgba(255,255,255,0.18)'; c.lineWidth = 1.5;
        c.beginPath(); c.moveTo(x0 + (y0 === y1 ? 0 : 3), y0 + (y0 === y1 ? 3 : 0)); c.lineTo(x1 + (y0 === y1 ? 0 : 3), y1 + (y0 === y1 ? 3 : 0)); c.stroke();
      }
    }

    // rivet rows either side of every seam
    const rivet = (x, y) => {
      // faint rust streak running down from some rivets
      if (rnd() < 0.18) {
        const g = c.createLinearGradient(x, y, x, y + 40);
        g.addColorStop(0, 'rgba(130,58,20,0.28)'); g.addColorStop(1, 'rgba(130,58,20,0)');
        c.fillStyle = g; c.fillRect(x - 2.5, y, 5, 40);
      }
      // shadow ring, dome shaded from top-left, specular dot
      c.fillStyle = 'rgba(0,0,0,0.45)'; c.beginPath(); c.arc(x + 1.5, y + 1.5, 8, 0, 7); c.fill();
      const g = c.createRadialGradient(x - 2.5, y - 2.5, 0.5, x, y, 7);
      g.addColorStop(0, '#e2e6ea'); g.addColorStop(0.45, '#9aa0a6'); g.addColorStop(1, '#3d4247');
      c.fillStyle = g; c.beginPath(); c.arc(x, y, 7, 0, 7); c.fill();
      r.fillStyle = 'rgb(0,70,0)'; r.beginPath(); r.arc(x, y, 7, 0, 7); r.fill();
    };
    const STEP = 32, IN = 16;
    for (let k = 0; k <= 2; k++) {
      const p = k * PLATE;
      for (let s = STEP / 2; s < SIZE; s += STEP) {
        if (Math.abs(s - p) < IN * 1.5) continue;
        for (const o of [-IN, IN]) {
          if (p + o >= 0 && p + o <= SIZE) { rivet(s, p + o); rivet(p + o, s); }
        }
      }
    }

    const tex = (cv) => {
      const t = new THREE.CanvasTexture(cv);
      t.wrapS = t.wrapT = THREE.RepeatWrapping;
      t.anisotropy = 8;
      t.needsUpdate = true;
      return t;
    };
    return { color: tex(col), rough: tex(rough) };
  }

  // Aged, brushed copper: warm copper-brown, mottled from dark chocolate to bright copper,
  // soft cloudy patches and fine random scratches. Seamless (every mark wraps round the tile).
  function drawCopper() {
    const S = 512;
    const col = document.createElement('canvas'), rough = document.createElement('canvas');
    col.width = col.height = rough.width = rough.height = S;
    const c = col.getContext('2d'), r = rough.getContext('2d');
    const wrap = (draw) => { for (const dx of [-S, 0, S]) for (const dy of [-S, 0, S]) draw(dx, dy); };

    c.fillStyle = '#8a4a26'; c.fillRect(0, 0, S, S);
    r.fillStyle = 'rgb(0,105,0)'; r.fillRect(0, 0, S, S);

    // cloudy mottling: big soft blobs, dark chocolate and bright copper
    const blob = (x, y, rad, rgb, a) => wrap((dx, dy) => {
      const g = c.createRadialGradient(x + dx, y + dy, 0, x + dx, y + dy, rad);
      g.addColorStop(0, `rgba(${rgb},${a})`); g.addColorStop(1, `rgba(${rgb},0)`);
      c.fillStyle = g; c.fillRect(x + dx - rad, y + dy - rad, rad * 2, rad * 2);
    });
    for (let i = 0; i < 90; i++) {
      const dark = rnd() < 0.5;
      blob(rnd() * S, rnd() * S, 30 + rnd() * 110, dark ? '58,26,12' : '214,120,62', 0.18 + rnd() * 0.3);
    }
    for (let i = 0; i < 160; i++) {   // smaller speckle in the same tones
      const dark = rnd() < 0.55;
      blob(rnd() * S, rnd() * S, 6 + rnd() * 20, dark ? '48,20,10' : '232,140,80', 0.12 + rnd() * 0.2);
    }
    // duller (rougher) where it is dark, shinier where bright
    for (let i = 0; i < 60; i++) {
      const x = rnd() * S, y = rnd() * S, rad = 30 + rnd() * 100, v = rnd() < 0.5 ? 170 : 60;
      wrap((dx, dy) => {
        const g = r.createRadialGradient(x + dx, y + dy, 0, x + dx, y + dy, rad);
        g.addColorStop(0, `rgba(0,${v},0,0.35)`); g.addColorStop(1, `rgba(0,${v},0,0)`);
        r.fillStyle = g; r.fillRect(x + dx - rad, y + dy - rad, rad * 2, rad * 2);
      });
    }
    // light brushing
    for (let i = 0; i < 900; i++) {
      const x = rnd() * S, y = rnd() * S, len = 30 + rnd() * 160, light = rnd() > 0.5;
      c.strokeStyle = light ? `rgba(255,190,140,${0.03 + rnd() * 0.05})` : `rgba(30,12,4,${0.03 + rnd() * 0.06})`;
      c.lineWidth = 0.5 + rnd();
      wrap((dx, dy) => { c.beginPath(); c.moveTo(x + dx, y + dy); c.lineTo(x + dx + len, y + dy); c.stroke(); });
    }
    // fine random scratches
    for (let i = 0; i < 220; i++) {
      const x = rnd() * S, y = rnd() * S, a = rnd() * Math.PI, len = 6 + rnd() * 45;
      const ex = Math.cos(a) * len, ey = Math.sin(a) * len;
      c.strokeStyle = `rgba(245,170,120,${0.12 + rnd() * 0.25})`; c.lineWidth = 0.5 + rnd() * 0.6;
      r.strokeStyle = 'rgba(0,55,0,0.5)'; r.lineWidth = c.lineWidth;
      wrap((dx, dy) => {
        c.beginPath(); c.moveTo(x + dx, y + dy); c.lineTo(x + dx + ex, y + dy + ey); c.stroke();
        r.beginPath(); r.moveTo(x + dx, y + dy); r.lineTo(x + dx + ex, y + dy + ey); r.stroke();
      });
    }
    return { color: tex(col), rough: tex(rough) };
  }

  function tex(cv) {
    const t = new THREE.CanvasTexture(cv);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.anisotropy = 8;
    t.needsUpdate = true;
    return t;
  }

  let steel = null, copper = null;

  // Replace a standard material's base colour and roughness with a triplanar texture
  // (object space, so it ignores the model's UVs and stays on parts when exploded).
  function applyTriplanar(material, set, tileM) {
    material.color.setRGB(1, 1, 1);
    material.roughness = 1.0;           // scaled by the roughness texture
    const uniforms = {
      uTri: { value: set.color },
      uTriRough: { value: set.rough },
      uTriScale: { value: 1 / tileM },
    };
    material.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, uniforms);
      shader.vertexShader = shader.vertexShader
        .replace('#include <common>', '#include <common>\nvarying vec3 vTriPos;\nvarying vec3 vTriNrm;')
        .replace('#include <begin_vertex>', '#include <begin_vertex>\nvTriPos = position;\nvTriNrm = normal;');
      shader.fragmentShader = shader.fragmentShader
        .replace('#include <common>', `#include <common>
uniform sampler2D uTri;
uniform sampler2D uTriRough;
uniform float uTriScale;
varying vec3 vTriPos;
varying vec3 vTriNrm;
vec4 triplanar(sampler2D t, vec3 p, vec3 n) {
  vec3 w = pow(abs(normalize(n)), vec3(4.0));
  w /= (w.x + w.y + w.z);
  return texture2D(t, p.zy) * w.x + texture2D(t, p.xz) * w.y + texture2D(t, p.xy) * w.z;
}`)
        .replace('#include <map_fragment>', `#include <map_fragment>
diffuseColor.rgb *= pow(triplanar(uTri, vTriPos * uTriScale, vTriNrm).rgb, vec3(2.2));`)
        .replace('#include <roughnessmap_fragment>', `float roughnessFactor = roughness * clamp(triplanar(uTriRough, vTriPos * uTriScale, vTriNrm).g, 0.08, 1.0);`);
    };
    material.customProgramCacheKey = () => 'twin-triplanar';
    material.needsUpdate = true;
    return material;
  }

  // Brushed riveted steel for the outside paint.
  function applySteel(material) {
    if (!material || material.userData.steel) return material;
    material.userData.steel = true;
    material.metalness = 0.75;
    material.envMapIntensity = 0.9;
    return applyTriplanar(material, steel = steel || drawTextures(), TILE_M);
  }

  // Aged brushed copper for the coils, windings and copper parts.
  function applyCopper(material) {
    if (!material || material.userData.copper) return material;
    material.userData.copper = true;
    material.metalness = 0.85;
    material.envMapIntensity = 0.8;
    return applyTriplanar(material, copper = copper || drawCopper(), 0.5);
  }

  // Walk the loaded model: exterior paint -> steel; windings and all copper -> aged copper.
  function dress(model) {
    const coil = new Map();
    model.traverse((o) => {
      if (!o.isMesh) return;
      const names = (o.name || '') + '|' + (o.parent ? o.parent.name : '');
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      if (/Winding|HV_LV_Barrier|End_Ring/i.test(names)) {   // coils plus the pressboard tubes and rings around them
        // their pressboard is shared with other inside parts, so the coils get their own copies
        const copy = mats.map((m) => {
          if (!coil.has(m)) coil.set(m, applyCopper(m.clone()));
          return coil.get(m);
        });
        o.material = Array.isArray(o.material) ? copy : copy[0];
        return;
      }
      for (const m of mats) {
        if (!m) continue;
        if (/Tank_Paint/i.test(m.name || '')) applySteel(m);
        else if (/Copper/i.test(m.name || '')) applyCopper(m);   // bushing studs, leads, earthing pads
      }
    });
  }

  root.TwinMaterials = { dress, applySteel, applyCopper };
})(window);
