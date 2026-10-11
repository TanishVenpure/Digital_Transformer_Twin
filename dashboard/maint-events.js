// Timeline-driven maintenance events: pure functions shared by the dashboard and its tests.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MaintEvents = api;
})(typeof self !== "undefined" ? self : this, () => {
  const SIGNIFICANT = new Set(["WARNING", "MAINTENANCE_REQUIRED", "CRITICAL"]);

  const isSignificant = (c) => SIGNIFICANT.has(c.signal) || !!c.flag;

  // Positions default to timestamps (ms). A sheet can instead be played on any ordered position, e.g.
  // the minute or day index of a timebar: pass `at(check)` (opened) and `resolvedAt(check)` (or null).
  const openedMs = (c) => Date.parse(c.opened_at);
  const resolvedMs = (c) => (c.resolved_at ? Date.parse(c.resolved_at) : null);

  // Number of checks opened at or before `ms` (sheet is sorted by its opening position).
  function indexAt(sheet, ms, at = openedMs) {
    let lo = 0, hi = sheet.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (at(sheet[mid]) <= ms) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  const initial = () => ({ index: -1, shown: new Set() });

  // Advance to timestamp `ms`. Returns the next state plus what the UI should do:
  // toasts to show, check numbers whose toasts must be withdrawn (rewind), and the
  // history of significant checks recorded up to `ms` (newest first).
  function step(prev, sheet, ms, { maxToasts = 3, at = openedMs } = {}) {
    const index = indexAt(sheet, ms, at);
    const shown = new Set();
    const retract = [];
    for (const no of prev.shown) {
      const c = sheet.find((x) => x.check_no === no);
      if (c && at(c) <= ms) shown.add(no); else retract.push(no);
    }
    const crossed = [];
    if (prev.index >= 0 && index > prev.index) {
      for (let i = prev.index; i < index; i++) {
        const c = sheet[i];
        if (isSignificant(c) && !shown.has(c.check_no)) { crossed.push(c); shown.add(c.check_no); }
      }
    }
    const toasts = crossed.slice(-maxToasts);
    const history = [];
    for (let i = index - 1; i >= 0; i--) if (isSignificant(sheet[i])) history.push(sheet[i]);
    return { state: { index, shown }, toasts, suppressed: crossed.length - toasts.length, retract, history };
  }

  // A check's status as it was known at `ms`: resolution after `ms` is still in the future.
  const statusAt = (c, ms, resolvedAt = resolvedMs) => {
    const r = resolvedAt(c);
    return r != null && r <= ms ? "RESOLVED" : "OPEN";
  };

  const findCheck = (sheet, no) => sheet.find((c) => c.check_no === no) || null;

  // ---- historical evidence for a check ---------------------------------------------------------
  const HOUR = 3600e3;

  // The recorded series a check was evaluated on, aligned with timeline.frames:
  // the IoT's own readings when the units match, else the backend's hourly cooling-health
  // estimate for cooling checks. null when no series in the timeline matches the record.
  function evidenceSeries(timeline, c) {
    const iot = (timeline.iots || []).find((x) => x.slot === c.iot);
    if (iot && c.unit === iot.unit) {
      return { kind: "sensor", label: iot.display_name || iot.name, unit: iot.unit,
               values: timeline.frames.map((f) => f.v[c.iot - 1]) };
    }
    const health = timeline.analytics && timeline.analytics.top_oil && timeline.analytics.top_oil.hourly
      && timeline.analytics.top_oil.hourly.cooling_health;
    if (c.unit === "% cooling health" && Array.isArray(health) && health.length === timeline.frames.length) {
      return { kind: "derived", label: "Cooling health (hourly rolling estimate from top-oil, ambient and load readings)",
               unit: "% cooling health", values: health };
    }
    return null;
  }

  // Readings around the check, never past `nowMs`: from `beforeH` hours before the check to
  // `afterH` hours after it, clipped at the playback time. null if the check is still in the future.
  function evidenceWindow(frames, values, eventMs, nowMs, { beforeH = 48, afterH = 24 } = {}) {
    if (!(eventMs <= nowMs)) return null;
    // Clipped to the recorded history: hours before the first frame were never recorded, not missed.
    const first = frames.length ? Date.parse(frames[0].t) : eventMs;
    const from = Math.max(first, eventMs - beforeH * HOUR), to = Math.min(nowMs, eventMs + afterH * HOUR);
    const points = [];
    for (let i = 0; i < frames.length; i++) {
      const t = Date.parse(frames[i].t);
      if (t < from) continue;
      if (t > to) break;
      const v = values[i];
      points.push({ t, v: typeof v === "number" && Number.isFinite(v) ? v : null, id: frames[i].id });
    }
    const valid = points.filter((p) => p.v != null);
    const missingHours = Math.max(0, Math.round((to - from) / HOUR) + 1 - valid.length);
    return { from, to, eventMs, points, valid,
             event: points.find((p) => p.t === eventMs) || null,
             before: valid.filter((p) => p.t < eventMs), after: valid.filter((p) => p.t > eventMs),
             missingHours };
  }

  const MIN_BEFORE = 3;

  // Plain description of the recorded values only: no thresholds, causes or forecasts.
  function describeEvidence(w, unit, fmt = (v) => String(+v.toFixed(2))) {
    if (!w) return "This check is after the selected time.";
    if (!w.valid.length) return "No readings were recorded in this window.";
    const u = unit ? ` ${unit}` : "";
    const parts = [];
    if (w.before.length >= MIN_BEFORE) {
      const first = w.before[0], ref = w.event && w.event.v != null ? w.event : w.before[w.before.length - 1];
      const hours = Math.round((ref.t - first.t) / HOUR);
      const d = ref.v - first.v;
      const lo = Math.min(...w.before.map((p) => p.v)), hi = Math.max(...w.before.map((p) => p.v));
      parts.push(`Over the ${hours} h before the check it went from ${fmt(first.v)} to ${fmt(ref.v)}${u} `
        + `(${d >= 0 ? "+" : "−"}${fmt(Math.abs(d))}); range before the check ${fmt(lo)}–${fmt(hi)}${u}.`);
    } else {
      parts.push(`Only ${w.before.length} reading${w.before.length === 1 ? "" : "s"} before the check: not enough to describe a change.`);
    }
    if (w.event && w.event.v == null) parts.push("No reading was recorded at the check hour.");
    if (w.after.length) {
      const last = w.after[w.after.length - 1];
      parts.push(`Latest reading up to the selected time: ${fmt(last.v)}${u}.`);
    }
    if (w.missingHours) parts.push(`${w.missingHours} hour${w.missingHours === 1 ? "" : "s"} in this window have no reading.`);
    return parts.join(" ");
  }

  return { isSignificant, indexAt, initial, step, statusAt, findCheck,
           evidenceSeries, evidenceWindow, describeEvidence };
});
