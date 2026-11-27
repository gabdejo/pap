// web/apps/dashboards/app/benchmarks/registro/BenchmarkEditor.js
// ---------------------------------------------------------------------------
// The composite indices (benchmarks, targets): which exist, and the editor
// of their VERSIONED definition - the Registry view of the Benchmarks module.
//
// A benchmark is a portfolio row with a level series; what it is made of
// lives in append-only versions (dim_benchmark_version). This editor never
// edits or deletes a version: every change - new weights, a swapped ticker,
// a correction - is a NEW version with its effective date, a change type
// and a reason that cannot be blank. The history on the right is the
// changelog view, read back from the database, not something kept here.
//
// Saving a version also materializes the benchmark (levels, valuation and
// holdings) in the shared background task; the component is handed the
// task hook by the page that mounts it. Constituents come from ONE catalog:
// the pipeline's priced series (series_registry / fact_prices), minus the
// model series themselves. A Bloomberg ticker the pipelines do not carry
// yet is registered from here too, in a folded block under the search.
//
// Rebalancing is MANUAL: the weights a version declares are struck at its
// effective date and hold until the operator declares the next version -
// that new version IS the rebalance. In the analytics module this is the
// buy_and_hold method; fixed_weights (periodic re-strike) is not offered
// here, though a version carrying it still renders in the history.
// ---------------------------------------------------------------------------
'use client';

import { useEffect, useState } from 'react';

import Echo from '../../../components/Echo';
import Log from '../../../components/Log';
import SppSeg from '../../../components/SppSeg';
import useShowAll from '../../../components/useShowAll';
import { apiGet, apiSend } from '../../../lib/api';
import { apiUrl, fDate, fTime, nInt, todayLocal } from '../../../lib/spp';

const METHOD = 'buy_and_hold';
// For reading the history: a version may carry a method or a frequency
// this editor no longer offers.
const METHOD_LABELS = {
  buy_and_hold: 'Manual rebalancing', fixed_weights: 'Fixed weights (periodic rebalancing)', heuristic: 'Heuristic',
};
const FREQUENCY_LABELS = { daily: 'daily', monthly: 'monthly', quarterly: 'quarterly', annual: 'annual' };
const ENTITY_TYPES = { security: 'Security (stock, ETF, bond)', index: 'Index', fund: 'Fund' };
const STATUS = {
  active: 'active', 'backfill-pending': 'history pending', suspended: 'suspended',
  inactive: 'inactive', 'error-hold': 'on error hold',
};
const CHANGE_TYPES = {
  initial: 'Initial version', rebalance: 'Rebalance', methodology: 'Methodology change',
  constituent_change: 'Constituent change', correction: 'Correction',
};
const CHANGE_MARK = { added: '+ new', dropped: '− out', reweighted: '≠ weight', unchanged: '' };

// Small key/value report table, the same one the rest of the view uses.
function Report({ rows }) {
  return (
    <table className="spp-report"><tbody>
      {rows.map(([a, b], i) => (
        <tr key={i}><td>{a}</td><td className="num">{b}</td></tr>
      ))}
    </tbody></table>
  );
}

const pctOf = (w) => `${(w * 100).toLocaleString('es-PE', { maximumFractionDigits: 2 })} %`;

