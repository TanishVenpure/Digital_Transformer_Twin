/*
 * Transformer IoT engine: 7 monitoring modules + a rule-based Health Engine.
 * Mirrors backend/app/engine.py line for line, so the page and the server reach
 * the same verdict. The page runs this only when no backend is connected.
 *
 *   const eng = TwinEngine.create();          // optional config overrides
 *   const out = eng.update(tsSeconds, readings);
 *   out.modules  -> 7 standard outputs { id, name, status, maintenance_required, metrics, predicted_breach_s, inputs, message }
 *   out.sensors  -> per-sensor status (for colouring the 3D model)
 *   out.health   -> { score, priority, priority_label, action, drivers }
 *
 * Status values, least to most severe:
 *   NORMAL < PREDICTED_MAINTENANCE < WARNING < MAINTENANCE_REQUIRED < CRITICAL
 */
(function (root) {
  'use strict';
  const STATUSES = ['NORMAL', 'PREDICTED_MAINTENANCE', 'WARNING', 'MAINTENANCE_REQUIRED', 'CRITICAL'];
  const rank = s => STATUSES.indexOf(s);
  const worst = list => list.reduce((a, b) => (rank(b) > rank(a) ? b : a), 'NORMAL');

  const DEFAULT_CONFIG = {
    transformer: { kva: 630, hv_kv: 11, lv_kv: 0.433, i_rated_a: 841, v_nominal_v: 250 },
    current: { warn_pct: 90, crit_pct: 100, min_slope: 1.0 },            // % of rated; A/s
    voltage: { warn_dev_pct: 5, crit_dev_pct: 10, min_slope: 0.05 },      // % deviation; %/s
    oil: { warn_pct: 35, min_pct: 20, min_slope: 0.05 },                  // % level; %/s
    top_oil: { alarm_c: 95, trip_c: 105, max_rise_c: 40, min_slope: 0.05 },
    winding: { max_rise_c: 45, warn_frac: 0.9, min_slope: 0.05 },
    sensor_health: { current_err_pct: 1.0, voltage_err_pct: 1.0 },       // instrument accuracy limits
    maintenance_lead_time_s: 1800,
    history_window_s: 30,
    deadband: 0.02,                                                      // 2 % before an alert clears
  };
  const SENSORS = {
    dynamic: ['IA', 'IB', 'IC', 'VA', 'VB', 'VC', 'OIL', 'TOP_OIL', 'TW_A', 'TW_B', 'TW_C', 'AMB'],
    reference: ['IA_REF', 'IB_REF', 'IC_REF', 'VA_REF', 'VB_REF', 'VC_REF'],
  };
  const PH = ['A', 'B', 'C'];
  const WEIGHTS = { winding: 1.0, top_oil: 0.9, oil_level: 0.9, current: 0.8, voltage: 0.6, current_sensor_health: 0.4, voltage_sensor_health: 0.4 };
  const PENALTY = { NORMAL: 0, PREDICTED_MAINTENANCE: 12, WARNING: 25, MAINTENANCE_REQUIRED: 30, CRITICAL: 50 };
  const ACTIONS = {
    current_sensor_health: 'Recalibrate or replace the current transformer (CT)',
    voltage_sensor_health: 'Recalibrate or replace the voltage sensor',
    current: { CRITICAL: 'Reduce load below rated current now', WARNING: 'Plan a load transfer; loading is high', PREDICTED_MAINTENANCE: 'Schedule a load review before the predicted overload' },
    voltage: { CRITICAL: 'Check tap position and supply; inform the utility', WARNING: 'Check tap position and supply voltage', PREDICTED_MAINTENANCE: 'Schedule a tap / supply check before the predicted breach' },
    oil_level: { CRITICAL: 'Find the leak and top up oil; consider de-energising', WARNING: 'Inspect for leaks and plan an oil top-up', PREDICTED_MAINTENANCE: 'Schedule an oil top-up and leak inspection' },
    top_oil: { CRITICAL: 'Trip level reached: reduce load or de-energise', WARNING: 'Reduce load and check radiators and cooling', PREDICTED_MAINTENANCE: 'Schedule a cooling check before the predicted breach' },
    winding: { CRITICAL: 'Winding rise above limit: reduce load now', WARNING: 'Reduce load; winding temperature rise is high', PREDICTED_MAINTENANCE: 'Schedule load reduction before the predicted breach' },
  };

  function merge(a, b) {
    const o = Array.isArray(a) ? a.slice() : Object.assign({}, a);
    for (const k in b || {}) o[k] = b[k] && typeof b[k] === 'object' && !Array.isArray(b[k]) ? merge(a[k] || {}, b[k]) : b[k];
    return o;
  }

  // High-is-bad grading with hysteresis: a level only drops once x is `db` back inside its limit.
  function grade(x, warn, crit, prev, db, hiStatus) {
    const lvl = (w, c) => (x > c ? 2 : x > w ? 1 : 0);
    let L = lvl(warn, crit);
    const P = prev === 'CRITICAL' || prev === hiStatus ? 2 : prev === 'WARNING' ? 1 : 0;
    if (L < P) {
      const shrink = v => v - Math.abs(v) * db;
      L = Math.max(L, Math.min(P, lvl(shrink(warn), shrink(crit))));
    }
    return [L === 2 ? (hiStatus || 'CRITICAL') : L === 1 ? 'WARNING' : 'NORMAL', L];
  }

  function slope(series, now, window) {
    const pts = series.filter(p => p[0] >= now - window);
    if (pts.length < 5) return 0;
    const n = pts.length, mt = pts.reduce((s, p) => s + p[0], 0) / n, mv = pts.reduce((s, p) => s + p[1], 0) / n;
    let num = 0, den = 0;
    for (const [t, v] of pts) { num += (t - mt) * (v - mv); den += (t - mt) * (t - mt); }
    return den ? num / den : 0;
  }

  function create(overrides) {
    const cfg = merge(DEFAULT_CONFIG, overrides || {});
    const hist = {};
    let prev = {};
    const push = (k, t, v) => { (hist[k] = hist[k] || []).push([t, v]); while (hist[k].length && hist[k][0][0] < t - cfg.history_window_s * 2) hist[k].shift(); };
    const sl = (k, t) => slope(hist[k] || [], t, cfg.history_window_s);
    const db = cfg.deadband, lead = cfg.maintenance_lead_time_s;
    const r2 = x => (x == null || !isFinite(x) ? null : Math.round(x * 100) / 100);

    // add a prediction to a module that is otherwise NORMAL
    function predict(m, t) {
      if (t != null && t > 0 && isFinite(t)) {
        m.predicted_breach_s = Math.round(t);
        if (m.status === 'NORMAL' && t <= lead) m.status = 'PREDICTED_MAINTENANCE';
      }
    }

    function sensorHealth(id, name, kind, r, limit) {
      const inputs = {}, metrics = {};
      let eMax = 0, worstPh = null;
      for (const p of PH) {
        const m = r[kind + p], ref = r[kind + p + '_REF'];
        if (m == null || ref == null || !ref) continue;
        const e = Math.abs(m - ref) / Math.abs(ref) * 100;
        metrics['e_' + p.toLowerCase() + '_pct'] = r2(e);
        const pk = id + ':' + p;
        inputs[kind + p] = grade(e, limit, limit, prev[pk], db, 'MAINTENANCE_REQUIRED')[0] === 'NORMAL' ? 'NORMAL' : 'MAINTENANCE_REQUIRED';
        prev[pk] = inputs[kind + p];
        if (e > eMax) { eMax = e; worstPh = p; }
      }
      metrics.e_max_pct = r2(eMax);
      const status = worst(Object.values(inputs));
      return {
        id, name, status, maintenance_required: status === 'MAINTENANCE_REQUIRED', metrics, predicted_breach_s: null, inputs,
        message: status === 'NORMAL' ? `${kind === 'I' ? 'Current' : 'Voltage'} sensors healthy (max error ${eMax.toFixed(2)}% ≤ ${limit}%)`
          : `${kind === 'I' ? 'CT' : 'Voltage sensor'} phase ${worstPh} error ${eMax.toFixed(2)}% > ${limit}%: maintenance required`,
      };
    }

    function update(t, r) {
      const T = cfg.transformer, mods = [];

      // 1, 2: measurement health
      mods.push(sensorHealth('current_sensor_health', 'Current measurement health', 'I', r, cfg.sensor_health.current_err_pct));
      mods.push(sensorHealth('voltage_sensor_health', 'Voltage measurement health', 'V', r, cfg.sensor_health.voltage_err_pct));

      // 3: current / loading
      {
        const c = cfg.current, I = PH.map(p => r['I' + p]).filter(v => v != null);
        const iMax = Math.max(...I), load = iMax / T.i_rated_a * 100;
        push('I_max', t, iMax);
        const inputs = {};
        for (const p of PH) if (r['I' + p] != null) {
          const k = 'current:' + p; inputs['I' + p] = grade(r['I' + p] / T.i_rated_a * 100, c.warn_pct, c.crit_pct, prev[k], db)[0]; prev[k] = inputs['I' + p];
        }
        const m = { id: 'current', name: 'Current IoT', status: worst(Object.values(inputs)), metrics: { i_max_a: r2(iMax), loading_pct: r2(load) }, predicted_breach_s: null, inputs };
        const s = sl('I_max', t); m.metrics.di_dt_a_per_s = r2(s);
        if (s > c.min_slope && load < c.crit_pct) predict(m, (c.crit_pct / 100 * T.i_rated_a - iMax) / s);
        m.maintenance_required = m.status === 'CRITICAL';
        m.message = `Loading ${load.toFixed(1)}% of ${T.i_rated_a} A (max phase ${iMax.toFixed(0)} A)`;
        mods.push(m);
      }

      // 4: voltage deviation
      {
        const c = cfg.voltage, inputs = {};
        let dMax = 0;
        for (const p of PH) if (r['V' + p] != null) {
          const d = Math.abs(r['V' + p] - T.v_nominal_v) / T.v_nominal_v * 100, k = 'voltage:' + p;
          inputs['V' + p] = grade(d, c.warn_dev_pct, c.crit_dev_pct, prev[k], db)[0]; prev[k] = inputs['V' + p];
          dMax = Math.max(dMax, d);
        }
        push('D_max', t, dMax);
        const m = { id: 'voltage', name: 'Voltage IoT', status: worst(Object.values(inputs)), metrics: { d_max_pct: r2(dMax) }, predicted_breach_s: null, inputs };
        const s = sl('D_max', t); m.metrics.dd_dt_pct_per_s = r2(s);
        if (s > c.min_slope && dMax < c.crit_dev_pct) predict(m, (c.crit_dev_pct - dMax) / s);
        m.maintenance_required = m.status === 'CRITICAL';
        m.message = `Max deviation ${dMax.toFixed(2)}% from ${T.v_nominal_v} V`;
        mods.push(m);
      }

      // 5: oil level (low is bad)
      {
        const c = cfg.oil, O = r.OIL;
        push('OIL', t, O);
        const [st] = grade(-O, -c.warn_pct, -c.min_pct, prev.oil, db);
        prev.oil = st;
        const m = { id: 'oil_level', name: 'Oil-level IoT', status: st, metrics: { oil_pct: r2(O) }, predicted_breach_s: null, inputs: { OIL: st } };
        const s = sl('OIL', t); m.metrics.do_dt_pct_per_s = r2(s);
        if (s < -c.min_slope && O > c.min_pct) predict(m, (O - c.min_pct) / Math.abs(s));
        m.metrics.t_min_s = m.predicted_breach_s;
        m.inputs.OIL = m.status;
        m.maintenance_required = m.status === 'CRITICAL';
        m.message = `Oil ${O.toFixed(1)}% (minimum ${c.min_pct}%)` + (m.predicted_breach_s ? `, reaches minimum in ${fmtDur(m.predicted_breach_s)}` : '');
        mods.push(m);
      }

      // 6: top-oil temperature
      {
        const c = cfg.top_oil, To = r.TOP_OIL, Ta = r.AMB, rise = To - Ta;
        push('TOP_OIL', t, To);
        const a = grade(To, c.alarm_c, c.trip_c, prev.top_oil_abs, db)[0];
        const b = grade(rise, c.max_rise_c, Infinity, prev.top_oil_rise, db)[0];
        prev.top_oil_abs = a; prev.top_oil_rise = b;
        const m = { id: 'top_oil', name: 'Top-oil temperature IoT', status: worst([a, b]), metrics: { top_oil_c: r2(To), ambient_c: r2(Ta), rise_c: r2(rise) }, predicted_breach_s: null, inputs: {} };
        const s = sl('TOP_OIL', t); m.metrics.dt_dt_c_per_s = r2(s);
        if (s > c.min_slope && To < c.alarm_c) predict(m, (c.alarm_c - To) / s);
        m.inputs.TOP_OIL = m.status;
        m.maintenance_required = m.status === 'CRITICAL';
        m.message = `Top oil ${To.toFixed(1)} °C, rise ${rise.toFixed(1)} °C over ambient (limit ${c.max_rise_c} °C)`;
        mods.push(m);
      }

      // 7: winding temperature
      {
        const c = cfg.winding, Ta = r.AMB, inputs = {};
        const Tw = PH.map(p => r['TW_' + p]).filter(v => v != null), Th = Math.max(...Tw), rise = Th - Ta;
        push('T_hot', t, Th);
        for (const p of PH) if (r['TW_' + p] != null) {
          const k = 'winding:' + p; inputs['TW_' + p] = grade(r['TW_' + p] - Ta, c.warn_frac * c.max_rise_c, c.max_rise_c, prev[k], db)[0]; prev[k] = inputs['TW_' + p];
        }
        const m = { id: 'winding', name: 'Winding temperature IoT', status: worst(Object.values(inputs)), metrics: { t_hot_c: r2(Th), ambient_c: r2(Ta), rise_c: r2(rise) }, predicted_breach_s: null, inputs };
        const s = sl('T_hot', t); m.metrics.dt_dt_c_per_s = r2(s);
        if (s > c.min_slope && rise < c.max_rise_c) predict(m, (c.max_rise_c - rise) / s);
        m.maintenance_required = m.status === 'CRITICAL';
        m.message = `Hottest winding ${Th.toFixed(1)} °C, rise ${rise.toFixed(1)} °C (limit ${c.max_rise_c} °C)`;
        mods.push(m);
      }

      for (const m of mods) m.ts = t;

      // per-sensor status for the 3D model
      const by = Object.fromEntries(mods.map(m => [m.id, m]));
      const sensors = {};
      for (const p of PH) {
        sensors['I' + p] = worst([by.current.inputs['I' + p] || 'NORMAL', by.current_sensor_health.inputs['I' + p] || 'NORMAL']);
        sensors['V' + p] = worst([by.voltage.inputs['V' + p] || 'NORMAL', by.voltage_sensor_health.inputs['V' + p] || 'NORMAL']);
        sensors['TW_' + p] = by.winding.inputs['TW_' + p] || 'NORMAL';
      }
      sensors.OIL = by.oil_level.status;
      sensors.TOP_OIL = by.top_oil.status;
      sensors.AMB = 'NORMAL';
      sensors.REF = worst([by.current_sensor_health.status, by.voltage_sensor_health.status]);

      return { ts: t, modules: mods, sensors, health: healthEngine(mods) };
    }
    return { config: cfg, update };
  }

  // Rule-based placeholder Health Engine. Replace with your ML model: same input (the 7 module outputs), same output shape.
  function healthEngine(mods) {
    let pen = 0;
    for (const m of mods) pen += (WEIGHTS[m.id] || 0.5) * PENALTY[m.status];
    const score = Math.max(0, Math.round(100 - pen));
    const ranked = mods.filter(m => m.status !== 'NORMAL')
      .sort((a, b) => rank(b.status) - rank(a.status) || (WEIGHTS[b.id] || 0) - (WEIGHTS[a.id] || 0));
    const top = ranked[0];
    const w = worst(mods.map(m => m.status));
    const priority = w === 'CRITICAL' ? 'P1' : (w === 'MAINTENANCE_REQUIRED' || w === 'WARNING') ? 'P2' : w === 'PREDICTED_MAINTENANCE' ? 'P3' : 'P4';
    const label = { P1: 'Immediate', P2: 'Within 24 h', P3: 'Schedule', P4: 'Routine' }[priority];
    let action = 'No action needed. Continue routine monitoring.';
    if (top) { const a = ACTIONS[top.id]; action = typeof a === 'string' ? a : a[top.status] || a.WARNING; }
    return { score, priority, priority_label: label, action, drivers: ranked.map(m => m.id) };
  }

  function fmtDur(s) {
    if (s == null) return '';
    if (s < 90) return Math.round(s) + ' s';
    if (s < 5400) return Math.round(s / 60) + ' min';
    return (s / 3600).toFixed(1) + ' h';
  }

  root.TwinEngine = { create, healthEngine, STATUSES, SENSORS, DEFAULT_CONFIG, rank, worst, fmtDur };
})(typeof window !== 'undefined' ? window : globalThis);
