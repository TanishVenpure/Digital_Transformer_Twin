/*
 * Bridge between the 3D page and the Digital Transformer Twin API (this repo's FastAPI backend).
 *
 * The page keeps simulating readings. When the API answers on /api/v1/health, every POST_EVERY_S the
 * page posts the latest reading to POST /api/v1/data/reading. The backend stores it, runs its
 * registered rules and keeps any alerts in PostgreSQL. The rule results it returns replace the
 * page's own verdict for the matching modules:
 *
 *   3_phase_current_instrument_error  -> module 1, current measurement health
 *   3_phase_voltage_instrument_error  -> module 2, voltage measurement health
 *   oil_level_predictive_maintenance  -> module 5, oil level
 *
 * The other modules stay on the page's engine until the backend registers rules for them.
 *
 *   const api = TwinApi.create({ base: location.origin + '/' });
 *   api.post(simT, readings);      // throttled, fire and forget
 *   out = api.overlay(out);        // swap in the backend verdicts
 */
(function (root) {
  'use strict';
  const E = root.TwinEngine;
  const PH = [['A', 'r', 'R'], ['B', 'y', 'Y'], ['C', 'b', 'B']];
  const RULE = {
    current: '3_phase_current_instrument_error',
    voltage: '3_phase_voltage_instrument_error',
    oil: 'oil_level_predictive_maintenance',
  };
  const OIL_STATUS = {
    NORMAL: 'NORMAL',
    LOW_OIL_WARNING: 'WARNING',
    PREDICTED_MINIMUM_LEVEL_BREACH: 'PREDICTED_MAINTENANCE',
    MAINTENANCE_REQUIRED: 'MAINTENANCE_REQUIRED',
    OIL_LEVEL_CRITICAL: 'CRITICAL',
  };
  // Demo clock: one simulated second is stamped as one minute of transformer time, so the oil rule's
  // per-hour trend sees a leak develop over hours rather than seconds.
  const TIME_SCALE = 60;
  const POST_EVERY_S = 2;
  const STALE_MS = 10000;

  function create(opts) {
    const base = (opts && opts.base) || '/';
    const url = p => new URL(p, base).href;
    const st = {
      on: false, db: null, tid: (opts && opts.transformerId) || 'T01', t0: null, sim0: null,
      lastSim: -Infinity, busy: false, last: null, lastAt: 0, posts: 0, open: null, error: '',
    };

    const ready = (async () => {
      try {
        const h = await fetch(url('api/v1/health'), { cache: 'no-store' });
        const j = h.ok ? await h.json() : null;
        if (!j || j.status !== 'healthy') return false;
        st.on = true;
        const l = await fetch(url('api/v1/transformer/latest'), { cache: 'no-store' });
        if (l.status === 503) { st.db = false; st.error = 'database not configured'; return true; }
        if (!l.ok) { st.db = false; st.error = 'transformer/latest returned ' + l.status; return true; }
        const d = await l.json();
        st.db = true;
        if (d.transformer_id) st.tid = d.transformer_id;   // the API serves one transformer; keep its id
        const prev = d.latest_reading ? Date.parse(d.latest_reading.timestamp) : 0;
        st.t0 = Math.max(Date.now(), prev + 60000);        // never stamp before what is already stored
        return true;
      } catch (e) { return false; }
    })();

    function payload(r) {
      // The backend rules compare all three phases with one reference value. Each phase is sent as a
      // percentage of its own reference meter (reference = 100), so the backend's error per phase is
      // the same |measured - reference| / reference the page uses. The raw readings go along unchanged.
      const data = { rated_current: 100, reference_voltage: 100 };
      for (const [p, q] of PH) {
        data['measured_current_' + q] = 100 * r['I' + p] / r['I' + p + '_REF'];
        data['measured_voltage_' + q] = 100 * r['V' + p] / r['V' + p + '_REF'];
      }
      data.oil_level = r.OIL;
      for (const k of E.SENSORS.dynamic.concat(E.SENSORS.reference)) if (r[k] != null) data[k] = Math.round(r[k] * 1000) / 1000;
      for (const k in data) if (!isFinite(data[k])) delete data[k];
      return data;
    }

    function post(simT, r) {
      if (!st.on || !st.db || st.busy || simT - st.lastSim < POST_EVERY_S) return;
      if (st.sim0 == null) st.sim0 = simT;
      st.lastSim = simT; st.busy = true;
      const ts = new Date(st.t0 + (simT - st.sim0) * 1000 * TIME_SCALE).toISOString();
      fetch(url('api/v1/data/reading'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transformer_id: st.tid, timestamp: ts, data: payload(r) }),
      }).then(async res => {
        if (res.status === 503) { st.db = false; st.error = 'database unavailable'; return; }
        if (!res.ok) { st.error = 'reading rejected (' + res.status + ')'; return; }
        const j = await res.json();
        st.last = j.evaluations || {}; st.lastAt = Date.now(); st.error = '';
        if (st.posts++ % 5 === 0) refreshAlerts();
      }).catch(() => { st.error = 'API unreachable'; }).finally(() => { st.busy = false; });
    }

    function refreshAlerts() {
      fetch(url('api/v1/alerts?resolved=false&limit=1000'), { cache: 'no-store' })
        .then(r => r.ok ? r.json() : null).then(a => { if (Array.isArray(a)) st.open = a.length; }).catch(() => {});
    }

    const fresh = () => st.last && Date.now() - st.lastAt < STALE_MS;
    const used = res => res && (res.status === 'PASS' || res.status === 'FLAGGED');

    function measurement(m, res, kind) {
      if (!used(res)) {
        if (res) m.message += ' · API: ' + res.status.toLowerCase() + (res.reason ? ' (' + res.reason + ')' : '');
        return;
      }
      // FLAGGED means the worst phase is over the backend limit; any other phase over the same
      // limit (1 %, as in config/thresholds.yaml) is marked too.
      const errs = (res.details && res.details.phase_errors) || {};
      const flagged = res.status === 'FLAGGED', limit = E.DEFAULT_CONFIG.sensor_health[kind === 'I' ? 'current_err_pct' : 'voltage_err_pct'], inputs = {};
      for (const [p, , rp] of PH) {
        const e = errs[rp];
        if (e == null) continue;
        m.metrics['e_' + p.toLowerCase() + '_pct'] = Math.round(e * 100) / 100;
        inputs[kind + p] = flagged && (rp === res.details.max_deviation_phase || e > limit) ? 'MAINTENANCE_REQUIRED' : 'NORMAL';
      }
      m.inputs = inputs;
      m.metrics.e_max_pct = Math.round(res.value * 100) / 100;
      m.status = flagged ? 'MAINTENANCE_REQUIRED' : 'NORMAL';
      m.maintenance_required = flagged;
      const ph = res.details.max_deviation_phase;
      m.message = (flagged ? (kind === 'I' ? 'CT' : 'Voltage sensor') + ' phase ' + ph + ' error ' + res.value.toFixed(2) + '%: maintenance required'
        : 'Max phase error ' + res.value.toFixed(2) + '% (phase ' + ph + ')') + ' · checked by API';
      m.source = 'api';
    }

    function oil(m, res) {
      if (!used(res)) {
        if (res) m.message += ' · API: ' + res.status.toLowerCase() + (res.reason ? ' (' + res.reason + ')' : '');
        return;
      }
      const d = res.details || {}, outs = d.outputs || ['NORMAL'];
      m.status = E.worst(outs.map(o => OIL_STATUS[o] || 'NORMAL'));
      m.maintenance_required = !!d.maintenance_required;
      m.inputs = { OIL: m.status };
      m.predicted_breach_s = null;
      let msg = 'Oil ' + Number(d.current_oil_level).toFixed(1) + '% (minimum ' + d.minimum_oil_level + '%)';
      if (d.trend_direction === 'declining' && d.estimated_t_min != null && d.estimated_t_min >= 0)
        msg += ', reaches minimum in ' + d.estimated_t_min.toFixed(1) + ' h';
      m.message = msg + ' · checked by API';
      m.source = 'api';
    }

    function overlay(out) {
      if (!fresh()) return out;
      const by = Object.fromEntries(out.modules.map(m => [m.id, m]));
      measurement(by.current_sensor_health, st.last[RULE.current], 'I');
      measurement(by.voltage_sensor_health, st.last[RULE.voltage], 'V');
      oil(by.oil_level, st.last[RULE.oil]);
      const s = out.sensors;
      for (const [p] of PH) {
        s['I' + p] = E.worst([by.current.inputs['I' + p] || 'NORMAL', by.current_sensor_health.inputs['I' + p] || 'NORMAL']);
        s['V' + p] = E.worst([by.voltage.inputs['V' + p] || 'NORMAL', by.voltage_sensor_health.inputs['V' + p] || 'NORMAL']);
      }
      s.OIL = by.oil_level.status;
      s.REF = E.worst([by.current_sensor_health.status, by.voltage_sensor_health.status]);
      out.health = E.healthEngine(out.modules);
      return out;
    }

    function label() {
      if (!st.on) return '';
      if (st.db === false || st.error) return 'API: ' + (st.error || 'not ready') + ', page rules only';
      if (!fresh()) return 'API connected';
      return 'API: 3 rules live' + (st.open != null ? ', ' + st.open + ' open alert' + (st.open === 1 ? '' : 's') : '');
    }

    return { ready, post, overlay, label, state: st, payload };
  }

  root.TwinApi = { create, RULE, TIME_SCALE };
})(typeof window !== 'undefined' ? window : globalThis);
