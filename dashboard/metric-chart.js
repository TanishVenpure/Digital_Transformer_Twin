/*
 * Per-metric health graph, opened by clicking a monitor card.
 *
 * One series per chart (the metric), one y-axis. Two views of the same data:
 *   health %  : 100 = as new (reference level), 0 = at the critical limit, below 0 = past it.
 *               Every metric declines toward 0 as it degrades, whichever way its limit points.
 *   value     : the measured quantity in its own unit, with the real threshold values.
 * Dashed lines mark the warning / critical limits (status colours + text labels).
 * Top lane: maintenance checks raised by threshold hits. Vertical lines: maintenance performed.
 * The window ends at the timeline's playback hour, so playing the timeline draws the graph live.
 */
(function (root) {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const RANGES = { '7d': 7 * 24, '30d': 30 * 24, all: Infinity };
  // Reference "as new" level per monitor slot, for the health % view.
  const REFERENCE = { 1: 0, 2: 0, 3: 60, 4: 0, 5: 86, 6: 35, 7: 25 };

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.append(e);
    return e;
  }
  function niceTicks(lo, hi, count) {
    const span = hi - lo || 1, step0 = span / count, mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) || mag * 10;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }

  function healthOf(s, v, iot) {
    if (v == null || !iot) return null;
    const ref = REFERENCE[s], crit = iot.critical;
    const h = iot.operator.startsWith('>') ? (100 * (crit - v)) / (crit - ref) : (100 * (v - crit)) / (ref - crit);
    return Math.max(-60, Math.min(100, h));
  }

  function create(opts) {
    const { MONITORS, LABEL, COLOR, GROUP, getState, onClose } = opts;
    const overlay = document.getElementById('metric-overlay');
    const svg = document.getElementById('metric-svg');
    const tip = document.getElementById('metric-tip');
    const plotWrap = document.getElementById('metric-plot');
    // link options: ?range=7d|30d|all  ?chart=health|value
    const params = new URLSearchParams(location.search);
    let slot = null, hoverX = null;
    let range = RANGES[params.get('range')] ? params.get('range') : '7d';
    let view = params.get('chart') === 'value' ? 'value' : 'health';
    const press = (sel, attr, val) => document.querySelectorAll(sel).forEach((x) => x.setAttribute('aria-pressed', x.dataset[attr] === val));
    press('#metric-range [data-range]', 'range', range);
    press('#metric-view [data-view]', 'view', view);

    function open(s) {
      slot = s; hoverX = null;
      overlay.hidden = false;
      document.body.style.overflow = 'hidden';
      update(true);
      document.getElementById('metric-close').focus();
    }
    function close() {
      overlay.hidden = true; slot = null;
      document.body.style.overflow = '';
      onClose && onClose();
    }

    function windowData() {
      const st = getState(), frames = st.data.frames, i = slot - 1, iot = st.data.iots[i];
      const end = Math.min(frames.length - 1, Math.floor(st.pos));
      const hours = RANGES[range];
      const start = hours === Infinity ? 0 : Math.max(0, end - hours + 1);
      const t0 = Date.parse(frames[start].t);
      const tEnd = Date.parse(frames[end].t);
      const domainEnd = hours === Infinity ? Date.parse(frames[frames.length - 1].t) : Math.max(tEnd, t0 + (hours - 1) * 3600e3);
      const pts = [];
      for (let k = start; k <= end; k++) {
        const f = frames[k], v = f.v[i];
        pts.push({ t: Date.parse(f.t), v, h: healthOf(slot, v, iot), s: f.s[i], ttc: f.ttc[i] });
      }
      const inWin = (iso) => { const t = Date.parse(iso); return t >= t0 && t <= tEnd; };
      const checks = st.sheet.filter((c) => c.iot === slot && inWin(c.opened_at));
      const repairs = (st.data.events || []).filter((e) => e.iot === slot && inWin(e.ts));
      // oil: refill cycles completed by the playback hour, and flags raised by then (no look-ahead)
      const leak = st.data.analytics && st.data.analytics.oil_leak;
      const isOil = leak && leak.slot === slot;
      const cycles = isOil ? leak.cycles.filter((c) => c.end && Date.parse(c.end) <= tEnd && Date.parse(c.end) >= t0) : [];
      const flags = isOil ? leak.flags.filter((f) => Date.parse(f.detected_at) <= tEnd) : [];
      const load = st.data.analytics && st.data.analytics.load_growth;
      const isLoad = load && load.slot === slot;
      const reviews = isLoad ? load.reviews.filter((r) => Date.parse(r.at) <= tEnd) : [];
      const loadFlags = isLoad ? load.flags.filter((f) => Date.parse(f.detected_at) <= tEnd) : [];
      return { st, iot, pts, t0, tEnd, domainEnd, checks, repairs, now: frames[end], isOil, cycles, flags, allCycles: isOil ? leak.cycles.filter((c) => c.end && Date.parse(c.end) <= tEnd) : [],
        isLoad, review: reviews[reviews.length - 1] || null, loadFlags,
        ...windingData(st, slot, start, end, tEnd), ...topOilData(st, slot, start, end, tEnd) };
    }

    // top-oil: expected top-oil (load + weather), heat breakdown, cooling health, cleaning cycles, oil ageing.
    // Everything is cut at the playback hour (no look-ahead); the cleaning-due forecast is fitted live.
    function topOilData(st, s, start, end, tEnd) {
      const a = st.data.analytics && st.data.analytics.top_oil;
      if (!a || a.slot !== s) return { isTopOil: false };
      const H = a.hourly, sl = (arr) => arr.slice(start, end + 1), frames = st.data.frames;
      const cleaned = a.cycles.filter((c) => c.started_by_cleaning && Date.parse(c.start) <= tEnd);
      const lastClean = cleaned.length ? Date.parse(cleaned[cleaned.length - 1].start) : Date.parse(frames[0].t);
      // cleaning due: straight line through cooling health since the last cleaning (+6 h settling)
      let due = null;
      const xs = [], ys = [];
      for (let k = 0; k <= end; k++) {
        const t = Date.parse(frames[k].t), h = H.cooling_health[k];
        if (h != null && t >= lastClean + 6 * 3600e3) { xs.push(t / 86400e3); ys.push(h); }
      }
      if (xs.length >= 72) {
        const n = xs.length, mx = xs.reduce((p, x) => p + x, 0) / n, my = ys.reduce((p, y) => p + y, 0) / n;
        let sxy = 0, sxx = 0;
        xs.forEach((x, i) => { sxy += (x - mx) * (ys[i] - my); sxx += (x - mx) ** 2; });
        const slope = sxx ? sxy / sxx : 0, now = my + slope * (xs[n - 1] - mx);
        if (slope < 0) due = Math.max(0, (now - a.clean_below) / -slope);
      }
      const weeks = a.weeks.filter((w) => w.complete && Date.parse(w.end) <= tEnd);
      return { isTopOil: true, cleanBelow: a.clean_below,
        toExpected: sl(H.expected), toAmbient: sl(H.ambient), toCool: sl(H.cooling_k), toLow: sl(H.low_oil_k), toHealth: sl(H.cooling_health),
        toCycles: cleaned.filter((c) => c.end && Date.parse(c.end) <= tEnd),
        toRecovered: cleaned.filter((c) => c.recovered_health != null && Date.parse(c.start) + 3 * 86400e3 <= tEnd).map((c) => c.recovered_health),
        toChecks: a.cooling_checks.filter((f) => Date.parse(f.detected_at) <= tEnd && Date.parse(f.detected_at) >= lastClean),
        toFlags: a.flags.filter((f) => Date.parse(f.detected_at) <= tEnd),
        toWeek: weeks[weeks.length - 1] || null, toDue: due };
    }

    // winding: hourly expected rise (thermal model) for the window + latest weekly review / flags
    function windingData(st, s, start, end, tEnd) {
      const wd = st.data.analytics && st.data.analytics.winding;
      if (!wd || wd.slot !== s) return { isWinding: false };
      const reviews = wd.reviews.filter((r) => Date.parse(r.at) <= tEnd);
      return { isWinding: true, expected: wd.expected.slice(start, end + 1),
        thermalReview: reviews[reviews.length - 1] || null,
        thermalFlags: wd.flags.filter((f) => Date.parse(f.detected_at) <= tEnd) };
    }

    function update(force) {
      if (slot == null) return;
      const w = windowData();
      const m = MONITORS[slot];
      document.getElementById('metric-title').textContent = m.name;
      const tag = document.getElementById('metric-group');
      tag.className = 'tag g-' + m.group; tag.textContent = GROUP[m.group].label;
      const last = w.pts[w.pts.length - 1];
      const pill = document.getElementById('metric-status');
      pill.className = 'pill s-' + (last.s || 'NONE');
      pill.textContent = last.s ? LABEL[last.s] : 'No data';
      document.getElementById('metric-sub').textContent = m.metric + ' · window ends ' + new Date(w.tEnd).toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
      stats(w, m);
      banner(w);
      draw(w, m);
      repairsList(w);
    }

    function stats(w, m) {
      const last = w.pts[w.pts.length - 1];
      if (w.isTopOil) return topOilStats(w, m, last);
      const valid = w.pts.filter((p) => p.s);
      const healthy = valid.length ? Math.round((100 * valid.filter((p) => p.s === 'NORMAL').length) / valid.length) : null;
      const [num, unit] = last.v == null ? ['—', ''] : m.fmt(last.v);
      const lastRepair = w.repairs.length ? w.repairs[w.repairs.length - 1].ts.slice(0, 10) : null;
      const tiles = [
        ['Health now', last.h == null ? '—' : Math.round(last.h) + '%', 'as-new = 100%, critical = 0%'],
        ['Measured', num + unit, w.iot.unit.replaceAll('_', ' ')],
        ['Time healthy', healthy == null ? '—' : healthy + '%', 'of hours in window'],
        ['Checks raised', String(w.checks.length), 'threshold hits in window'],
        ['Maintenance done', String(w.repairs.length), lastRepair ? 'last ' + lastRepair : 'none in window'],
        ['Time to critical', last.ttc == null ? '—' : last.ttc < 48 ? Math.round(last.ttc) + ' h' : (last.ttc / 24).toFixed(1) + ' d', 'from current trend'],
      ];
      document.getElementById('metric-stats').innerHTML = tiles.map(([k, v, s]) =>
        `<div class="stat"><span>${k}</span><b>${v}</b><small>${s}</small></div>`).join('');
    }

    const DIAGNOSIS = {
      healthy: 'top-oil and winding both match the thermal model',
      cooling: 'top-oil and winding run hot together → radiators / oil, not the windings',
      ducts: 'only the winding runs hot → internal winding cooling ducts',
      both: 'radiators / oil degraded and the winding ducts as well',
    };
    function topOilStats(w, m, last) {
      const k = w.pts.length - 1, [num, unit] = last.v == null ? ['—', ''] : m.fmt(last.v);
      const amb = w.toAmbient[k], exp = w.toExpected[k], fault = (w.toCool[k] || 0) + (w.toLow[k] || 0);
      const why = amb == null || exp == null || last.v == null ? '—'
        : `${amb.toFixed(0)}+${(exp - amb).toFixed(0)}+${Math.max(0, fault).toFixed(0)}`;
      const h = w.toHealth[k], lastRepair = w.repairs.length ? w.repairs[w.repairs.length - 1].ts.slice(0, 10) : null;
      const tiles = [
        ['Measured', num + unit, 'top-oil now'],
        ['Cooling health', h == null ? '—' : Math.round(h) + '%', '100% = radiators as designed'],
        ['Why it\'s hot', why, 'ambient + load + fault, °C'],
        ['Weather-adjusted', w.toWeek && w.toWeek.normalised_top_oil_c != null ? w.toWeek.normalised_top_oil_c.toFixed(1) + '°C' : '—', 'rated load on a 30 °C day (last week)'],
        ['Cleaning due', w.toDue == null ? '—' : w.toDue < 0.5 ? 'now' : '~' + Math.round(w.toDue) + ' d', `cooling trend to ${w.cleanBelow}%`],
        ['Cleanings done', String(w.repairs.length), lastRepair ? 'last ' + lastRepair : 'none in window'],
      ];
      document.getElementById('metric-stats').innerHTML = tiles.map(([key, v, s]) =>
        `<div class="stat"><span>${key}</span><b>${v}</b><small>${s}</small></div>`).join('');
    }

    function banner(w) {
      const el = document.getElementById('metric-banner');
      document.getElementById('legend-trend').hidden = !w.isLoad;
      document.getElementById('legend-expected').hidden = !(w.isWinding || w.isTopOil);
      document.getElementById('legend-expected-text').textContent = w.isTopOil
        ? 'Expected from load + weather (thermal model)' : 'Expected at this load (thermal model)';
      document.querySelectorAll('.legend-topoil').forEach((x) => x.hidden = !w.isTopOil);
      if (w.isTopOil) {
        const flag = w.toFlags[w.toFlags.length - 1], check = w.toChecks[w.toChecks.length - 1];
        const k = w.pts.length - 1, h = w.toHealth[k];
        const arrows = (xs, f) => xs.slice(-4).map(f).join(' → ');
        const intervals = w.toCycles.map((c) => c.interval_days).filter((x) => x);
        const cross = w.toWeek ? ` · winding check: ${DIAGNOSIS[w.toWeek.diagnosis]}` : '';
        el.hidden = false;
        el.className = 'banner' + (flag || check ? ' alert' : '');
        if (flag) {
          el.innerHTML = `<b>Oil ageing</b> flagged ${flag.detected_at.slice(0, 10)}: ${flag.message.replace(/^Oil ageing: /, '')}${cross}`;
        } else {
          el.innerHTML = `<b>Cooling review:</b> cooling health ${h == null ? '—' : Math.round(h) + '%'}`
            + (check ? ` · <b>clean radiators</b> (below ${w.cleanBelow}% since ${check.detected_at.slice(0, 10)})` : '')
            + (w.toRecovered.length ? ` · after each cleaning ${arrows(w.toRecovered, (x) => Math.round(x))}%` : ' · no cleaning yet')
            + (intervals.length ? ` · cleaning intervals ${arrows(intervals, (x) => x.toFixed(1))} d` : '') + cross;
        }
        return;
      }
      if (w.isWinding) {
        const r = w.thermalReview, flag = w.thermalFlags[w.thermalFlags.length - 1];
        el.hidden = false;
        el.className = 'banner' + (flag ? ' alert' : '');
        if (flag) {
          el.innerHTML = `<b>Winding excess heating</b> flagged ${flag.detected_at.slice(0, 10)}: ${flag.message.replace(/^Winding running hotter than its load explains: /, '')}`;
        } else if (r) {
          el.innerHTML = `<b>Weekly thermal review ${r.at.slice(0, 10)}:</b> measured vs thermal model ${r.excess_by_week.map((x) => (x >= 0 ? '+' : '') + x.toFixed(1)).join(' → ')} K · `
            + `insulation ageing ${r.ageing_days_week.toFixed(2)} equivalent days this week (${r.ageing_rate.toFixed(3)}× design rate) · life used so far ${r.loss_of_life_days.toFixed(2)} days`;
        } else {
          el.innerHTML = '<b>Weekly thermal review:</b> needs at least one full week of data';
        }
        return;
      }
      if (w.isLoad) {
        const r = w.review, flag = w.loadFlags[w.loadFlags.length - 1];
        el.hidden = false;
        el.className = 'banner' + (flag ? ' alert' : '');
        if (flag) {
          el.innerHTML = `<b>Load growth</b> flagged ${flag.detected_at.slice(0, 10)}: ${flag.message.replace(/^Load growth: /, '')}`;
        } else if (r) {
          el.innerHTML = `<b>Weekly load review ${r.at.slice(0, 10)}:</b> overload hours per week ${r.overload_hours_by_week.join(' → ')} · daily-peak trend ${r.slope_per_day >= 0 ? '+' : ''}${r.slope_per_day.toFixed(2)} %/day`
            + (r.forecast_exceeds_rated ? ` · peaks reach rated current ~${r.forecast_exceeds_rated}` : r.already_at_rated ? ' · trend already at rated current' : '');
        } else {
          el.innerHTML = '<b>Weekly load review:</b> needs at least one full week of data';
        }
        return;
      }
      if (!w.isOil) { el.hidden = true; return; }
      const rates = w.allCycles.filter((c) => c.leak_rate_per_day).map((c) => c.leak_rate_per_day.toFixed(1));
      const flag = w.flags[w.flags.length - 1];
      el.hidden = false;
      el.className = 'banner' + (flag ? ' alert' : '');
      el.innerHTML = flag
        ? `<b>Accelerating oil leak</b> flagged ${flag.detected_at.slice(0, 10)}: ${flag.message.replace(/^Accelerating oil leak: /, '')}`
        : `<b>Leak rate by refill cycle:</b> ${rates.length ? rates.join(' → ') + ' %/day' : 'no completed refill cycle yet'} · flagged if it rises ≥15% per cycle twice in a row`;
    }

    function repairsList(w) {
      const list = document.getElementById('metric-repairs');
      list.innerHTML = w.repairs.length
        ? w.repairs.slice().reverse().map((e) => `<li><time>${e.ts.slice(0, 16).replace('T', ' ')} UTC</time><span>${e.action}</span></li>`).join('')
        : '<li class="none">No maintenance performed in this window.</li>';
    }

    function draw(w, m) {
      const W = plotWrap.clientWidth, H = plotWrap.clientHeight;
      if (!W || !H) return;
      svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
      svg.innerHTML = '';
      const M = { l: 52, r: 104, t: 22, b: 40 };
      const PW = W - M.l - M.r, PH = H - M.t - M.b;
      const health = view === 'health';
      const key = health ? 'h' : 'v';
      const iot = w.iot;
      const limits = [];
      if (iot.warning != null) limits.push({ name: 'Warning', status: 'WARNING', value: health ? healthOf(slot, iot.warning, iot) : iot.warning });
      limits.push({ name: 'Critical', status: 'CRITICAL', value: health ? 0 : iot.critical });

      const vals = w.pts.map((p) => p[key]).filter((v) => v != null);
      let lo, hi;
      if (health) { lo = Math.min(-10, ...vals); hi = 100; }
      else {
        const extra = w.isTopOil ? w.toAmbient.filter((v) => v != null) : [];
        lo = Math.min(...vals, ...extra, ...limits.map((l) => l.value)); hi = Math.max(...vals, ...limits.map((l) => l.value));
        const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
      }
      const X = (t) => M.l + ((t - w.t0) / Math.max(1, w.domainEnd - w.t0)) * PW;
      // Measured view of an upper-limit metric is drawn upside-down (high values low on the axis), so heading
      // toward a limit always reads as a decline - the same visual story as Oil Level and the Health % view.
      const inverted = !health && iot.operator.startsWith('>');
      const Y = (v) => M.t + (inverted ? (v - lo) / (hi - lo) : 1 - (v - lo) / (hi - lo)) * PH;
      document.getElementById('legend-inverted').hidden = !inverted;
      const g = el('g', {}, svg);

      // zones: between warning and critical, and beyond critical (light status tints)
      const critY = Y(limits[limits.length - 1].value);
      const warnLimit = limits.find((l) => l.status === 'WARNING');
      const edgeBad = M.t + PH;   // the bad side is always at the bottom now
      el('rect', { x: M.l, width: PW, y: Math.min(critY, edgeBad), height: Math.abs(edgeBad - critY), fill: COLOR.CRITICAL, opacity: 0.07 }, g);
      if (warnLimit) {
        const wy = Y(warnLimit.value);
        el('rect', { x: M.l, width: PW, y: Math.min(wy, critY), height: Math.abs(critY - wy), fill: COLOR.WARNING, opacity: 0.06 }, g);
      }

      // gridlines + y ticks
      for (const v of niceTicks(lo, hi, 5)) {
        const y = Y(v);
        el('line', { x1: M.l, x2: M.l + PW, y1: y, y2: y, stroke: '#233040', 'stroke-width': 1 }, g);
        const tx = el('text', { x: M.l - 8, y: y + 4, 'text-anchor': 'end', class: 'ax' }, g);
        tx.textContent = health ? v + '%' : (Math.abs(v) >= 100 ? v.toFixed(0) : +v.toFixed(2));
      }
      // x ticks (dates)
      const span = w.domainEnd - w.t0, days = span / 86400e3;
      const stepDays = days <= 8 ? 1 : days <= 32 ? 5 : 14;
      const first = new Date(w.t0); first.setUTCHours(0, 0, 0, 0);
      for (let t = first.getTime() + 86400e3; t <= w.domainEnd; t += stepDays * 86400e3) {
        const x = X(t);
        if (x < M.l + 10 || x > M.l + PW - 10) continue;
        el('line', { x1: x, x2: x, y1: M.t + PH, y2: M.t + PH + 4, stroke: '#3a4a5e' }, g);
        const tx = el('text', { x, y: M.t + PH + 18, 'text-anchor': 'middle', class: 'ax' }, g);
        tx.textContent = new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' });
      }
      el('line', { x1: M.l, x2: M.l + PW, y1: M.t + PH, y2: M.t + PH, stroke: '#3a4a5e' }, g);

      // maintenance performed: vertical lines with a label marker at the bottom
      for (const e of w.repairs) {
        const x = X(Date.parse(e.ts));
        el('line', { x1: x, x2: x, y1: M.t, y2: M.t + PH, stroke: '#e8eef5', 'stroke-width': 1.5, 'stroke-dasharray': '2 3', opacity: 0.55 }, g);
        el('circle', { cx: x, cy: M.t + PH - 10, r: 7, fill: '#0e131a', stroke: '#e8eef5', 'stroke-width': 1.5 }, g);
        const tx = el('text', { x, y: M.t + PH - 6.5, 'text-anchor': 'middle', class: 'wrench' }, g);
        tx.textContent = 'M';
      }

      // oil: label each completed refill cycle with its leak rate and refill interval
      w.cycles.forEach((c, k) => {
        const a = Math.max(Date.parse(c.start), w.t0), b = Date.parse(c.end);
        const px = X(b) - X(a);
        if (px < 34 || !c.leak_rate_per_day) return;
        const rate = c.leak_rate_per_day.toFixed(1);
        // full label when the cycle is wide, shorter ones when narrow; alternate rows so neighbours never collide
        const label = px >= 150 ? `${rate} %/day` + (c.interval_days ? ` · ${c.interval_days.toFixed(1)} d` : '')
          : px >= 80 ? `${rate}%/d` + (c.interval_days ? ` · ${c.interval_days.toFixed(0)}d` : '') : rate;
        const tx = el('text', { x: X((a + b) / 2), y: M.t + (px < 150 && k % 2 ? 28 : 14), 'text-anchor': 'middle', class: 'cyc' }, g);
        tx.textContent = label;
      });

      // current: daily-peak trend from the latest weekly review, projected to rated current
      if (w.isLoad && w.review) {
        const r = w.review, conv = (v) => (health ? healthOf(slot, v, iot) : v);
        const ta = Date.parse(r.trend_from.date + 'T12:00:00Z'), tb = Date.parse(r.trend_to.date + 'T12:00:00Z');
        const per = (r.trend_to.value - r.trend_from.value) / Math.max(1, (tb - ta) / 86400e3);
        const tx0 = Math.max(ta, w.t0);
        const v0 = r.trend_from.value + per * (tx0 - ta) / 86400e3;
        if (tb > w.t0) el('line', { x1: X(tx0), y1: Y(conv(v0)), x2: X(tb), y2: Y(conv(r.trend_to.value)), stroke: '#e8eef5', 'stroke-width': 2, opacity: 0.85 }, g);
        if (r.forecast_exceeds_rated) {
          const tf = Date.parse(r.forecast_exceeds_rated + 'T12:00:00Z');
          const tEndLine = Math.min(tf, w.domainEnd);
          const vEnd = r.trend_to.value + per * (tEndLine - tb) / 86400e3;
          el('line', { x1: X(tb), y1: Y(conv(r.trend_to.value)), x2: X(tEndLine), y2: Y(conv(vEnd)), stroke: '#e8eef5', 'stroke-width': 1.5, 'stroke-dasharray': '3 4', opacity: 0.8 }, g);
          if (tf <= w.domainEnd) {
            const fx = X(tf), fy = Y(conv(iot.critical));
            el('circle', { cx: fx, cy: fy, r: 5, fill: '#0e131a', stroke: '#e8eef5', 'stroke-width': 2 }, g);
            const lbl = el('text', { x: fx, y: fy - 10, 'text-anchor': fx > M.l + PW - 120 ? 'end' : 'middle', class: 'cyc' }, g);
            lbl.textContent = 'peaks reach rated ~' + new Date(tf).toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' });
          }
        }
      }

      // threshold lines, direct-labelled on the right
      for (const l of limits) {
        const y = Y(l.value);
        el('line', { x1: M.l, x2: M.l + PW, y1: y, y2: y, stroke: COLOR[l.status], 'stroke-width': 1.5, 'stroke-dasharray': '6 4' }, g);
        const unit = health ? '%' : m.fmt(l.value)[1];
        const lbl = health ? (l.status === 'CRITICAL' ? '0%' : Math.round(l.value) + '%') : (+l.value.toFixed(2)) + unit;
        const tx = el('text', { x: M.l + PW + 8, y: y + 4, class: 'lim' }, g);
        tx.textContent = `${l.name} ${lbl}`;
      }

      // top-oil: where the heat comes from - load band (ambient -> expected) and fault bands (expected -> measured)
      if (w.isTopOil) {
        const conv = (v) => (health ? healthOf(slot, v, iot) : v);
        const band = (lower, upper, fill, opacity) => {
          let run = [];
          const flush = () => {
            if (run.length > 1) {
              const top = run.map(([x, , u]) => `${x.toFixed(1)},${Y(conv(u)).toFixed(1)}`);
              const bot = run.slice().reverse().map(([x, l]) => `${x.toFixed(1)},${Y(conv(l)).toFixed(1)}`);
              el('path', { d: 'M' + top.join('L') + 'L' + bot.join('L') + 'Z', fill, opacity, stroke: 'none' }, g);
            }
            run = [];
          };
          w.pts.forEach((p, k) => {
            const l = lower(p, k), u = upper(p, k);
            if (l == null || u == null) flush(); else run.push([X(p.t), l, u]);
          });
          flush();
        };
        if (!health) band((p, k) => w.toAmbient[k], (p, k) => w.toExpected[k], '#64748b', 0.22);
        band((p, k) => w.toExpected[k], (p, k) => (p.v == null || w.toExpected[k] == null ? null
          : w.toExpected[k] + Math.max(0, Math.min(p.v - w.toExpected[k], w.toCool[k] || 0))), '#f59e0b', 0.35);
        band((p, k) => (w.toLow[k] > 0.3 && w.toExpected[k] != null ? w.toExpected[k] + Math.max(0, w.toCool[k] || 0) : null),
          (p, k) => (w.toLow[k] > 0.3 ? p.v : null), '#ef4444', 0.4);
        if (!health) {
          let da = '', on = false;
          w.pts.forEach((p, k) => {
            const a = w.toAmbient[k];
            if (a == null) { on = false; return; }
            da += (on ? 'L' : 'M') + X(p.t).toFixed(1) + ',' + Y(a).toFixed(1); on = true;
          });
          el('path', { d: da, fill: 'none', stroke: '#8d9bab', 'stroke-width': 1, 'stroke-dasharray': '1 3', opacity: 0.8 }, g);
        }
        // completed cleaning cycles: interval and cooling health recovered after the cleaning
        w.toCycles.forEach((c, k) => {
          const a = Math.max(Date.parse(c.start), w.t0), b = Date.parse(c.end);
          const px = X(b) - X(a);
          if (b < w.t0 || px < 40) return;
          const label = px >= 150 && c.recovered_health != null
            ? `${c.interval_days.toFixed(1)} d · cleaned to ${Math.round(c.recovered_health)}%` : `${c.interval_days.toFixed(0)}d`;
          const tx = el('text', { x: X((a + b) / 2), y: M.t + (px < 150 && k % 2 ? 28 : 14), 'text-anchor': 'middle', class: 'cyc' }, g);
          tx.textContent = label;
        });
      }

      // winding / top-oil: what the thermal model expects at this load (dashed) - the gap to the metric is the excess
      const expectedLine = w.isTopOil ? w.toExpected : w.isWinding ? w.expected : null;
      if (expectedLine) {
        const conv = (v) => (health ? healthOf(slot, v, iot) : v);
        let de = '', on = false;
        w.pts.forEach((p, k) => {
          const e = expectedLine[k];
          if (e == null) { on = false; return; }
          de += (on ? 'L' : 'M') + X(p.t).toFixed(1) + ',' + Y(conv(e)).toFixed(1);
          on = true;
        });
        el('path', { d: de, fill: 'none', stroke: '#e8eef5', 'stroke-width': 1.3, 'stroke-dasharray': '4 3', opacity: 0.75 }, g);
      }

      // the metric line (gaps where the input was invalid)
      let d = '', pen = false;
      for (const p of w.pts) {
        if (p[key] == null) { pen = false; continue; }
        d += (pen ? 'L' : 'M') + X(p.t).toFixed(1) + ',' + Y(p[key]).toFixed(1);
        pen = true;
      }
      el('path', { d, fill: 'none', stroke: '#38bdf8', 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, g);

      // checks raised: markers in a lane along the top, coloured by signal
      const half = Math.max(1.5, Math.min(4, PW / Math.max(1, w.checks.length) / 2.5));
      for (const c of w.checks) {
        const x = X(Date.parse(c.opened_at));
        el('path', { d: `M${x - half},${M.t - 12} L${x + half},${M.t - 12} L${x},${M.t - 12 + half * 1.75} Z`, fill: COLOR[c.signal] || '#8d9bab' }, g);
      }

      // "now" = playback hour
      const nx = X(w.tEnd);
      el('line', { x1: nx, x2: nx, y1: M.t - 4, y2: M.t + PH, stroke: '#38bdf8', 'stroke-width': 1, opacity: 0.6 }, g);
      const last = w.pts[w.pts.length - 1];
      if (last[key] != null) el('circle', { cx: nx, cy: Y(last[key]), r: 4.5, fill: '#38bdf8', stroke: '#0e131a', 'stroke-width': 2 }, g);

      // status strip below the axis
      const sw = Math.max(1, PW / Math.max(1, (w.domainEnd - w.t0) / 3600e3));
      for (const p of w.pts) {
        el('rect', { x: X(p.t) - sw / 2, y: M.t + PH + 26, width: sw + 0.5, height: 5, fill: p.s ? COLOR[p.s] : '#2a3442' }, g);
      }

      // hover crosshair
      const hover = el('g', { class: 'hover' }, svg);
      const hit = el('rect', { x: M.l, y: M.t, width: PW, height: PH, fill: 'transparent' }, svg);
      const show = (clientX) => {
        const r = svg.getBoundingClientRect();
        const t = w.t0 + ((clientX - r.left - M.l) / PW) * (w.domainEnd - w.t0);
        let best = null;
        for (const p of w.pts) if (!best || Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
        if (!best || t > w.tEnd + 3600e3) { hover.innerHTML = ''; tip.hidden = true; return; }
        hoverX = clientX;
        hover.innerHTML = '';
        const x = X(best.t);
        el('line', { x1: x, x2: x, y1: M.t, y2: M.t + PH, stroke: '#8d9bab', 'stroke-width': 1 }, hover);
        if (best[key] != null) el('circle', { cx: x, cy: Y(best[key]), r: 5, fill: '#38bdf8', stroke: '#0e131a', 'stroke-width': 2 }, hover);
        const [num, unit] = best.v == null ? ['—', ''] : m.fmt(best.v);
        const repair = w.repairs.find((e) => Math.abs(Date.parse(e.ts) - best.t) <= 3600e3);
        tip.innerHTML = `<time>${new Date(best.t).toISOString().slice(0, 16).replace('T', ' ')} UTC</time>
          <div><b>${num}${unit}</b> · health ${best.h == null ? '—' : Math.round(best.h) + '%'}</div>
          <div class="pill s-${best.s || 'NONE'}">${best.s ? LABEL[best.s] : 'No data'}</div>
          ${topOilTip(w, best)}
          ${repair ? `<div class="rep">Maintenance: ${repair.action}</div>` : ''}`;
        tip.hidden = false;
        const left = x + 14 + 220 > W ? x - 14 - 220 : x + 14;
        tip.style.left = left + 'px';
        tip.style.top = Math.max(4, (best[key] != null ? Y(best[key]) : M.t) - 30) + 'px';
      };
      hit.addEventListener('pointermove', (e) => show(e.clientX));
      hit.addEventListener('pointerleave', () => { hoverX = null; hover.innerHTML = ''; tip.hidden = true; });
      if (hoverX != null) show(hoverX);
    }

    function topOilTip(w, p) {
      if (!w.isTopOil || p.v == null) return '';
      const k = w.pts.indexOf(p), amb = w.toAmbient[k], exp = w.toExpected[k];
      if (amb == null || exp == null) return '';
      const cool = w.toCool[k] || 0, low = w.toLow[k] || 0, h = w.toHealth[k];
      return `<div>= ${amb.toFixed(0)} ambient + ${(exp - amb).toFixed(0)} load + ${cool.toFixed(1)} cooling${low > 0.3 ? ` + ${low.toFixed(1)} low oil` : ''} °C</div>`
        + (h != null ? `<div>Cooling health ${Math.round(h)}%</div>` : '');
    }

    // controls
    document.querySelectorAll('#metric-range [data-range]').forEach((b) => b.onclick = () => {
      range = b.dataset.range;
      document.querySelectorAll('#metric-range [data-range]').forEach((x) => x.setAttribute('aria-pressed', x === b));
      update(true);
    });
    document.querySelectorAll('#metric-view [data-view]').forEach((b) => b.onclick = () => {
      view = b.dataset.view;
      document.querySelectorAll('#metric-view [data-view]').forEach((x) => x.setAttribute('aria-pressed', x === b));
      update(true);
    });
    document.getElementById('metric-close').onclick = close;
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && slot != null) close(); });
    new ResizeObserver(() => update(true)).observe(plotWrap);

    return { open, close, update, get slot() { return slot; } };
  }

  root.MetricChart = { create, healthOf };
})(window);
