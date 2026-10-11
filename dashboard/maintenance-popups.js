// Recorded maintenance popups. This displays existing events; it never evaluates rules.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(require("./maint-events.js"));
  else root.MaintenancePopups = factory(root.MaintEvents);
})(typeof self !== "undefined" ? self : this, (ME) => {
  const COLORS = { WARNING: "var(--warning)", CRITICAL: "var(--critical)",
    MAINTENANCE_REQUIRED: "var(--maintenance)", PREDICTED_MAINTENANCE: "var(--predicted)" };
  function create({ container, openCheck, fetcher = (...args) => fetch(...args),
                    schedule = setTimeout, maxVisible = 4 }) {
    const doc = container.ownerDocument;
    let cursor = null;
    const sheets = { 1: [], 2: [] }, states = { 1: null, 2: null };
    const nodes = new Map();
    const position = (tb) => cursor ? Date.parse(tb === 1 ? cursor.until : cursor.until2 + 'T00:00:00Z') : NaN;
    function retract(no) {
      const node = nodes.get(no);
      if (node) { node.remove(); nodes.delete(no); }
    }
    function show(c) {
      const el = doc.createElement('div'); el.className = 'maint-toast'; el.dataset.no = c.check_no;
      el.setAttribute('role', 'button'); el.tabIndex = 0;
      el.style.setProperty('--c', COLORS[c.signal] || 'var(--accent)');
      function text(parent, tag, cls, value) {
        const node = doc.createElement(tag); node.className = cls; node.textContent = value;
        parent.appendChild(node); return node;
      }
      const head = text(el, 'div', 'toast-head', '');
      text(head, 'span', 'toast-signal', c.signal.replaceAll('_', ' '));
      text(head, 'span', 'toast-title', '#' + c.check_no + ' ' + c.iot_name);
      const dismiss = text(head, 'button', 'toast-dismiss', '×');
      dismiss.setAttribute('aria-label', 'Dismiss alert');
      dismiss.onclick = (e) => { e.stopPropagation(); retract(c.check_no); };
      const when = c.timebar === 1 ? c.time_utc + ' UTC' : c.date;
      const body = text(el, 'div', 'toast-body', 'Timebar ' + c.timebar + ' · ' + when + ' · ' + c.flag.replaceAll('_', ' ').toLowerCase());
      text(body, 'div', 'action-line', c.recommended_action || '');
      el.onclick = () => { openCheck(c.timebar, c.iot); retract(c.check_no); };
      el.onkeydown = (e) => { if (e.target === el && (e.key === 'Enter' || e.key === ' ')) {
        e.preventDefault(); el.onclick(); } };
      container.prepend(el); nodes.set(c.check_no, el);
      while (container.children.length > maxVisible) {
        const last = container.lastChild; retract(Number(last.dataset.no));
      }
      schedule(() => {
        // An old timeout must not dismiss a new toast after rewind/replay.
        if (nodes.get(c.check_no) !== el) return;
        el.classList.add('fade-out');
        schedule(() => { if (nodes.get(c.check_no) === el) retract(c.check_no); }, 300);
      }, 12000);
    }
    function update(next) {
      cursor = next;
      for (const tb of [1, 2]) {
        const pos = position(tb);
        if (!states[tb] || !Number.isFinite(pos)) continue;
        const r = ME.step(states[tb], sheets[tb], pos, { maxToasts: 2 });
        states[tb] = r.state; r.retract.forEach(retract); r.toasts.forEach(show);
      }
    }
    const ready = fetcher('/api/twin2/sheet').then((r) => {
      if (!r.ok) throw new Error('Recorded maintenance alerts unavailable');
      return r.json();
    }).then((data) => {
      for (const tb of [1, 2]) {
        sheets[tb] = data.checks.filter((c) => c.timebar === tb)
          .sort((a, b) => Date.parse(a.opened_at) - Date.parse(b.opened_at) || a.check_no - b.check_no);
        const pos = position(tb);
        states[tb] = Number.isFinite(pos) ? ME.step(ME.initial(), sheets[tb], pos).state : ME.initial();
      }
    }).catch(() => { container.textContent = 'Recorded alert popups unavailable. Reload to retry.'; });
    return { update, ready };
  }
  return { create };
});
