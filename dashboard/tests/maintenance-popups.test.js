const test = require('node:test');
const assert = require('node:assert/strict');
const { create } = require('../maintenance-popups.js');
class Element {
  constructor(doc) { this.ownerDocument = doc; this.children = []; this.dataset = {};
    this.style = { setProperty() {} }; this.classList = { add() {} }; this.textContent = ''; }
  setAttribute() {}
  appendChild(el) { el.parent = this; this.children.push(el); }
  prepend(el) { el.parent = this; this.children.unshift(el); }
  get lastChild() { return this.children.at(-1); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(x => x !== this); }
}
const checks = [
  { check_no: 1, timebar: 1, opened_at: '2026-07-15T10:21:01Z', time_utc: '10:21:01',
    date: '2026-07-15', signal: 'CRITICAL', iot: 'loading', iot_name: 'Loading',
    flag: 'OVERLOAD_TRIP', recommended_action: 'Recorded trip alert.' },
  { check_no: 2, timebar: 2, opened_at: '2026-07-03T00:00:00Z', date: '2026-07-03',
    signal: 'WARNING', iot: 'oil', iot_name: 'Oil', flag: 'OIL_LEAK', recommended_action: 'Recorded oil alert.' }];
async function setup() {
  const doc = { createElement: () => new Element(doc) };
  const container = new Element(doc), timers = [], clicked = [];
  const api = create({ container, openCheck: (...args) => clicked.push(args),
    schedule: (fn) => timers.push(fn), fetcher: async (url) => {
      assert.equal(url, '/api/twin2/sheet'); return { ok: true, json: async () => ({ checks }) }; } });
  await api.ready;
  api.update({ until: '2026-07-15T10:20:00Z', until2: '2026-07-01' });
  return { api, container, timers, clicked };
}
const cursor = (until, until2 = '2026-07-01') => ({ until, until2 });

test('popups use exact opening time, both cursors, and avoid duplicates', async () => {
  const { api, container } = await setup();
  api.update(cursor('2026-07-15T10:21:00Z'));
  assert.equal(container.children.length, 0); // 10:21:01 is still future
  api.update(cursor('2026-07-15T10:22:00Z'));
  assert.equal(container.children.length, 1);
  assert.equal(container.children[0].dataset.no, 1);
  api.update(cursor('2026-07-15T10:22:00Z', '2026-07-03'));
  assert.equal(container.children.length, 2);
  api.update(cursor('2026-07-15T10:22:00Z', '2026-07-03'));
  assert.equal(container.children.length, 2);
});

test('rewind retracts, replay restores, stale timeout cannot remove replayed toast', async () => {
  const { api, container, timers } = await setup();
  api.update(cursor('2026-07-15T10:22:00Z'));
  api.update(cursor('2026-07-15T10:20:00Z'));
  assert.equal(container.children.length, 0);
  api.update(cursor('2026-07-15T10:22:00Z'));
  timers[0]();
  assert.equal(container.children.length, 1);
  assert.equal(timers.length, 2);
});

test('click opens recorded monitor; dismiss removes popup without opening graph', async () => {
  const { api, container, clicked } = await setup();
  api.update(cursor('2026-07-15T10:22:00Z'));
  container.children[0].onclick();
  assert.deepEqual(clicked, [[1, 'loading']]);
  assert.equal(container.children.length, 0);
  api.update(cursor('2026-07-15T10:20:00Z'));
  api.update(cursor('2026-07-15T10:22:00Z'));
  const dismiss = container.children[0].children[0].children.at(-1);
  dismiss.onclick({ stopPropagation() {} });
  assert.equal(container.children.length, 0);
  assert.equal(clicked.length, 1);
});

test('failed event API visibly reports popup unavailability', async () => {
  const doc = { createElement: () => new Element(doc) }, container = new Element(doc);
  const api = create({ container, openCheck() {}, fetcher: async () => ({ ok: false }) });
  await api.ready;
  assert.match(container.textContent, /unavailable/);
});
