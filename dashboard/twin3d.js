/*
 * 3D twin viewer for the dashboard.
 *
 * Model, sensor placement and lighting come from the team's FRONTEND branch
 * (frontend/index.html + transformer.glb, built in Blender). Here the sensors are driven by
 * the seven IoT statuses of the current timeline hour instead of the page's own simulation.
 *
 *   base model      : every sensor shown in its status colour, camera on the whole transformer
 *   IoT n signalled : the camera flies to IoT n's sensors, they glow in the signal colour and
 *                     every other sensor dims
 *
 *   const twin = Twin3D.create(container, { onStatus: text => ... });
 *   twin.setStatuses(['NORMAL', 'CRITICAL', null, ...]);   // index 0 = IOT1
 *   twin.focus(6);                                           // or null for the base view
 *   twin.setMode('cut');                                     // 'solid' | 'cut' | 'xray'
 *   twin.explode(1, true);                                   // 0 = assembled, 1 = exploded; animate
 *
 * Tank views, cutaway clipping and the explode / assemble animation are the FRONTEND branch's:
 * every part with glTF extras "explode" ([dx, dy, dz, t0, t1] stages, Blender axes) moves along its
 * stages as t goes 0 -> 1, and the sensors move with the part they are mounted on.
 */
(function (root) {
  'use strict';
  const COL = { NORMAL: '#3fb950', WARNING: '#d29922', PREDICTED_MAINTENANCE: '#a371f7', MAINTENANCE_REQUIRED: '#f0883e', CRITICAL: '#f85149', NONE: '#5c6773' };
  const RANK = { NONE: -1, NORMAL: 0, WARNING: 1, PREDICTED_MAINTENANCE: 2, MAINTENANCE_REQUIRED: 3, CRITICAL: 4 };
  const B = (x, y, z) => new THREE.Vector3(x, z, -y);   // Blender (Z up) -> three (Y up)

  // Sensor devices on the model. slots = the IoTs (1..7) each device feeds.
  // dev: a device already modelled in Blender. at + parent: a small sensor module placed on that part.
  const SENSORS = [
    { id: 'IA', slots: [3, 1], name: 'Current, phase R', dev: 'IoT_CT_A' },
    { id: 'IB', slots: [3, 1], name: 'Current, phase Y', dev: 'IoT_CT_B' },
    { id: 'IC', slots: [3, 1], name: 'Current, phase B', dev: 'IoT_CT_C' },
    { id: 'VA', slots: [4, 2], name: 'Voltage, phase R', at: [-0.18, 0.205, 1.30], parent: 'LV_Lead_u' },
    { id: 'VB', slots: [4, 2], name: 'Voltage, phase Y', at: [0.07, 0.205, 1.30], parent: 'LV_Lead_v' },
    { id: 'VC', slots: [4, 2], name: 'Voltage, phase B', at: [0.32, 0.205, 1.30], parent: 'LV_Lead_w' },
    { id: 'OIL', slots: [5], name: 'Oil level gauge', dev: 'MOG_Oil_Level_Gauge' },
    { id: 'TOP_OIL', slots: [6], name: 'Top-oil temperature', dev: 'IoT_TopOil_Temp' },
    { id: 'TW_A', slots: [7], name: 'Winding temp R', at: [-0.29, -0.16, 1.00], parent: 'HV_Winding_U', fibre: true },
    { id: 'TW_B', slots: [7], name: 'Winding temp Y', at: [0.13, -0.16, 1.00], parent: 'HV_Winding_V', fibre: true },
    { id: 'TW_C', slots: [7], name: 'Winding temp B', at: [0.55, -0.16, 1.00], parent: 'HV_Winding_W', fibre: true },
    { id: 'AMB', slots: [6, 7], name: 'Ambient temperature', dev: 'IoT_Ambient_Temp' },
    { id: 'REF', slots: [1, 2], name: 'Reference meters', dev: 'Marshalling_Box' },
  ];

  function create(container, opts) {
    opts = opts || {};
    const say = opts.onStatus || (() => {});
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const sensors = SENSORS.map(s => Object.assign({}, s, { s: 'NONE' }));
    const api = { ready: false, setStatuses, focus, resetView, toggleSpin, setMode, setOil, setLabels, explode, get t() { return t; } };
    const onExplode = opts.onExplode || (() => {});
    let statuses = [], focusSlot = null;
    // explode / tank-view state
    const parts = [], tank = [], oil = [], radL = [], radPucks = [];
    let t = 0, tAnim = null, mode = opts.mode || 'xray', showOil = true, follow = true;
    const homes = {};

    if (!root.THREE || !THREE.GLTFLoader) { say('3D viewer could not load'); return api; }

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.9;
    renderer.shadowMap.enabled = true;
    renderer.localClippingEnabled = true;
    container.prepend(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0f1419);
    const camera = new THREE.PerspectiveCamera(35, 16 / 10, 0.05, 100);
    const controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.autoRotateSpeed = 0.8;
    if (THREE.RoomEnvironment) {
      const pmrem = new THREE.PMREMGenerator(renderer);
      scene.environment = pmrem.fromScene(new THREE.RoomEnvironment(), 0.04).texture;
    }
    scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x2a2622, 0.6));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.shadow.bias = -0.0005; sun.shadow.normalBias = 0.02;
    sun.position.set(4, 7, 6); sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -3.5, right: 3.5, top: 5.5, bottom: -1.5, far: 30 });
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0xffffff, 0.8); fill.position.set(-5, 4, -4); scene.add(fill);
    const rim = new THREE.DirectionalLight(0xffe2c8, 0.5); rim.position.set(1, 3, -6); scene.add(rim);

    // label chips that follow each sensor on screen
    const chips = document.createElement('div');
    chips.className = 'chips';
    container.append(chips);
    for (const s of sensors) {
      const c = document.createElement('div');
      c.className = 'chip'; c.title = s.name; c.innerHTML = '<i></i>' + s.id;
      c.onclick = () => flyTo(s);
      s.chip = c; chips.append(c);
    }

    const glowTex = (() => {
      const c = document.createElement('canvas'); c.width = c.height = 64;
      const g = c.getContext('2d'), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
      gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(0.25, 'rgba(255,255,255,.65)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
      g.fillStyle = gr; g.fillRect(0, 0, 64, 64);
      return new THREE.CanvasTexture(c);
    })();
    const housing = new THREE.MeshStandardMaterial({ color: 0xdfe3e6, roughness: 0.4 });
    const ease = x => { x = Math.min(Math.max(x, 0), 1); return x * x * (3 - 2 * x); };
    let fly = null;

    // Cutaway windows (three world: x, y up, z = -Blender y). Clipped where inside every plane.
    const P = (x, y, z, c) => new THREE.Plane(new THREE.Vector3(x, y, z), c);
    const tankCut = [P(0, 0, -1, 0.30), P(1, 0, 0, -0.64), P(-1, 0, 0, -0.64), P(0, -1, 0, 0.28), P(0, 1, 0, -1.36)];
    const oilCut = [P(0, 0, -1, -0.12), P(1, 0, 0, -0.64), P(-1, 0, 0, -0.64), P(0, -1, 0, 0.28), P(0, 1, 0, -1.36)];

    function setT(v) {
      t = Math.min(Math.max(v, 0), 1);
      for (const part of parts) {
        const pos = part.base.clone();
        for (const [dx, dy, dz, t0, t1] of part.stages) {
          const k = ease((t - t0) / (t1 - t0));
          pos.x += dx * k; pos.y += dz * k; pos.z -= dy * k;
        }
        part.obj.position.copy(pos);
      }
      if (follow && homes.a && !fly) lerpHome(t);
      onExplode(t);
    }
    const homeAt = k => {
      const e = ease(k);
      return { target: homes.a.target.clone().lerp(homes.e.target, e), pos: homes.a.pos.clone().lerp(homes.e.pos, e) };
    };
    function lerpHome(k) { const h = homeAt(k); controls.target.copy(h.target); camera.position.copy(h.pos); }
    function explode(goal, animate) {
      if (!api.ready) return;
      follow = true; fly = null;   // exploding shows the whole model, as on the FRONTEND page
      if (!animate || reduce) { tAnim = null; setT(goal); return; }
      const from = t, t0 = performance.now(), ms = 2800 * Math.max(Math.abs(goal - from), 0.15);
      tAnim = now => {
        const k = Math.min((now - t0) / ms, 1);
        setT(from + (goal - from) * k);
        if (k >= 1) { tAnim = null; if (goal === 0 && focusSlot && performance.now() > introUntil) moveCamera(); }
      };
    }

    function applyMode() {
      const cut = mode === 'cut', xr = mode === 'xray', oilOn = showOil && mode !== 'solid';
      for (const m of tank) {
        m.material.clippingPlanes = cut ? tankCut : null; m.material.clipIntersection = true;
        m.material.transparent = xr; m.material.opacity = xr ? 0.16 : 1; m.material.depthWrite = !xr;
        m.material.side = cut ? THREE.DoubleSide : THREE.FrontSide; m.material.needsUpdate = true;
      }
      for (const m of oil) {
        m.visible = oilOn;
        m.material.clippingPlanes = cut ? oilCut : null; m.material.clipIntersection = true; m.material.needsUpdate = true;
      }
      for (const o of radL) {
        o.visible = !cut;
        o.material.transparent = xr; o.material.opacity = xr ? 0.12 : 1; o.material.depthWrite = !xr; o.material.needsUpdate = true;
      }
      for (const o of radPucks) o.visible = !cut;
    }
    function setMode(m) { mode = m; if (api.ready) applyMode(); }
    function setOil(on) { showOil = !!on; if (api.ready) applyMode(); }
    function setLabels(on) { chips.hidden = !on; }

    function attachSensors(model) {
      const byName = n => model.getObjectByName(n);
      model.updateMatrixWorld(true);
      for (const s of sensors) {
        let parent, world;
        if (s.dev) {
          parent = byName(s.dev);
          if (!parent) continue;
          world = new THREE.Box3().setFromObject(parent).getCenter(new THREE.Vector3());
          s.mats = [];
          parent.traverse(o => { if (o.isMesh) { o.material = o.material.clone(); s.mats.push(o.material); } });
          const core = /^IoT_CT_/.test(s.dev) && byName(s.dev + '_Core');
          if (core) core.traverse(o => { if (o.isMesh) { o.material = o.material.clone(); s.mats.push(o.material); } });
        } else {
          parent = byName(s.parent);
          if (!parent) continue;
          world = B(...s.at);
        }
        const g = new THREE.Group();
        g.position.copy(parent.worldToLocal(world.clone()));
        if (!s.dev) {
          const box = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.045, 0.035), housing);
          const band = new THREE.Mesh(new THREE.BoxGeometry(0.062, 0.012, 0.037),
            new THREE.MeshStandardMaterial({ color: 0x222222, emissive: 0x3fb950, emissiveIntensity: 2 }));
          band.position.y = -0.012;
          s.mats = [band.material];
          g.add(box, band);
          if (s.fibre) {
            const f = new THREE.Mesh(new THREE.CylinderGeometry(0.0025, 0.0025, 0.32, 6),
              new THREE.MeshStandardMaterial({ color: 0x3d8bff, emissive: 0x1a4aa0 }));
            f.position.y = 0.18; g.add(f);
          }
        }
        const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: COL.NONE, transparent: true, opacity: 0.9, depthTest: false, depthWrite: false }));
        sp.scale.setScalar(0.09); sp.renderOrder = 20; sp.raycast = () => {};
        g.add(sp); s.glow = sp; s.anchor = g;
        parent.add(g);
      }
    }

    function paint(s) {
      s.chip.dataset.s = s.s;
      s.chip.style.setProperty('--c', COL[s.s]);
      if (!s.glow) return;
      const c = new THREE.Color(COL[s.s]);
      s.glow.material.color.copy(c);
      for (const m of s.mats) { m.emissive.copy(c); m.emissiveIntensity = s.s === 'NORMAL' || s.s === 'NONE' ? 0.45 : 1.3; }
    }

    // A device's status is the worst status of the IoTs it feeds (NONE when none was evaluated).
    function setStatuses(list) {
      statuses = list || [];
      for (const s of sensors) {
        let worst = 'NONE';
        for (const slot of s.slots) { const st = statuses[slot - 1]; if (st && RANK[st] > RANK[worst]) worst = st; }
        if (worst !== s.s) { s.s = worst; paint(s); }
      }
    }

    function flyTo(s, distance) {
      if (!s.anchor) return;
      const p = s.anchor.getWorldPosition(new THREE.Vector3());
      const dir = camera.position.clone().sub(controls.target).normalize();
      flyBetween(p, p.clone().add(dir.multiplyScalar(distance || 2.0)));
    }
    function flyBetween(target, position) {
      const from = { t: controls.target.clone(), c: camera.position.clone() }, t0 = performance.now();
      if (reduce) { controls.target.copy(target); camera.position.copy(position); return; }
      fly = now => {
        const k = ease((now - t0) / 900);
        controls.target.lerpVectors(from.t, target, k); camera.position.lerpVectors(from.c, position, k);
        if (k >= 1) fly = null;
      };
    }
    function resetView() { if (!homes.a) return; follow = true; const h = homeAt(t); flyBetween(h.target, h.pos); }
    function toggleSpin() { controls.autoRotate = !controls.autoRotate; return controls.autoRotate; }

    // Base model = whole transformer; IoT model = camera centred on that IoT's sensors.
    // Colours and dimming follow every change at once; the camera moves at most once per
    // FOCUS_MS so fast playback does not make it chase every hour.
    const FOCUS_MS = 1500;
    let lastMove = -Infinity, moveTimer = null, introUntil = 0;
    function focus(slot) {
      focusSlot = slot || null;
      if (!api.ready) return;
      const wait = Math.max(lastMove + FOCUS_MS, introUntil) - performance.now();
      if (wait > 0) {
        if (!moveTimer) moveTimer = setTimeout(() => { moveTimer = null; focus(focusSlot); }, wait);
        return;
      }
      lastMove = performance.now();
      moveCamera();
    }
    function moveCamera() {
      // exploded = inspection mode: keep the whole model in view; highlighting still applies
      if (!focusSlot || t > 0.05) return resetView();
      follow = false;
      const group = sensors.filter(s => s.anchor && s.slots[0] === focusSlot);
      const targets = group.length ? group : sensors.filter(s => s.anchor && s.slots.includes(focusSlot));
      if (!targets.length) return resetView();
      const box = new THREE.Box3();
      for (const s of targets) box.expandByPoint(s.anchor.getWorldPosition(new THREE.Vector3()));
      const centre = box.getCenter(new THREE.Vector3());
      const spread = box.getSize(new THREE.Vector3()).length();
      const h = homeAt(t), dir = h.pos.clone().sub(h.target).normalize();
      flyBetween(centre, centre.clone().add(dir.multiplyScalar(Math.max(2.6, spread * 2.2))));
    }

    say('Loading 3D model…');
    new THREE.GLTFLoader().load('transformer.glb', gltf => {
      const model = gltf.scene;
      model.traverse(o => {
        if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; o.material.envMapIntensity = o.material.metalness > 0.5 ? 0.7 : 0.3; }
        if (o.userData && o.userData.explode) parts.push({ obj: o, base: o.position.clone(), stages: JSON.parse(o.userData.explode) });
      });
      model.traverse(o => {
        if (!o.isMesh) return;
        const nm = (o.name || '') + '|' + (o.parent ? o.parent.name : '');
        if (/(^|\|)Tank(\||_?\d*$)/.test(nm)) { o.material = o.material.clone(); tank.push(o); }
        if (/Insulating_Oil/.test(nm)) { o.material = o.material.clone(); o.castShadow = false; o.renderOrder = 2; oil.push(o); }
        if (/Radiator_Left/.test(nm)) { o.material = o.material.clone(); radL.push(o); }
        if (/IoT_RAD_/.test(nm)) radPucks.push(o);
      });
      scene.add(model);

      // camera homes for the assembled (a) and fully exploded (e) poses
      function home(k) {
        setT(k);
        model.updateMatrixWorld(true);
        const box = new THREE.Box3().setFromObject(model), c = box.getCenter(new THREE.Vector3()), sz = box.getSize(new THREE.Vector3());
        const r = Math.max(sz.x, sz.y, sz.z), f = k ? 1.3 : 1.05;
        return { target: c.clone(), pos: c.clone().add(new THREE.Vector3(r * 0.95 * f, r * 0.42 * f, r * 1.15 * f)), r };
      }
      follow = false;
      homes.e = home(1); homes.a = home(0);
      attachSensors(model);          // assembled pose (t = 0): sensors ride on their parts
      for (const s of sensors) paint(s);
      controls.minDistance = 0.4; controls.maxDistance = homes.e.r * 4;
      api.ready = true;
      applyMode();
      setStatuses(statuses);
      say('');
      // intro, as on the FRONTEND page: start exploded, then assemble
      follow = true;
      setT(1);
      if (reduce || opts.noIntro) { setT(opts.t || 0); if (focusSlot && !opts.t) moveCamera(); return; }
      introUntil = performance.now() + 3700;
      setTimeout(() => explode(0, true), 700);
      setTimeout(() => { if (focusSlot) { lastMove = performance.now(); moveCamera(); } }, 3700);
    }, undefined, () => say('The 3D model could not load'));

    controls.addEventListener('start', () => { fly = null; follow = false; });

    function size() {
      const w = container.clientWidth, h = container.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    }
    new ResizeObserver(size).observe(container); size();

    const v3 = new THREE.Vector3();
    function placeChips(now) {
      const w = container.clientWidth, h = container.clientHeight;
      for (const s of sensors) {
        if (!s.anchor) { s.chip.style.display = 'none'; continue; }
        let vis = true;
        for (let o = s.anchor.parent; o; o = o.parent) if (!o.visible) { vis = false; break; }
        s.glow.visible = vis;
        if (!vis) { s.chip.style.display = 'none'; continue; }
        s.anchor.getWorldPosition(v3).project(camera);
        if (v3.z > 1 || Math.abs(v3.x) > 1.05 || Math.abs(v3.y) > 1.05) { s.chip.style.display = 'none'; continue; }
        s.chip.style.display = '';
        s.chip.style.transform = `translate(${(v3.x * 0.5 + 0.5) * w}px, ${(-v3.y * 0.5 + 0.5) * h}px) translate(-50%, calc(-100% - 8px))`;
        const dim = focusSlot && !s.slots.includes(focusSlot);
        s.chip.classList.toggle('dim', !!dim);
        const pulse = s.s === 'CRITICAL' && !reduce ? 1 + 0.45 * Math.sin(now / 160) : RANK[s.s] > 0 ? 1.15 : 1;
        s.glow.scale.setScalar((dim ? 0.04 : 0.09) * pulse);
        s.glow.material.opacity = dim ? 0.15 : 0.9;
      }
    }

    (function loop(now) {
      requestAnimationFrame(loop);
      now = now || performance.now();
      if (tAnim) tAnim(now);
      if (fly) fly(now);
      controls.update();
      renderer.render(scene, camera);
      if (api.ready) placeChips(now);
    })();

    return api;
  }

  root.Twin3D = { create, SENSORS };
})(window);
