const test = require("node:test");
const assert = require("node:assert/strict");
const ME = require("../maint-events.js");

const at = (iso) => Date.parse(iso);
const SHEET = [
  { check_no: 1, opened_at: "2026-07-01T05:00:00Z", resolved_at: "2026-07-01T09:00:00Z", signal: "PREDICTED_MAINTENANCE", flag: null },
  { check_no: 2, opened_at: "2026-07-01T10:00:00Z", resolved_at: null, signal: "WARNING", flag: null },
  { check_no: 4, opened_at: "2026-07-02T00:00:00Z", resolved_at: null, signal: "MAINTENANCE_REQUIRED", flag: "COOLING_DEGRADED",
    measured_value: 47.0, unit: "% cooling health", recommended_action: "Clean the radiators." },
  { check_no: 5, opened_at: "2026-07-03T00:00:00Z", resolved_at: "2026-07-05T00:00:00Z", signal: "CRITICAL", flag: null },
  { check_no: 6, opened_at: "2026-07-04T00:00:00Z", resolved_at: null, signal: "PREDICTED_MAINTENANCE", flag: null },
];
const nos = (list) => list.map((c) => c.check_no);

test("initial load shows history but no toasts", () => {
  const r = ME.step(ME.initial(), SHEET, at("2026-07-02T12:00:00Z"));
  assert.deepEqual(r.toasts, []);
  assert.deepEqual(nos(r.history), [4, 2]);
});

test("playback forward toasts only significant checks it crosses", () => {
  let r = ME.step(ME.initial(), SHEET, at("2026-07-01T00:00:00Z"));
  r = ME.step(r.state, SHEET, at("2026-07-01T11:00:00Z"));
  assert.deepEqual(nos(r.toasts), [2]);
  r = ME.step(r.state, SHEET, at("2026-07-03T00:00:00Z"));
  assert.deepEqual(nos(r.toasts), [4, 5]);
});

test("history never includes checks opened after the playback time", () => {
  const r = ME.step(ME.initial(), SHEET, at("2026-07-02T23:59:00Z"));
  assert.ok(r.history.every((c) => at(c.opened_at) <= at("2026-07-02T23:59:00Z")));
  assert.ok(!nos(r.history).includes(5));
});

test("no duplicate toast when the same position is rendered again", () => {
  let r = ME.step(ME.initial(), SHEET, at("2026-07-01T00:00:00Z"));
  r = ME.step(r.state, SHEET, at("2026-07-02T01:00:00Z"));
  assert.deepEqual(nos(r.toasts), [2, 4]);
  r = ME.step(r.state, SHEET, at("2026-07-02T01:00:00Z"));
  assert.deepEqual(r.toasts, []);
  r = ME.step(r.state, SHEET, at("2026-07-02T02:00:00Z"));
  assert.deepEqual(r.toasts, []);
});

test("rewind retracts future toasts and replay shows them again once", () => {
  let r = ME.step(ME.initial(), SHEET, at("2026-07-01T00:00:00Z"));
  r = ME.step(r.state, SHEET, at("2026-07-03T01:00:00Z"));
  r = ME.step(r.state, SHEET, at("2026-07-01T12:00:00Z"));
  assert.deepEqual(r.retract.sort(), [4, 5]);
  assert.deepEqual(r.toasts, []);
  assert.deepEqual(nos(r.history), [2]);
  r = ME.step(r.state, SHEET, at("2026-07-03T01:00:00Z"));
  assert.deepEqual(nos(r.toasts), [4, 5]);
});

test("a long seek forward caps toasts and reports how many were skipped", () => {
  let r = ME.step(ME.initial(), SHEET, at("2026-07-01T00:00:00Z"));
  r = ME.step(r.state, SHEET, at("2026-07-10T00:00:00Z"), { maxToasts: 1 });
  assert.deepEqual(nos(r.toasts), [5]);
  assert.equal(r.suppressed, 2);
  assert.deepEqual(nos(r.history), [5, 4, 2]);
});

test("status is evaluated at playback time, not from the final record", () => {
  const c = ME.findCheck(SHEET, 5);
  assert.equal(ME.statusAt(c, at("2026-07-04T00:00:00Z")), "OPEN");
  assert.equal(ME.statusAt(c, at("2026-07-06T00:00:00Z")), "RESOLVED");
});

test("evidence links resolve to the exact recorded check", () => {
  const r = ME.step(ME.initial(), SHEET, at("2026-07-02T00:00:00Z"));
  const linked = ME.findCheck(SHEET, r.history[0].check_no);
  assert.equal(linked, SHEET[2]);
  assert.equal(linked.measured_value, 47.0);
  assert.equal(linked.recommended_action, "Clean the radiators.");
  assert.equal(ME.findCheck(SHEET, 999), null);
});

// ---- historical evidence -------------------------------------------------------------------------
const H = 3600e3;
const T0 = at("2026-07-18T00:00:00Z");
// 72 hourly frames from T0; hour 50 is missing entirely (gap), hour 10 has no value for IoT 4.
const FRAMES = [];
for (let h = 0; h < 72; h++) {
  if (h === 50) continue;
  FRAMES.push({ id: 1000 + h, t: new Date(T0 + h * H).toISOString(), v: [null, null, null, h === 10 ? null : h / 10, 80, 55, 20] });
}
const TL = {
  iots: [{ slot: 4, unit: "percent_deviation", display_name: "Voltage IoT" }, { slot: 6, unit: "degC", display_name: "Top-Oil Temperature IoT" }],
  frames: FRAMES,
  analytics: { top_oil: { hourly: { cooling_health: FRAMES.map((_, i) => 50 - i * 0.1) } } },
};
const EVENT = T0 + 48 * H;
const CHECK = { check_no: 37, iot: 4, unit: "percent_deviation", opened_at: new Date(EVENT).toISOString(), threshold: 5, comparison: ">=" };