export default function BenchmarkEditor({ busy, task, start, onTaskDone }) {
  // ---- which benchmarks exist ----
  const [list, setList] = useState(null);
  const [selected, setSelected] = useState('');
  const loadList = () => apiGet('/api/benchmarks')
    .then((j) => {
      const items = j.benchmarks || [];
      setList(items);
      setSelected((s) => (s && items.some((b) => b.procode === s)) ? s : (items[0]?.procode || ''));
    })
    .catch(() => setList([]));
  const current = (list || []).find((b) => b.procode === selected) || null;

  // ---- create / rename, and the house fund it is the benchmark of ----
  const [newProcode, setNewProcode] = useState('');
  const [newName, setNewName] = useState('');
  const [newFund, setNewFund] = useState(null);
  const [funds, setFunds] = useState([0, 1, 2, 3]);
  const [houseName, setHouseName] = useState('');
  const [createEcho, setCreateEcho] = useState('');

  // The fund follows the code: an existing benchmark brings its own, a
  // new code ending in _F{n} proposes n; otherwise the operator picks.
  useEffect(() => {
    const code = newProcode.trim().toUpperCase();
    const existing = (list || []).find((b) => b.procode === code);
    if (existing && existing.fund != null) { setNewFund(existing.fund); return; }
    const m = /_F(\d)$/.exec(code);
    if (m) setNewFund(Number(m[1]));
  }, [newProcode, list]);

  const createBenchmark = async () => {
    setCreateEcho('');
    const r = await apiSend('/api/benchmarks', 'POST',
      { procode: newProcode.trim().toUpperCase(), display_name: newName.trim(), fund: newFund });
    if (!r.ok) { setCreateEcho({ ok: false, text: r.data.detail }); return; }
    setCreateEcho({ ok: true, text: `${r.data.procode}: "${r.data.display_name}" · Fund ${r.data.fund}.` });
    setNewProcode(''); setNewName(''); setNewFund(null);
    await loadList();
    setSelected(r.data.procode);
  };

  // ---- versions of the selected benchmark ----
  const [versions, setVersions] = useState([]);
  const [changelog, setChangelog] = useState([]);
  const loadVersions = (procode) => {
    if (!procode) { setVersions([]); setChangelog([]); return; }
    const p = encodeURIComponent(procode);
    apiGet(`/api/benchmarks/${p}/versions`).then((j) => setVersions(j.versions || [])).catch(() => setVersions([]));
    apiGet(`/api/benchmarks/${p}/changelog`).then((j) => setChangelog(j.changelog || [])).catch(() => setChangelog([]));
  };
  const latest = versions.length ? versions[versions.length - 1] : null;

  // ---- the new version form ----
  const [effectiveFrom, setEffectiveFrom] = useState('');
  const [changeType, setChangeType] = useState('rebalance');
  const [reason, setReason] = useState('');
  const [supportDoc, setSupportDoc] = useState('');
  const [recordedBy, setRecordedBy] = useState('');
  const [constituents, setConstituents] = useState([]);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState(null);
  const [fxCatalog, setFxCatalog] = useState([]);
  const [saveEcho, setSaveEcho] = useState('');

  useEffect(() => {
    setEffectiveFrom(todayLocal());
    // A ten-year history is the usual starting point for a new ticker.
    setTStart(`${Number(todayLocal().slice(0, 4)) - 10}-01-01`);
    loadList();
    // A small catalog, always loaded, for the FX selectors.
    apiGet('/api/benchmarks/series?limit=200').then((j) => setFxCatalog(j.series || [])).catch(() => {});
    // The house and its fund types, for the assignment.
    apiGet('/api/spp/config').then((c) => {
      if (c.funds?.length) setFunds(c.funds);
      setHouseName(c.house || '');
    }).catch(() => {});
  }, []);

  useEffect(() => { loadVersions(selected); setSaveEcho(''); }, [selected]);

  // The first version of a benchmark is 'initial'; afterwards the operator
  // says what kind of change it is.
  useEffect(() => {
    setChangeType(versions.length ? 'rebalance' : 'initial');
  }, [versions.length]);

  const [visibleResults, ShowMoreResults] = useShowAll(results || [], 8);

  const searchSeries = async () => {
    try {
      const j = await apiGet(`/api/benchmarks/series?q=${encodeURIComponent(query)}`);
      setResults(j.series || []);
    } catch { setResults([]); }
  };

  // `points` travels with the row so a constituent that has no prices yet
  // (a ticker registered but not fetched) is flagged in the basket: the
  // materialization would fail loud on it.
  const addConstituent = (s) => {
    if (constituents.some((c) => c.series_id === s.series_id)) return;
    setConstituents([...constituents, {
      label: s.label, series_id: s.series_id, detail: s.detail, weight: '', fx_series_id: '',
      points: s.points,
    }]);
  };

  // ---- a Bloomberg ticker the pipelines do not carry yet ----
  // Writes the registry only (entity, Bloomberg identifier, PX_LAST series
  // as backfill-pending); the Bloomberg machine fetches the history on its
  // next backfill run. Until then the series shows "no prices".
  const [tProcode, setTProcode] = useState('');
  const [tName, setTName] = useState('');
  const [tTicker, setTTicker] = useState('');
  const [tType, setTType] = useState('security');
  const [tStart, setTStart] = useState('');
  const [tickerEcho, setTickerEcho] = useState('');
  const canRegister = tProcode.trim() && tName.trim() && tTicker.trim() && tStart && !busy;

  const registerTicker = async () => {
    setTickerEcho('');
    const r = await apiSend('/api/benchmarks/series', 'POST', {
      procode: tProcode.trim().toUpperCase(), name: tName.trim(), parsekyable: tTicker.trim(),
      default_start_date: tStart, entity_type: tType,
    });
    if (!r.ok) { setTickerEcho({ ok: false, text: r.data.detail }); return; }
    const s = r.data;
    setTickerEcho({
      ok: true,
      text: s.created
        ? `${s.procode} (${s.ticker}) registered, history pending: the Bloomberg machine fetches it on its next backfill run.`
        : `${s.procode} was already registered (${STATUS[s.status] || s.status}).`,
    });
    if (s.created) { setTProcode(''); setTName(''); setTTicker(''); }
    // Show it where it will be picked from.
    setQuery(s.procode);
    const j = await apiGet(`/api/benchmarks/series?q=${encodeURIComponent(s.procode)}`).catch(() => null);
    if (j) setResults(j.series || []);
  };

  const weightSum = constituents.reduce((a, c) => a + (Number(c.weight) || 0), 0);
  const sumOk = Math.abs(weightSum - 1) < 1e-6 || Math.abs(weightSum - 100) < 1e-4;
  const canSave = !!current && constituents.length > 0 && sumOk && !!effectiveFrom
    && reason.trim().length > 0 && !busy;

  // Loads the latest version's basket into the form, to adjust and save as
  // a new version - the only way to "edit" an append-only definition.
  const loadLatest = () => {
    if (!latest) return;
    setConstituents(latest.constituents.map((c) => ({
      label: c.label, series_id: c.series_id,
      detail: `${c.field} · ${c.source} · ${c.procode}`,
      weight: String(+(c.weight * 100).toFixed(4)),
      fx_series_id: c.fx_series_id ? String(c.fx_series_id) : '',
    })));
    setSaveEcho({ ok: true, text: `Basket of version ${latest.version_id} loaded into the editor; saving creates a new version.` });
  };

  const materialize = async (procode, label) => {
    const r = await apiSend(`/api/benchmarks/${encodeURIComponent(procode)}/materialize`, 'POST');
    if (!r.ok) { setSaveEcho({ ok: false, text: r.data.detail }); return; }
    setSaveEcho({ ok: true, text: `Rebuilding ${label}…` });
    start(r.data.action, onTaskDone((t) => {
      loadList();
      setSaveEcho(t.error
        ? { ok: false, text: `The rebuild failed and the previous series is intact: ${t.error}` }
        : { ok: true, text: `${label} rebuilt from its versions.` });
    }));
  };

  const saveVersion = async () => {
    if (!current) return;
    setSaveEcho('');
    const body = {
      effective_from: effectiveFrom,
      method: METHOD,
      rebalance_frequency: null,
      change_type: changeType,
      reason: reason.trim(),
      support_doc: supportDoc.trim() || null,
      recorded_by: recordedBy.trim() || null,
      supersedes_version_id: latest ? latest.version_id : null,
      constituents: constituents.map((c) => ({
        label: c.label, series_id: c.series_id, weight: Number(c.weight),
        fx_series_id: c.fx_series_id ? Number(c.fx_series_id) : null,
      })),
    };
    const r = await apiSend(`/api/benchmarks/${encodeURIComponent(current.procode)}/versions`, 'POST', body);
    if (!r.ok) { setSaveEcho({ ok: false, text: r.data.detail }); return; }
    setReason(''); setSupportDoc('');
    loadVersions(current.procode);
    setSaveEcho({ ok: true, text: `Version ${r.data.version_id} saved, effective from ${fDate(effectiveFrom)}.` });
    await materialize(current.procode, current.display_name);
  };

  // Changelog rows of one version, by series, for the marks in the history.
  const changesOf = (versionId) => {
    const out = {};
    changelog.filter((r) => r.version_id === versionId).forEach((r) => { out[r.series_id] = r; });
    return out;
  };

  return (
    <>
      {/* ===== a · which exist ===== */}
      <div className="panel">
        <div className="panel-title">Declared benchmarks</div>
        <p className="page-sub">Every composite index is a portfolio with its level series.
          Declaring it names it, assigns it to a house fund (it appears in that fund's return
          tables, in the SPP Panel) and registers its series; its basket is defined by
          <b> versions</b>, in the editor below.</p>

        <div className="table-wrap">
          <table>
            <thead><tr><th>Code</th><th>Name</th><th>Fund</th><th className="num">Versions</th>
              <th className="num">Days computed</th><th>From</th><th>To</th><th className="num">Last level</th></tr></thead>
            <tbody>
              {list === null && <tr><td colSpan={8} className="dim">Loading…</td></tr>}
              {list && !list.length && <tr><td colSpan={8} className="dim">No benchmark declared yet.</td></tr>}
              {(list || []).map((b) => (
                <tr key={b.procode} style={b.procode === selected ? { fontWeight: 700 } : undefined}>
                  <td className="mono">{b.procode}</td>
                  <td>{b.display_name}</td>
                  <td className={b.fund == null ? 'neg' : ''}>{b.fund == null ? 'unassigned' : `Fund ${b.fund}`}</td>
                  <td className="num">{nInt(b.versions)}</td>
                  <td className={`num ${b.points ? '' : 'dim'}`}>{b.points ? nInt(b.points) : 'not computed'}</td>
                  <td>{fDate(b.from)}</td>
                  <td>{fDate(b.to)}</td>
                  <td className="num">{b.last_level == null ? '—'
                    : b.last_level.toLocaleString('es-PE', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="controls spp-controls" style={{ marginTop: 12 }}>
          <div className="field" style={{ flex: '0 1 220px' }}>
            <label htmlFor="b-procode">Code (procode)</label>
            <input id="b-procode" className="date-input mono" placeholder="e.g. SPP_BENCH_F2"
              value={newProcode} onChange={(e) => setNewProcode(e.target.value)} /></div>
          <div className="field" style={{ flex: '1 1 260px' }}>
            <label htmlFor="b-name">Name</label>
            <input id="b-name" className="date-input" placeholder="e.g. Global balanced 60/40"
              value={newName} onChange={(e) => setNewName(e.target.value)} /></div>
          <div className="field"><label>Benchmark of {houseName || 'the house'}</label>
            <SppSeg items={funds.map((f) => [`Fund ${f}`, f])} value={newFund} onChange={setNewFund} /></div>
          <div className="field"><label aria-hidden="true">&nbsp;</label>
            <button className="btn primary" disabled={!newProcode.trim() || !newName.trim() || newFund == null || busy}
              title={newFund == null ? 'Pick the fund this is the benchmark of' : undefined}
              onClick={createBenchmark}>
              {(list || []).some((b) => b.procode === newProcode.trim().toUpperCase()) ? 'Rename' : 'Declare benchmark'}</button></div>
        </div>
        <Echo echo={createEcho} />
      </div>

      {/* ===== b · the versioned definition ===== */}
      <div className="spp-two-up">
        <div className="panel">
          <div className="panel-title">New version of "{current?.display_name || '—'}"</div>
          <p className="page-sub">The basket is <b>append-only</b>: changing weights or constituents
            edits nothing, it creates a new version from its effective date, with a reason.
            Rebalancing is <b>manual</b>: the weights you declare hold from that date until the
            next version, which is the one that rebalances. Levels are rebuilt from the first
            version (base 100) and never loaded by hand.</p>

          <div className="controls spp-controls">
            <div className="field"><label>Benchmark</label>
              <SppSeg items={(list || []).map((b) => [b.display_name, b.procode])}
                value={selected} onChange={setSelected} /></div>
          </div>
          <div className="controls spp-controls" style={{ marginTop: 8 }}>
            <div className="field"><label htmlFor="v-from">Effective from</label>
              <input id="v-from" className="date-input" type="date" value={effectiveFrom}
                onChange={(e) => setEffectiveFrom(e.target.value)} /></div>
            <div className="field"><label htmlFor="v-type">Change type</label>
              <select id="v-type" className="select" value={changeType}
                disabled={!versions.length}
                onChange={(e) => setChangeType(e.target.value)}>
                {Object.entries(CHANGE_TYPES)
                  .filter(([k]) => (versions.length ? k !== 'initial' : k === 'initial'))
                  .map(([k, label]) => <option key={k} value={k}>{label}</option>)}
              </select></div>
          </div>
          <div className="controls spp-controls" style={{ marginTop: 8 }}>
            <div className="field" style={{ flex: '1 1 320px' }}>
              <label htmlFor="v-reason">Reason (required)</label>
              <input id="v-reason" className="date-input" placeholder="e.g. Quarterly committee: overweight equities"
                value={reason} onChange={(e) => setReason(e.target.value)} /></div>
            <div className="field" style={{ flex: '1 1 200px' }}>
              <label htmlFor="v-doc">Supporting document</label>
              <input id="v-doc" className="date-input" placeholder="network path (optional)"
                value={supportDoc} onChange={(e) => setSupportDoc(e.target.value)} /></div>
            <div className="field" style={{ flex: '0 1 160px' }}>
              <label htmlFor="v-by">Recorded by</label>
              <input id="v-by" className="date-input" placeholder="initials"
                value={recordedBy} onChange={(e) => setRecordedBy(e.target.value)} /></div>
          </div>
          {latest && (
            <p className="page-sub dim" style={{ marginTop: 6 }}>
              Supersedes version {latest.version_id} (effective from {fDate(latest.effective_from)}).
              {' '}<button type="button" className="btn" style={{ marginLeft: 6 }} onClick={loadLatest}>
                Load its basket into the editor</button>
            </p>
          )}

          <div className="controls" style={{ marginTop: 10 }}>
            <input className="date-input" placeholder="Search series (code, ticker, name, ISIN)…"
              aria-label="Search series"
              value={query} onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && searchSeries()} />
            <button className="btn" onClick={searchSeries}>Search</button>
          </div>
          {results && (
            <div className="table-wrap" style={{ marginTop: 8 }}>
              <table><tbody>
                {visibleResults.map((s) => (
                  <tr key={s.series_id}>
                    <td className="mono">{s.label}</td>
                    <td className="dim">{s.detail}</td>
                    <td className={s.points ? 'dim' : 'neg'}>
                      {s.points ? `${fDate(s.from)} – ${fDate(s.to)} · ${nInt(s.points)} prices` : 'no prices yet'}
                    </td>
                    <td><button className="btn" onClick={() => addConstituent(s)}>+ Add</button></td>
                  </tr>
                ))}
                {!results.length && <tr><td className="dim">No results. A ticker the pipelines do not carry yet is registered below.</td></tr>}
              </tbody></table>
            </div>
          )}
          {results && <ShowMoreResults label="series found" />}

          <details className="spp-collapsible">
            <summary>Ticker not there? Register it from Bloomberg</summary>
            <p className="page-sub dim" style={{ marginTop: 4 }}>It is added to the database registry
              as a daily PX_LAST series, history pending. This dashboard does not talk to Bloomberg: the
              machine with the terminal fetches the history on its next backfill run and sets it active.</p>
            <div className="controls spp-controls">
              <div className="field" style={{ flex: '0 1 150px' }}>
                <label htmlFor="t-procode">Code</label>
                <input id="t-procode" className="date-input mono" placeholder="SPY_US"
                  value={tProcode} onChange={(e) => setTProcode(e.target.value)} /></div>
              <div className="field" style={{ flex: '1 1 200px' }}>
                <label htmlFor="t-name">Name</label>
                <input id="t-name" className="date-input" placeholder="SPDR S&P 500 ETF"
                  value={tName} onChange={(e) => setTName(e.target.value)} /></div>
              <div className="field" style={{ flex: '0 1 190px' }}>
                <label htmlFor="t-ticker">Bloomberg ticker</label>
                <input id="t-ticker" className="date-input mono" placeholder="SPY US Equity"
                  value={tTicker} onChange={(e) => setTTicker(e.target.value)} /></div>
              <div className="field"><label htmlFor="t-type">Type</label>
                <select id="t-type" className="select" value={tType} onChange={(e) => setTType(e.target.value)}>
                  {Object.entries(ENTITY_TYPES).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                </select></div>
              <div className="field"><label htmlFor="t-start">History from</label>
                <input id="t-start" className="date-input" type="date" value={tStart}
                  onChange={(e) => setTStart(e.target.value)} /></div>
              <div className="field"><label aria-hidden="true">&nbsp;</label>
                <button className="btn" disabled={!canRegister} onClick={registerTicker}>Register</button></div>
            </div>
            <Echo echo={tickerEcho} />
          </details>

          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table>
              <thead><tr><th>Constituent</th><th>Series</th><th className="num">Weight</th>
                <th>FX (optional)</th><th></th></tr></thead>
              <tbody>
                {constituents.length ? constituents.map((c, i) => (
                  <tr key={c.series_id}>
                    <td className="mono">{c.label}</td>
                    <td className="dim">{c.detail}{c.points === 0 && <> · <b className="neg">no prices</b></>}</td>
                    <td className="num">
                      <input className="date-input" type="number" min="0" step="0.01"
                        style={{ width: 90 }} value={c.weight} aria-label={`Weight of ${c.label}`}
                        onChange={(e) => setConstituents(
                          constituents.map((x, j) => (j === i ? { ...x, weight: e.target.value } : x)))} />
                    </td>
                    <td>
                      <select className="select" value={c.fx_series_id} aria-label={`FX of ${c.label}`}
                        onChange={(e) => setConstituents(
                          constituents.map((x, j) => (j === i ? { ...x, fx_series_id: e.target.value } : x)))}>
                        <option value="">— no conversion —</option>
                        {fxCatalog.map((s) => (
                          <option key={s.series_id} value={String(s.series_id)}>{s.label} · {s.field} · {s.source}</option>
                        ))}
                      </select>
                    </td>
                    <td><button className="btn danger"
                      onClick={() => setConstituents(constituents.filter((_, j) => j !== i))}>Remove</button></td>
                  </tr>
                )) : <tr><td colSpan={5} className="dim">Search series and add them to the basket.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="page-sub">Sum of weights: <b className={sumOk ? 'pos' : 'neg'}>
            {weightSum.toLocaleString('es-PE', { maximumFractionDigits: 4 })}</b> (must be 1 or 100)</p>

          <div className="controls">
            <button className="btn primary" disabled={!canSave} onClick={saveVersion}
              title={reason.trim() ? undefined : 'Write the reason for the version'}>
              Save version and rebuild</button>
            <button className="btn" disabled={!current || !versions.length || busy}
              onClick={() => materialize(current.procode, current.display_name)}>
              Rebuild "{current?.display_name || '—'}"</button>
          </div>
          <Echo echo={saveEcho} />
          {/* The materialization runs in the shared task; its trail shows here. */}
          {task && <Log task={task} />}
        </div>

        <div className="panel">
          <div className="panel-title">Computed series</div>
          <p className="page-sub">
            {current?.points
              ? `${nInt(current.points)} dates · ${fDate(current.from)} to ${fDate(current.to)} · last level ${
                current.last_level.toLocaleString('es-PE', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`
              : 'No computed series yet: save a version and rebuild.'}
          </p>
          {current?.points > 0 && (
            <div className="controls" style={{ marginBottom: 12 }}>
              <a className="btn" href={apiUrl(`/api/benchmarks/${encodeURIComponent(current.procode)}/export`)}>
                ↓ Download the series · XLSX</a>
            </div>
          )}

          <div className="panel-title">Version history</div>
          <p className="page-sub">Each version holds from its date until the next. Nothing is deleted:
            a correction is another version that says which one it supersedes and why.</p>
          {versions.length ? [...versions].reverse().map((v) => {
            const changes = changesOf(v.version_id);
            const dropped = Object.values(changes).filter((r) => r.change === 'dropped');
            return (
              <div key={v.version_id} style={{ marginBottom: 14 }}>
                <div className="panel-title" style={{ fontSize: 13 }}>
                  Version {v.version_id} · from {fDate(v.effective_from)} · {CHANGE_TYPES[v.change_type] || v.change_type}
                </div>
                <p className="page-sub" style={{ margin: '2px 0 6px' }}>
                  <b>{v.reason}</b>
                  {' · '}{METHOD_LABELS[v.method] || v.method}
                  {v.rebalance_frequency ? ` (${FREQUENCY_LABELS[v.rebalance_frequency] || v.rebalance_frequency})` : ''}
                  {v.supersedes_version_id ? ` · supersedes ${v.supersedes_version_id}` : ''}
                  {' · recorded '}{fTime(v.created_at)}{v.recorded_by ? ` by ${v.recorded_by}` : ''}
                  {v.support_doc ? <> · <span className="mono">{v.support_doc}</span></> : null}
                </p>
                <Report rows={[
                  ...v.constituents.map((c) => {
                    const ch = changes[c.series_id];
                    const mark = ch ? CHANGE_MARK[ch.change] : '';
                    const before = ch && ch.change === 'reweighted' ? ` (was ${pctOf(ch.weight_before)})` : '';
                    return [`${c.label}${c.fx_series_id ? ' (with FX)' : ''}${mark ? ` · ${mark}` : ''}`,
                      `${pctOf(c.weight)}${before}`];
                  }),
                  ...dropped.map((r) => [`${r.label} · ${CHANGE_MARK.dropped}`, `was ${pctOf(r.weight_before)}`]),
                ]} />
              </div>
            );
          }) : <p className="page-sub dim">No versions yet.</p>}
        </div>
      </div>
    </>
  );
}
