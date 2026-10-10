/*
 * Two-timebar dashboard for the CPRI transformer (data from /api/twin2/timebar/1 and /2).
 *
 *   Timebar 1 - one day of fast signals at 1-minute playback resolution (V / I sampled every 20 ms, thermal
 *               every 1 s, ambient / pressure / precision every 1 min; the 20 ms windows around each
 *               electrical event are shown in the loading and voltage graphs).
 *   Timebar 2 - one year of daily readings: oil leakage and the three precision devices.
 *
 * Each timebar has its own player (play / step / scrub, "1 unit every [n] s" tick rate and a
 * 1x / 2x / 4x / 0.5x speed button). The player ticks every 20 ms. Monitor cards, the signal list,
 * the 3D model and the graphs follow both cursors.
 */
(function (root) {
  'use strict';
  const API = '/api/twin2';
  const LABEL = { NORMAL: 'Normal', WARNING: 'Warning', PREDICTED_MAINTENANCE: 'Predicted', MAINTENANCE_REQUIRED: 'Maintenance',
    CRITICAL: 'Critical', OFF: 'Out of service', NO_DATA: 'No data' };
  const RANK = { OFF: -1, NO_DATA: -1, NORMAL: 0, WARNING: 1, PREDICTED_MAINTENANCE: 2, MAINTENANCE_REQUIRED: 3, CRITICAL: 4 };
  const COLOR = { NORMAL: '#3fb950', WARNING: '#d29922', PREDICTED_MAINTENANCE: '#a371f7', MAINTENANCE_REQUIRED: '#f0883e',
    CRITICAL: '#f85149', OFF: '#4b5563', NO_DATA: '#4b5563' };
  const LEVEL = { WARNING: 'WARNING', CRITICAL: 'CRITICAL', MAINTENANCE_REQUIRED: 'MAINTENANCE_REQUIRED', MAINTENANCE: 'MAINTENANCE_REQUIRED' };
  const SPEEDS = [1, 2, 4, 0.5];
  const C = { a: '#3fa7ff', b: '#eef3f8', c: '#f59e0b', d: '#a371f7', e: '#2dd4bf', r: '#f85149', y: '#d29922', g: '#3fb950', m: '#8b98a5' };
  const $ = (id) => document.getElementById(id);
  const worst = (list) => list.reduce((w, s) => (s && RANK[s] > (RANK[w] ?? -2) ? s : w), null);
  const fmt = (v, d = 1) => (v == null || !Number.isFinite(v) ? '—' : v.toFixed(d));
  const pmax = (arr) => (arr ? Math.max(...arr) : null);

  // ---------------------------------------------------------------- monitors
  function monitors(tb1, tb2) {
    const L1 = tb1.limits, L2 = tb2.limits, s1 = tb1.series, s2 = tb2.series;
    const vKey = L1.voltage_winding === 'primary' ? 'v_pri' : 'v_sec';
    const farthest = (m) => { const a = s1[vKey + '_min'][m], b = s1[vKey + '_max'][m]; return a == null ? null : (Math.abs(a) > Math.abs(b) ? a : b); };
    const prec = (tb, dev, every) => ({
      metric: `R / Y / B deviation from reference · limit ${tb.limits.precision_max_pct} % · ${every}`,
      value: (i) => pmax(tb.series[`prec_${dev}`][i]), unit: '%', digits: 2, status: (i) => tb.status[`prec_${dev}`][i],
      charts: () => [{ title: 'Deviation from the reference instrument per phase (%)', unit: '%', series: [
        { name: 'R', values: tb.series[`prec_${dev}`].map((v) => v && v[0]), color: C.r },
        { name: 'Y', values: tb.series[`prec_${dev}`].map((v) => v && v[1]), color: C.y },
        { name: 'B', values: tb.series[`prec_${dev}`].map((v) => v && v[2]), color: C.a }],
        lines: [{ y: tb.limits.precision_max_pct, label: `${tb.limits.precision_max_pct} % accuracy limit`, level: 'MAINTENANCE_REQUIRED' }] }],
    });
    return [
      { id: 'loading', tb: 1, name: 'Loading (overload)', metric: `I / I_rated · warning > ${L1.overload_warning_pct} %, trip > ${L1.overload_trip_pct} % · 20 ms`,
        value: (m) => s1.loading_max[m], unit: '%', status: (m) => tb1.status.loading[m], slot: 3, windows: 'OVERLOAD',
        charts: () => [{ title: 'Loading, % of rated current (max and mean of each minute)', unit: '%', series: [
          { name: 'Max in minute', values: s1.loading_max, color: C.a }, { name: 'Mean', values: s1.loading_mean, color: C.b, dash: '4 3' }],
          lines: [{ y: L1.overload_warning_pct, label: `Warning ${L1.overload_warning_pct} %`, level: 'WARNING' },
            { y: L1.overload_trip_pct, label: `Trip ${L1.overload_trip_pct} %`, level: 'CRITICAL' }] }] },
      { id: 'voltage', tb: 1, name: `Voltage (${L1.voltage_winding} winding)`,
        metric: `${L1.voltage_class.replace('_', '/')} class · +${L1.voltage_high_pct} % / −${L1.voltage_low_pct} % · 20 ms`,
        value: farthest, unit: '%', signed: true, status: (m) => tb1.status.voltage[m], slot: 4, windows: 'VOLTAGE',
        charts: () => [
          { title: `${L1.voltage_winding === 'primary' ? 'Primary' : 'Secondary'} voltage deviation from nominal (min / max of each minute, %)`, unit: '%', series: [
            { name: 'Max', values: s1[vKey + '_max'], color: C.a }, { name: 'Min', values: s1[vKey + '_min'], color: C.e }],
            lines: [{ y: L1.voltage_high_pct, label: `Over-voltage +${L1.voltage_high_pct} %`, level: 'WARNING' },
              { y: -L1.voltage_low_pct, label: `Under-voltage −${L1.voltage_low_pct} %`, level: 'WARNING' }] },
          { title: `${L1.voltage_winding === 'primary' ? 'Secondary' : 'Primary'} voltage deviation (for reference, %)`, unit: '%', series: [
            { name: 'Max', values: s1[(vKey === 'v_pri' ? 'v_sec' : 'v_pri') + '_max'], color: C.d },
            { name: 'Min', values: s1[(vKey === 'v_pri' ? 'v_sec' : 'v_pri') + '_min'], color: C.m }] }] },
      { id: 'oti', tb: 1, name: 'Top oil (OTI)', metric: `absolute T_O · alarm ${L1.oti_alarm_c} °C, trip ${L1.oti_trip_c} °C · 1 s`,
        value: (m) => s1.top_oil[m], unit: '°C', status: (m) => tb1.status.oti[m], slot: 6,
        charts: () => [
          { title: 'Top-oil temperature: measured vs IEC 60076-7 expected (°C)', unit: '°C', series: [
            { name: 'Measured T_O', values: s1.top_oil, color: C.a }, { name: 'Expected (IEC model)', values: s1.top_oil_expected, color: C.b, dash: '4 3' },
            { name: 'Ambient (1 min)', values: s1.ambient, color: C.m }],
            lines: [{ y: L1.oti_alarm_c, label: `OTI alarm ${L1.oti_alarm_c} °C`, level: 'WARNING' }, { y: L1.oti_trip_c, label: `OTI trip ${L1.oti_trip_c} °C`, level: 'CRITICAL' }] },
          { title: 'Top-oil rise over ambient ΔT_O = T_O − T_A (K) · analytics only', unit: 'K', series: [
            { name: 'Measured', values: s1.top_oil_rise_measured, color: C.a }, { name: 'Expected', values: s1.top_oil_rise_expected, color: C.b, dash: '4 3' }],
            lines: [{ y: L1.top_oil_rise_limit_k, label: `${L1.top_oil_rise_limit_k} K rise (analytics warning)`, level: 'WARNING' }] }] },
      { id: 'wti', tb: 1, name: 'Winding (WTI)', metric: `T_O + thermal image · alarm ${L1.wti_alarm_c} °C, trip ${L1.wti_trip_c} °C · 1 s`,
        value: (m) => s1.wti[m], unit: '°C', status: (m) => tb1.status.wti[m], slot: 7,
        charts: () => [{ title: 'WTI vs estimated hot spot and the measured winding (°C)', unit: '°C', series: [
          { name: 'WTI', values: s1.wti, color: C.a }, { name: 'Hot spot T_H (IEC estimate)', values: s1.hotspot, color: C.c, dash: '4 3' },
          { name: 'Measured winding (probe)', values: s1.winding_measured, color: C.e }],
          lines: [{ y: L1.wti_alarm_c, label: `WTI alarm ${L1.wti_alarm_c} °C`, level: 'WARNING' }, { y: L1.wti_trip_c, label: `WTI trip ${L1.wti_trip_c} °C`, level: 'CRITICAL' }] }] },
      { id: 'thermal', tb: 1, name: 'Thermal analytics (IEC 60076-7)', metric: 'residual, ageing, cooling · never a trip · 1 s',
        value: (m) => s1.residual[m], unit: 'K', signed: true, status: (m) => tb1.status.thermal[m], slot: 7,
        charts: () => [
          { title: 'Winding thermal residual R_W = measured − expected (K)', unit: 'K', series: [{ name: 'R_W', values: s1.residual, color: C.a }],
            lines: [{ y: L1.residual_min_k, label: `${L1.residual_min_k} K excess-heating level`, level: 'MAINTENANCE_REQUIRED' }] },
          { title: 'Relative ageing rate V = 2^((T_H − 98) / 6)', unit: '', series: [{ name: 'V', values: s1.ageing_rate, color: C.c }],
            lines: [{ y: 1, label: 'Design rate (V = 1)', level: 'WARNING' }] },
          { title: 'Equivalent ageing L_EQ = Σ V·dt (hours) and ageing ratio L_EQ / t', unit: 'h', series: [
            { name: 'L_EQ (h)', values: s1.ageing_hours, color: C.d }, { name: 'Ageing ratio', values: s1.ageing_ratio, color: C.b, dash: '4 3' }] },
          { title: 'Load factor K = I / I_rated', unit: '', series: [{ name: 'K', values: s1.load_factor, color: C.e }] }] },
      { id: 'pressure', tb: 1, name: 'Tank pressure', metric: `sealed tank, ideal gas · warning ${L1.pressure_warning_kpa}, limit ${L1.pressure_limit_kpa} kPa (assumed) · 1 min`,
        value: (m) => s1.pressure_abs[m], unit: 'kPa', digits: 1, status: (m) => tb1.status.pressure[m], slot: 6,
        charts: () => [
          { title: 'Headspace pressure, absolute (kPa)', unit: 'kPa', series: [{ name: 'P abs', values: s1.pressure_abs, color: C.a }, { name: 'Gauge', values: s1.pressure_gauge, color: C.m, dash: '4 3' }],
            lines: [{ y: L1.pressure_warning_kpa, label: `Warning ${L1.pressure_warning_kpa} kPa`, level: 'WARNING' }, { y: L1.pressure_limit_kpa, label: `Limit ${L1.pressure_limit_kpa} kPa (assumed)`, level: 'CRITICAL' }] },
          { title: 'Oil expansion and remaining headspace (L)', unit: 'L', series: [{ name: 'Headspace', values: s1.headspace, color: C.e }, { name: 'Oil expansion', values: s1.oil_expansion, color: C.c }] }] },
      { id: 'prec_current', tb: 1, name: 'Current device (CT)', ...prec(tb1, 'current', '1 min'), slot: 1 },
      { id: 'prec_voltage', tb: 1, name: 'Voltage device (PT)', ...prec(tb1, 'voltage', '1 min'), slot: 2 },
      { id: 'prec_temperature', tb: 1, name: 'Temperature device', ...prec(tb1, 'temperature', '1 min'), slot: 7 },
      { id: 'oil', tb: 2, name: 'Oil volume (leak)', metric: `V_meas − V_exp,noleak · tolerance ${L2.tolerance_l} L, minimum ${L2.minimum_volume_l} L · daily`,
        value: (d) => s2.residual[d], unit: 'L', signed: true, status: (d) => tb2.status.oil[d], slot: 5,
        charts: () => [
          { title: 'Oil volume: measured vs expected without a leak (L)', unit: 'L', series: [
            { name: 'Measured', values: s2.volume_measured, color: C.a }, { name: 'Expected, no leak', values: s2.volume_expected, color: C.b, dash: '4 3' }],
            lines: [{ y: L2.minimum_volume_l, label: `Minimum ${L2.minimum_volume_l} L`, level: 'CRITICAL' }] },
          { title: 'Residual R = V_meas − V_exp,noleak (L)', unit: 'L', series: [{ name: 'R', values: s2.residual, color: C.a }, { name: '−L_cum (cumulative leak)', values: s2.cumulative_leak.map((x) => -x), color: C.c, dash: '4 3' }],
            lines: [{ y: -L2.tolerance_l, label: `Leak alarm −${L2.tolerance_l} L`, level: 'WARNING' }] },
          { title: 'Daily mean oil temperature (°C)', unit: '°C', series: [{ name: 'T_oil', values: s2.oil_temperature, color: C.c }] }] },
      { id: 'y_prec_current', tb: 2, name: 'Current device (CT)', ...prec(tb2, 'current', 'daily'), slot: 1 },
      { id: 'y_prec_voltage', tb: 2, name: 'Voltage device (PT)', ...prec(tb2, 'voltage', 'daily'), slot: 2 },
      { id: 'y_prec_temperature', tb: 2, name: 'Temperature device', ...prec(tb2, 'temperature', 'daily'), slot: 7 },
    ];
  }

  // ---------------------------------------------------------------- chart (SVG, one y axis, shared x = sample index)
  const NS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs, parent) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.append(e); return e; }
  function niceTicks(lo, hi, n) {
    const span = hi - lo || 1, step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || mag * 10;
    const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10)); return out;
  }
  function drawChart(box, spec, ctx) {
    const W = 1000, H = 220, M = { l: 54, r: 150, t: 12, b: 26 }, PW = W - M.l - M.r, PH = H - M.t - M.b;
    const [a, b] = ctx.range, n = b - a + 1;
    const wrap = document.createElement('div'); wrap.className = 'chart';
    const h = document.createElement('div'); h.className = 'chart-title'; h.textContent = spec.title; wrap.append(h);
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': spec.title }, wrap);
    let lo = Infinity, hi = -Infinity;
    for (const s of spec.series) for (let i = a; i <= b; i++) { const v = s.values[i]; if (v != null && Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }
    for (const l of spec.lines || []) { lo = Math.min(lo, l.y); hi = Math.max(hi, l.y); }
    if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
    const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
    const X = (i) => M.l + ((i - a) / Math.max(1, n - 1)) * PW, Y = (v) => M.t + (1 - (v - lo) / (hi - lo)) * PH;
    const g = el('g', {}, svg);
    for (const v of niceTicks(lo, hi, 5)) {
      el('line', { x1: M.l, x2: M.l + PW, y1: Y(v), y2: Y(v), stroke: 'rgba(255,255,255,.08)' }, g);
      el('text', { x: M.l - 6, y: Y(v) + 4, 'text-anchor': 'end', class: 'ax' }, g).textContent = v;
    }
    for (let k = 0; k <= 4; k++) {
      const i = Math.round(a + (k / 4) * (n - 1));
      el('text', { x: X(i), y: H - 8, 'text-anchor': k === 0 ? 'start' : k === 4 ? 'end' : 'middle', class: 'ax' }, g).textContent = ctx.xLabel(i);
    }
    // off-line (out of service) shading
    if (ctx.offline) {
      let s = null;
      for (let i = a; i <= b + 1; i++) {
        const off = i <= b && ctx.offline[i];
        if (off && s == null) s = i;
        if (!off && s != null) { el('rect', { x: X(s), y: M.t, width: Math.max(1, X(i - 1) - X(s)), height: PH, fill: 'rgba(255,255,255,.05)' }, g); s = null; }
      }
    }
    for (const l of spec.lines || []) {
      const c = COLOR[l.level] || C.m;
      el('line', { x1: M.l, x2: M.l + PW, y1: Y(l.y), y2: Y(l.y), stroke: c, 'stroke-dasharray': '6 4', 'stroke-width': 1.5 }, g);
      el('text', { x: M.l + PW + 6, y: Y(l.y) + 4, class: 'lim', fill: c }, g).textContent = l.label;
    }
    // event markers
    for (const e of ctx.events) {
      if (e.i < a || e.i > b) continue;
      el('path', { d: `M${X(e.i) - 5},${M.t - 2} L${X(e.i) + 5},${M.t - 2} L${X(e.i)},${M.t + 7} Z`, fill: COLOR[LEVEL[e.level]] || C.m }, g);
    }
    for (const s of spec.series) {
      let d = '', pen = false;
      const step = Math.max(1, Math.floor(n / 900));
      for (let i = a; i <= b; i += step) {
        const v = s.values[i];
        if (v == null || !Number.isFinite(v)) { pen = false; continue; }
        d += `${pen ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`; pen = true;
      }
      el('path', { d, fill: 'none', stroke: s.color, 'stroke-width': s.width || 1.6, 'stroke-dasharray': s.dash || 'none', 'stroke-linejoin': 'round' }, g);
    }
    const legend = el('g', {}, svg);
    spec.series.forEach((s, k) => {
      el('line', { x1: M.l + PW + 6, x2: M.l + PW + 22, y1: H - 26 - k * 15, y2: H - 26 - k * 15, stroke: s.color, 'stroke-width': 2, 'stroke-dasharray': s.dash || 'none' }, legend);
      el('text', { x: M.l + PW + 26, y: H - 22 - k * 15, class: 'ax' }, legend).textContent = s.name;
    });
    if (ctx.cursor != null && ctx.cursor >= a && ctx.cursor <= b) {
      el('line', { x1: X(ctx.cursor), x2: X(ctx.cursor), y1: M.t, y2: M.t + PH, stroke: '#fff', 'stroke-width': 1, opacity: 0.6 }, g);
    }
    // hover readout
    const tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; wrap.append(tip);
    const hover = el('line', { y1: M.t, y2: M.t + PH, stroke: 'rgba(255,255,255,.4)', visibility: 'hidden' }, g);
    svg.addEventListener('pointermove', (ev) => {
      const r = svg.getBoundingClientRect(), x = ((ev.clientX - r.left) / r.width) * W;
      if (x < M.l || x > M.l + PW) { tip.hidden = true; hover.setAttribute('visibility', 'hidden'); return; }
      const i = Math.round(a + ((x - M.l) / PW) * (n - 1));
      hover.setAttribute('x1', X(i)); hover.setAttribute('x2', X(i)); hover.setAttribute('visibility', 'visible');
      tip.hidden = false;
      tip.innerHTML = `<time>${ctx.xLabel(i, true)}</time>` + spec.series.map((s) => `<span style="color:${s.color}">${s.name}: <b>${fmt(s.values[i], 2)}</b> ${spec.unit || ''}</span>`).join('');
      const px = (X(i) / W) * r.width; tip.style.left = `${Math.min(r.width - 230, px + 12)}px`; tip.style.top = '28px';
    });
    svg.addEventListener('pointerleave', () => { tip.hidden = true; hover.setAttribute('visibility', 'hidden'); });
    box.append(wrap);
  }

  // ---------------------------------------------------------------- player (one per timebar)
  function Player(opts) {
    const p = { pos: 0, playing: false, tick: opts.tick, speed: 0, n: opts.n, onChange: opts.onChange, timer: null, last: 0 };
    const host = document.createElement('div'); host.className = 'panel glass tb-panel';
    host.innerHTML = `
      <div class="panel-title"><span>${opts.title}</span><span class="rate"></span></div>
      <div class="timeline-body">
        <div class="controls">
          <button class="back" title="Back ${opts.stepName}">&#9198;</button>
          <button class="play" title="Play / pause">&#9654;</button>
          <button class="fwd" title="Forward ${opts.stepName}">&#9197;</button>
          <label class="tick" title="Seconds of playback for each ${opts.unit} of data at 1×">1 ${opts.unit} every
            <input type="number" min="0.02" max="60" step="0.05" value="${opts.tick}"> s</label>
          <button class="speed" title="Fast-forward / slow down (multiplies the tick rate)">1×</button>
          <span class="time"></span>
        </div>
        <div class="track"><canvas></canvas><span class="knob"></span></div>
        <div class="ticks"></div>
      </div>`;
    const q = (s) => host.querySelector(s);
    const rate = () => { const k = SPEEDS[p.speed]; q('.rate').textContent = `1 ${opts.unit} every ${+(p.tick / k).toPrecision(3)} s` + (k === 1 ? '' : ` (${p.tick} s at ${k}×)`); };
    const set = (pos) => { p.pos = Math.max(0, Math.min(p.n - 1, pos)); draw(); p.onChange(Math.floor(p.pos)); };
    const draw = () => { q('.knob').style.left = `${(p.pos / (p.n - 1)) * 100}%`; q('.time').textContent = opts.label(Math.floor(p.pos)); };
    // 20 ms player tick; wall-clock based so the speed holds even if the browser throttles
    const tick = () => { const now = performance.now(), dt = (now - p.last) / 1000; p.last = now; const before = Math.floor(p.pos);
      p.pos = Math.min(p.n - 1, p.pos + (dt / p.tick) * SPEEDS[p.speed]); draw(); if (Math.floor(p.pos) !== before) p.onChange(Math.floor(p.pos)); if (p.pos >= p.n - 1) play(false); };
    const play = (on) => { if (on && p.pos >= p.n - 1) p.pos = 0; p.playing = on; q('.play').innerHTML = on ? '&#10074;&#10074;' : '&#9654;';
      clearInterval(p.timer); if (on) { p.last = performance.now(); p.timer = setInterval(tick, 20); } };
    q('.play').onclick = () => play(!p.playing);
    q('.back').onclick = () => set(Math.floor(p.pos) - opts.step);
    q('.fwd').onclick = () => set(Math.floor(p.pos) + opts.step);
    q('.speed').onclick = () => { p.speed = (p.speed + 1) % SPEEDS.length; q('.speed').textContent = `${SPEEDS[p.speed]}×`; rate(); };
    q('input').addEventListener('change', (e) => { const v = Number(e.target.value); p.tick = Number.isFinite(v) && v > 0 ? Math.min(60, Math.max(0.02, v)) : opts.tick; e.target.value = p.tick; rate(); });
    const track = q('.track');
    const scrub = (e) => { const r = track.getBoundingClientRect(); set(((e.clientX - r.left) / r.width) * (p.n - 1)); };
    track.addEventListener('pointerdown', (e) => { track.setPointerCapture(e.pointerId); scrub(e); });
    track.addEventListener('pointermove', (e) => { if (track.hasPointerCapture(e.pointerId)) scrub(e); });
    q('.ticks').innerHTML = [0, .25, .5, .75, 1].map((f) => `<span>${opts.tickLabel(Math.round(f * (p.n - 1)))}</span>`).join('');
    const strip = () => { const c = q('canvas'), w = Math.max(1, Math.round(c.clientWidth * devicePixelRatio)); c.width = w; c.height = 10; const x2 = c.getContext('2d');
      for (let x = 0; x < w; x++) { const i0 = Math.floor((x / w) * p.n), i1 = Math.max(i0 + 1, Math.floor(((x + 1) / w) * p.n)); let s = null;
        for (let i = i0; i < i1; i++) s = worst([s, opts.stripStatus(i)]); x2.globalAlpha = 0.8; x2.fillStyle = s ? COLOR[s] : 'rgba(255,255,255,.12)'; x2.fillRect(x, 0, 1, 10); } };
    new ResizeObserver(strip).observe(track);
    rate(); draw();
    return { host, set, get pos() { return Math.floor(p.pos); }, play };
  }

  // ---------------------------------------------------------------- app
  async function start({ twin }) {
    const [tb1, tb2] = await Promise.all([1, 2].map((n) => fetch(`${API}/timebar/${n}`).then((r) => r.json())));
    const day0 = Date.parse(tb1.start), y0 = Date.parse(tb2.start + 'T00:00:00Z');
    const clock1 = (m, full) => new Date(day0 + m * 60000).toISOString().slice(full ? 0 : 11, 16).replace('T', ' ');
    const date2 = (d) => tb2.series.dates[d] || '';
    const mons = monitors(tb1, tb2);
    const state = { m: 0, d: 0, filter: 'all', open: null, range: 'all', win: null };
    // events with their sample index on their own timebar
    for (const e of tb1.events) e.i = Math.floor(e.t / 60);
    for (const e of tb2.events) e.i = e.day;
    const cursor = (tb) => (tb === 1 ? state.m : state.d);

    // cards
    const cards = $('cards');
    const group = (tb, title) => `<div class="col-label glass"><span>${title}</span><span class="count" id="count-tb${tb}"></span></div>
      <div class="health-grid">${mons.filter((m) => m.tb === tb).map((m) => `
        <div class="health glass float" id="card-${m.id}" data-id="${m.id}" role="button" tabindex="0" title="Open the ${m.name} graphs">
          <span class="open" aria-hidden="true">&#8599;</span>
          <div class="row"><span class="name">${m.name}</span><span class="value">—</span></div>
          <div class="sub">${m.metric}</div>
          <div class="state">No data</div>
        </div>`).join('')}</div>`;
    cards.innerHTML = group(1, 'Timebar 1 · fast signals, 24 h') + group(2, 'Timebar 2 · daily, 1 year');
    cards.querySelectorAll('[data-id]').forEach((c) => { const go = () => openModal(c.dataset.id); c.onclick = go;
      c.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } }; });

    // players
    const players = $('timebars');
    const p1 = Player({ title: 'Timebar 1 · ' + new Date(day0).toISOString().slice(0, 10) + ' · fast signals', n: tb1.minutes, unit: 'minute', tick: 0.25,
      step: 60, stepName: 'one hour', label: (m) => `${clock1(m)} UTC`, tickLabel: (m) => clock1(m),
      stripStatus: (m) => worst(mons.filter((x) => x.tb === 1).map((x) => x.status(m))), onChange: (m) => { state.m = m; render(); } });
    const p2 = Player({ title: 'Timebar 2 · one year · daily', n: tb2.days, unit: 'day', tick: 0.5, step: 7, stepName: 'one week',
      label: (d) => `${date2(d)} · day ${d + 1} of ${tb2.days}`, tickLabel: (d) => date2(d).slice(0, 7),
      stripStatus: (d) => worst(mons.filter((x) => x.tb === 2).map((x) => x.status(d))), onChange: (d) => { state.d = d; render(); } });
    players.append(p1.host, p2.host);

    // signal list filters
    document.querySelectorAll('#ev-filters [data-filter]').forEach((b) => (b.onclick = () => {
      document.querySelectorAll('#ev-filters [data-filter]').forEach((x) => x.setAttribute('aria-pressed', x === b));
      state.filter = b.dataset.filter; renderEvents();
    }));
    $('status').textContent = `CPRI 630 kVA · 11 / 0.433 kV · ${tb1.limits.voltage_class.replace('_', '/')} class: ${tb1.limits.voltage_winding} winding ` +
      `+${tb1.limits.voltage_high_pct} / −${tb1.limits.voltage_low_pct} % · I_rated ${tb1.limits.i_rated_primary_a} A / ${tb1.limits.i_rated_secondary_a} A`;

    let lastModel = null;
    function render() {
      $('clock').textContent = `${clock1(state.m, true)} UTC`;
      $('clock-sub').textContent = `Timebar 2: ${date2(state.d)}`;
      const statuses = {};
      const counts = { 1: 0, 2: 0 };
      for (const m of mons) {
        const i = cursor(m.tb), st = m.status(i) || 'NO_DATA', v = m.value(i);
        statuses[m.id] = st;
        const card = $(`card-${m.id}`);
        card.className = card.className.replace(/\bs-\S+/g, '').trim(); card.classList.add('s-' + st);
        card.querySelector('.value').textContent = v == null ? '—' : `${m.signed && v > 0 ? '+' : ''}${fmt(v, m.digits ?? 1)} ${m.unit}`;
        card.querySelector('.state').textContent = LABEL[st] || st;
        if (RANK[st] > 0) counts[m.tb]++;
      }
      $('count-tb1').textContent = counts[1] ? `${counts[1]} need attention` : 'All within limits';
      $('count-tb2').textContent = counts[2] ? `${counts[2]} need attention` : 'All within limits';
      const overall = worst(Object.values(statuses));
      const pill = $('overall'); pill.className = 'pill s-' + overall;
      pill.textContent = overall === 'NORMAL' ? 'All monitors normal' : tb1.series.energized[state.m] ? `Overall: ${LABEL[overall]}` : 'Breaker open · out of service';
      // 3D: worst status per sensor slot; focus on the worst monitor
      const slots = [1, 2, 3, 4, 5, 6, 7].map((s) => { const w = worst(mons.filter((m) => m.slot === s).map((m) => statuses[m.id])); return w === 'OFF' || w === 'NO_DATA' ? null : w; });
      twin.setStatuses(slots);
      const top = mons.filter((m) => RANK[statuses[m.id]] > 0).sort((a, b) => RANK[statuses[b.id]] - RANK[statuses[a.id]])[0];
      const key = top ? top.id + statuses[top.id] : 'base';
      root.TimebarsApp.currentSlot = top ? top.slot : null;
      if (key !== lastModel) {
        lastModel = key;
        $('model-name').textContent = top ? `${top.name}` : 'Base transformer';
        $('model-signal').textContent = top ? `Timebar ${top.tb} · ${LABEL[statuses[top.id]]}` : 'All monitors normal';
        const vp = $('model-viewport'); vp.className = vp.className.replace(/\bs-\S+/g, '').trim(); if (top) vp.classList.add('s-' + statuses[top.id]);
        twin.focus(top ? top.slot : null);
      }
      $('model-call').textContent = tb1.series.energized[state.m] ? '' : 'Breaker open: transformer out of service';
      renderEvents();
      if (state.open) renderModalSoon();
    }

    function eventsUpTo() {
      const list = [];
      if (state.filter !== '2') list.push(...tb1.events.filter((e) => e.i <= state.m));
      if (state.filter !== '1') list.push(...tb2.events.filter((e) => e.i <= state.d));
      return list.sort((a, b) => (b.timebar - a.timebar) || (b.i - a.i) || (b.t || 0) - (a.t || 0));
    }
    function renderEvents() {
      const list = eventsUpTo();
      $('ev-count').textContent = `· ${list.length} of ${(state.filter !== '2' ? tb1.events.length : 0) + (state.filter !== '1' ? tb2.events.length : 0)}`;
      $('ev-list').innerHTML = list.length ? list.slice(0, 60).map((e) => `
        <div class="check s-${LEVEL[e.level] || 'NORMAL'}" data-mon="${e.timebar === 2 && e.monitor.startsWith('prec') ? 'y_' + e.monitor : e.monitor}" data-tb="${e.timebar}" data-i="${e.i}" role="button" tabindex="0">
          <span class="no">TB${e.timebar}</span><span class="what">${e.type.replaceAll('_', ' ').toLowerCase()} · ${LABEL[LEVEL[e.level]] || e.level}</span>
          <span class="action">${e.message}</span>
          <span class="when">${e.timebar === 1 ? `${e.start.slice(11, 19)}${e.end && e.end !== e.start ? '–' + e.end.slice(11, 19) : ''} UTC` : e.start}</span>
        </div>`).join('') : '<div class="empty">No signals yet. Press play on a timebar.</div>';
      $('ev-list').querySelectorAll('[data-mon]').forEach((x) => (x.onclick = () => {
        const id = x.dataset.mon === 'thermal' || mons.some((m) => m.id === x.dataset.mon) ? x.dataset.mon : null;
        if (id) openModal(id);
      }));
    }

    // modal
    let modalTimer = null;
    const renderModalSoon = () => { if (!modalTimer) modalTimer = setTimeout(() => { modalTimer = null; renderModal(); }, 250); };
    function openModal(id) { state.open = id; state.win = null; $('tb-modal').hidden = false; renderModal(); $('tb-close').focus(); }
    function closeModal() { state.open = null; $('tb-modal').hidden = true; }
    $('tb-close').onclick = closeModal;
    $('tb-modal').addEventListener('click', (e) => { if (e.target.id === 'tb-modal') closeModal(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && state.open) closeModal(); });
    document.querySelectorAll('#tb-range [data-range]').forEach((b) => (b.onclick = () => {
      document.querySelectorAll('#tb-range [data-range]').forEach((x) => x.setAttribute('aria-pressed', x === b));
      state.range = b.dataset.range; renderModal();
    }));
    function renderModal() {
      const m = mons.find((x) => x.id === state.open); if (!m) return;
      const tb = m.tb === 1 ? tb1 : tb2, i = cursor(m.tb), n = m.tb === 1 ? tb1.minutes : tb2.days, st = m.status(i) || 'NO_DATA';
      $('tb-title').textContent = m.name;
      $('tb-sub').textContent = `${m.metric} · cursor ${m.tb === 1 ? clock1(i, true) + ' UTC' : date2(i)}`;
      $('tb-tag').textContent = `Timebar ${m.tb}`;
      const pill = $('tb-status'); pill.className = 'pill s-' + st; pill.textContent = LABEL[st] || st;
      const zoom = m.tb === 1 ? 60 : 30;
      const range = state.range === 'zoom' ? [Math.max(0, i - zoom), Math.min(n - 1, i + zoom)] : [0, n - 1];
      $('tb-range').querySelector('[data-range="zoom"]').textContent = m.tb === 1 ? '±1 hour' : '±30 days';
      const evs = (m.tb === 1 ? tb1.events : tb2.events).filter((e) => (e.monitor === m.id || (m.tb === 2 && 'y_' + e.monitor === m.id)));
      const ctx = { range, cursor: i, events: evs, offline: m.tb === 1 ? tb1.series.energized.map((x) => !x) : null,
        xLabel: (k, full) => (m.tb === 1 ? clock1(k, full) : date2(k)) };
      const box = $('tb-charts'); box.innerHTML = '';
      for (const spec of m.charts()) drawChart(box, spec, ctx);
      // 20 ms windows for the electrical monitors
      if (m.windows) {
        const wins = tb1.windows.filter((w) => (m.windows === 'OVERLOAD' ? w.kind === 'OVERLOAD' : w.kind !== 'OVERLOAD'));
        const bar = document.createElement('div'); bar.className = 'win-bar';
        bar.innerHTML = '<span>20 ms detail (per-cycle RMS):</span>' + wins.map((w) => `<button data-w="${w.id}" aria-pressed="${state.win === w.id}">${w.id.replaceAll('_', ' ')} · ${w.start.slice(11, 19)}</button>`).join('');
        box.prepend(bar);
        bar.querySelectorAll('[data-w]').forEach((b) => (b.onclick = () => { state.win = state.win === b.dataset.w ? null : b.dataset.w; renderModal(); }));
        const w = wins.find((x) => x.id === state.win);
        if (w) {
          const L = tb1.limits, isV = m.windows === 'VOLTAGE';
          const t0 = Date.parse(w.start);
          drawChart(bar.parentNode, isV
            ? { title: `${w.id.replaceAll('_', ' ')}: voltage deviation every 20 ms (%)`, unit: '%', series: [{ name: 'Per-cycle RMS', values: w.voltage_deviation_pct, color: C.a }],
              lines: [{ y: L.voltage_high_pct, label: `+${L.voltage_high_pct} %`, level: 'WARNING' }, { y: -L.voltage_low_pct, label: `−${L.voltage_low_pct} %`, level: 'WARNING' }] }
            : { title: `${w.id.replaceAll('_', ' ')}: loading every 20 ms (%)`, unit: '%', series: [{ name: 'Per-cycle RMS', values: w.loading_pct, color: C.a }],
              lines: [{ y: L.overload_warning_pct, label: `Warning ${L.overload_warning_pct} %`, level: 'WARNING' }, { y: L.overload_trip_pct, label: `Trip ${L.overload_trip_pct} %`, level: 'CRITICAL' }] },
          { range: [0, w.loading_pct.length - 1], cursor: null, events: [], xLabel: (k) => new Date(t0 + k * w.dt * 1000).toISOString().slice(11, 22) });
          bar.after(bar.parentNode.lastChild);
        }
      }
      const shown = evs.filter((e) => e.i <= i);
      $('tb-events').innerHTML = shown.length ? shown.slice().reverse().map((e) => `<li><time>${m.tb === 1 ? e.start.slice(11, 19) : e.start}</time><span><b>${e.type.replaceAll('_', ' ').toLowerCase()}</b> · ${e.message}</span></li>`).join('')
        : '<li class="none">No signals for this monitor up to the cursor.</li>';
    }

    render();
    root.TimebarsApp.players = { p1, p2 };
  }

  root.TimebarsApp = { start };
})(window);