test("evidence uses the IoT's own readings when the record unit matches", () => {
  const s = ME.evidenceSeries(TL, CHECK);
  assert.equal(s.kind, "sensor");
  assert.equal(s.values.length, FRAMES.length);
  assert.equal(s.values[FRAMES.findIndex((f) => Date.parse(f.t) === EVENT)], 4.8);
});

test("cooling checks use the derived cooling-health series; unmatched units get none", () => {
  assert.equal(ME.evidenceSeries(TL, { iot: 6, unit: "% cooling health" }).kind, "derived");
  assert.equal(ME.evidenceSeries(TL, { iot: 6, unit: "kPa" }), null);
  assert.equal(ME.evidenceSeries({ ...TL, analytics: {} }, { iot: 6, unit: "% cooling health" }), null);
});

test("evidence window never includes readings after the selected time", () => {
  const s = ME.evidenceSeries(TL, CHECK);
  const now = EVENT + 5 * H;
  const w = ME.evidenceWindow(FRAMES, s.values, EVENT, now);
  assert.ok(w.points.every((p) => p.t <= now));
  assert.equal(w.to, now);
  assert.equal(w.after.length, 4);                         // hour 50 has no frame
  assert.equal(w.points[0].t, EVENT - 48 * H);
});

test("evidence window is withheld while the check is in the future", () => {
  const s = ME.evidenceSeries(TL, CHECK);
  assert.equal(ME.evidenceWindow(FRAMES, s.values, EVENT, EVENT - H), null);
  assert.match(ME.describeEvidence(null, "x"), /after the selected time/);
});

test("event point is the reading at the check timestamp with its reading id", () => {
  const s = ME.evidenceSeries(TL, CHECK);
  const w = ME.evidenceWindow(FRAMES, s.values, EVENT, EVENT);
  assert.equal(w.event.t, EVENT);
  assert.equal(w.event.v, 4.8);
  assert.equal(w.event.id, 1048);
  assert.equal(w.after.length, 0);
});

test("missing values and missing hours are reported, not filled in", () => {
  const s = ME.evidenceSeries(TL, CHECK);
  const w = ME.evidenceWindow(FRAMES, s.values, EVENT, EVENT + 23 * H);
  assert.ok(w.points.some((p) => p.v === null));          // hour 10 has no value
  assert.ok(!w.points.some((p) => p.t === T0 + 50 * H));   // hour 50 has no frame
  assert.equal(w.missingHours, 2);
  assert.match(ME.describeEvidence(w, "u"), /2 hours in this window have no reading/);
});

test("description states recorded values only and admits insufficient history", () => {
  const s = ME.evidenceSeries(TL, CHECK);
  const w = ME.evidenceWindow(FRAMES, s.values, EVENT, EVENT);
  const text = ME.describeEvidence(w, "percent_deviation");
  assert.match(text, /went from 0 to 4\.8 percent_deviation \(\+4\.8\)/);
  assert.doesNotMatch(text, /threshold|limit|likely|predict|will/i);
  const early = ME.evidenceWindow(FRAMES, s.values, T0 + H, T0 + H);
  assert.match(ME.describeEvidence(early, "u"), /Only 1 reading before the check: not enough/);
  const empty = ME.evidenceWindow(FRAMES, FRAMES.map(() => null), EVENT, EVENT);
  assert.equal(ME.describeEvidence(empty, "u"), "No readings were recorded in this window.");
});

test("hours before the recorded history are not reported as missing", () => {
  const s = ME.evidenceSeries(TL, CHECK);
  const w = ME.evidenceWindow(FRAMES, s.values, T0 + 24 * H, T0 + 24 * H);
  assert.equal(w.from, T0);
  assert.equal(w.missingHours, 1);                          // only hour 10, not the 24 h before T0
});

// ---- playback on timebar indices (twin2 sheet: index = minute or day, resolved_index) ------------
const TB = [
  { check_no: 1, index: 3, resolved_index: 5, signal: "WARNING", flag: "OVERLOAD" },
  { check_no: 2, index: 3, resolved_index: null, signal: "CRITICAL", flag: "TRIP" },
  { check_no: 3, index: 9, resolved_index: 12, signal: "MAINTENANCE_REQUIRED", flag: "OIL_LEAK" },
];
const byIndex = { at: (c) => c.index };
const resolvedIdx = (c) => c.resolved_index;

test("a sheet can be played on timebar indices instead of timestamps", () => {
  let r = ME.step(ME.initial(), TB, 0, byIndex);
  assert.deepEqual(r.toasts, []);
  r = ME.step(r.state, TB, 3, byIndex);
  assert.deepEqual(nos(r.toasts), [1, 2]);
  r = ME.step(r.state, TB, 2, byIndex);
  assert.deepEqual(r.retract.sort(), [1, 2]);
  r = ME.step(r.state, TB, 9, byIndex);
  assert.deepEqual(nos(r.history), [3, 2, 1]);
  assert.equal(ME.indexAt(TB, 8, byIndex.at), 2);
});

test("index status resolves from the first index the resolution is known", () => {
  assert.equal(ME.statusAt(TB[0], 4, resolvedIdx), "OPEN");
  assert.equal(ME.statusAt(TB[0], 5, resolvedIdx), "RESOLVED");
  assert.equal(ME.statusAt(TB[1], 999, resolvedIdx), "OPEN");
});
