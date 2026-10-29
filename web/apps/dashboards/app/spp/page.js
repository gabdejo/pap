// web/apps/dashboards/app/spp/page.js
// ---------------------------------------------------------------------------
// SPP tablero · Panel: historic series, performance windows (levels /
// absolute / relative) and monthly rank positions. Ported from the
// monitor's view 01 onto the pap stack: Plotly instead of hand-built SVG,
// pap panels, and the /api/spp/* contract.
//
// The interface names no AFP: names, colors, the house and which funds each
// one operates all come from /api/spp/config, loaded before anything else.
// Labels are English, like the upstream dashboards; AFP names and the
// metric keys are the SBS's own and stay as they come.
// ---------------------------------------------------------------------------
'use client';

import { useEffect, useMemo, useState } from 'react';
import dynamic from 'next/dynamic';
import { apiGet } from '../../lib/api';
import {
  COMPETITOR_GRAY, alphaOf, colorOf, periodWindow, fDate, fmtMetric, fmtReturn,
  grayLine, nInt, metricName, operates as operatesCfg, houseFirst, sign,
  shownValue,
} from '../../lib/spp';
import SppSeg from '../../components/SppSeg';
import DataDownload from '../../components/DataDownload';
import { useDashboard } from '../../components/DashboardProvider';
import { useModule } from '../../components/ModuleContext';
import { generateReport } from './report';
import { chartTheme } from '../../lib/theme';

const PlotlyChart = dynamic(() => import('../../components/PlotlyChart'), { ssr: false });

export default function SppPanelPage() {
  const [cfg, setCfg] = useState(null);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);

  // The panel reads ONE metric: the price. Fondo (S/) and cuotas are still
  // stored and the Book still offers them; here they are the closing
  // table's columns, not a series worth a chart.
  const metric = 'valor_cuota';
  // Fund type and period come from the floating pill (ModuleContext and
  // the upstream DashboardProvider): the fund governs the chart AND the
  // monthly positions, the period the chart's window.
  const pill = useModule();
  const dash = useDashboard();
  const fund = typeof pill?.fund === 'number' ? pill.fund : 2;
  const period = dash?.period || 'YTD';
  // Base 100 by default: it is the only reading in which four funds that
  // started on different dates and bases can share one chart.
  const [scale, setScale] = useState('base');
  const [selectedAfps, setSelectedAfps] = useState([]);

  const [seriesData, setSeriesData] = useState(null);
  const [win, setWin] = useState(null);
  const [pos, setPos] = useState(null);
  // The control date governs the WHOLE panel: chart, windows and positions.
  // It starts at the last date with COMPLETE valor cuota (every AFP and
  // fund published); cuotas and fondo do not count for that.
  const [controlDate, setControlDate] = useState('');
  const [generating, setGenerating] = useState(false);
  const [posView, setPosView] = useState('table');
  // The benchmarks assigned to each fund are rows of the return tables;
  // this switch takes them off without another request.
  const [showBench, setShowBench] = useState(true);

  // Config first: without it we don't know how many AFPs exist.
  useEffect(() => {
    apiGet('/api/spp/config')
      .then((c) => { setCfg(c); setSelectedAfps(c.afps.map((a) => a.name)); })
      .catch((e) => setError(e.message));
    apiGet('/api/spp/status').then(setStatus).catch((e) => setError(e.message));
  }, []);

  const names = useMemo(() => (cfg?.afps || []).map((a) => a.name), [cfg]);
  const house = cfg?.house;

  useEffect(() => {
    if (status?.to_complete && !controlDate) setControlDate(status.to_complete);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.to_complete]);
  const operates = (afp, f) => operatesCfg(cfg, afp, f);

  // The fund comes from the pill: keep the AFP selection valid for it.
  useEffect(() => {
    if (!names.length) return;
    setSelectedAfps((prev) => {
      const valid = prev.filter((a) => operates(a, fund));
      return valid.length ? valid : names.filter((a) => operates(a, fund));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fund, names]);

  // Historic series follows the metric/fund/window/AFP selection - and the
  // reference dates, which is what MTD/YTD actually start from. Leaving
  // win?.dates out of the deps meant the chart kept the previous month's
  // base after a change of control date, disagreeing with the windows
  // tables right beside it, and never adopted the true prior close the
  // first time it loaded (it used the calendar fallback instead).
  useEffect(() => {
    if (!cfg || !selectedAfps.length || !controlDate) return;
    setError(null);
    // The pill's period counts back from the control date, not from the
    // book's last date; Custom brings its own two dates.
    const { from, to } = periodWindow(period, controlDate,
      { customFrom: dash?.customFrom, customTo: dash?.customTo, refDates: win?.dates });
    // In Alpha the house is every line's reference: it is always requested,
    // even when the operator switched it off in "AFPs on screen".
    const afps = (scale === 'alpha' && house && !selectedAfps.includes(house))
      ? [house, ...selectedAfps] : selectedAfps;
    const q = new URLSearchParams({
      fund: String(fund), afps: afps.join(','), metric, from, to,
    });
    apiGet(`/api/spp/series?${q}`).then(setSeriesData).catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg, controlDate, metric, fund, period, dash?.customFrom, dash?.customTo, selectedAfps,
      scale === 'alpha', win?.dates?.year]);

  // Windows + positions follow metric and control date.
  useEffect(() => {
    if (!cfg || !controlDate) return;
    setError(null);
    const q = new URLSearchParams({ metric, date: controlDate });
    apiGet(`/api/spp/windows?${q}`).then(setWin).catch((e) => setError(e.message));
    const q2 = new URLSearchParams({ metric, months: '12', date: controlDate });
    apiGet(`/api/spp/positions?${q2}`).then(setPos).catch((e) => setError(e.message));
  }, [cfg, metric, controlDate]);

  // ---- Chart traces ------------------------------------------------------
  // The house AFP stands out in its brand color; competitors render in a
  // gray scale of decreasing weight (the monitor's positions-chart design):
  // one glance says how the house is doing without losing who it competes
  // against. Grays are spread over however many competitors are on screen.
  const grayOf = (afp, onScreen) => {
    const others = onScreen.filter((a) => a !== house);
    const i = others.indexOf(afp);
    const alpha = others.length < 2 ? 0.8 : 0.85 - (0.5 * i) / (others.length - 1);
    return grayLine(alpha.toFixed(2));
  };

  // The SAME ink everywhere: lines, selector chips, table dots. The color
  // says "this is the house", not "this is such AFP" - if every competitor
  // wore its brand color, with a red theme Habitat (#c50331) and Prima
  // (#ca3f04) would compete visually with Profuturo instead of staying
  // behind. `list` decides how the gray scale is spread.
  const inkOf = (afp, list) =>
    (afp === house ? colorOf(cfg, afp, true) : grayOf(afp, list));

  const series = seriesData?.series || [];

  // What the hover label says: per AFP, how much since the start of the
  // window and how much it moved that day, each figure in the color of its
  // sign. They travel as pre-built text in customdata because the color
  // depends on the value and the hover template knows no conditions.
  const withSign = (v, fmt) => {
    if (v == null || !Number.isFinite(v)) return '<span style="opacity:0.6">—</span>';
    const t = chartTheme();
    const color = v > 0 ? t.positive : v < 0 ? t.negative : t.muted;
    return `<span style="color:${color}">${fmt(v)}</span>`;
  };
  const pct = (v) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`;
  const bps = (v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)} bps`;
  const cumulativeAndDaily = (ys, fmt, cumulativeOf) => ys.map((y, i) => [
    withSign(cumulativeOf(y), fmt),
    withSign(i > 0 ? y - ys[i - 1] : null, fmt),
  ]);

  // Alpha: how much the house has gained over EACH competitor since the
  // start of the window, in basis points. It is the difference of the two
  // Base 100 curves - house minus the other - so positive is the house
  // ahead and each day's slope is what that day opened or closed the gap.
  // One line per competitor, none for the house: the house is the zero.
  const alphaTraces = () => {
    const withData = series.filter((s) => s.points.length);
    const onScreen = withData.map((s) => s.afp);
    // Without a house line the grays no longer say "this is not the house":
    // they are equal lines. Same ink, but the tones spread end to end (the
    // normal spread bunches them so the house stands out), and all solid:
    // dashed strokes read as data with holes.
    const order = houseFirst(cfg, onScreen).filter((a) => a !== house);
    const toneOf = (afp) => {
      const i = order.indexOf(afp);
      const a = order.length < 2 ? 0.9 : 0.95 - (0.6 * i) / (order.length - 1);
      return grayLine(a.toFixed(2));
    };
    return alphaOf(series, house).map((c) => {
      const color = toneOf(c.afp);
      return {
        // Only the AFP: the title already says everything is against the house.
        x: c.x, y: c.y, type: 'scatter', mode: 'lines', name: c.afp,
        legendrank: houseFirst(cfg, onScreen).indexOf(c.afp) + 1,
        line: { color, width: 2 },
        // In bps the cumulative IS the line's value; the daily, its step.
        customdata: cumulativeAndDaily(c.y, bps, (y) => y),
        hovertemplate: `<b>${c.afp}</b><br>Cumulative alpha  %{customdata[0]}`
          + '<br>Day  %{customdata[1]}<extra></extra>',
        hoverlabel: { bordercolor: color },
      };
    });
  };

  // The name and the last value at the end of every line, so the chart
  // reads without the legend. Annotations, not trace text: Plotly does not
  // keep texts apart, and two AFPs closing a point from each other wrote
  // one label over the other. Each label starts at its line's last value
  // and the labels are then spread, in the order of the returns, so that
  // neighbours sit a line of type apart.
  const endLabel = (name, y) => {
    if (y == null) return '';
    const value = scale === 'alpha' ? `${y >= 0 ? '+' : ''}${y.toFixed(1)} bps`
      : scale === 'base' ? y.toLocaleString('es-PE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : fmtMetric(y, metric, true);
    return `${name}  ${value}`;
  };

  const traces = useMemo(() => {
    if (scale === 'alpha') return alphaTraces();
    const withData = series.filter((s) => s.points.length);
    let baseDate = null;
    if (scale === 'base' && withData.length) {
      // First date with data in every AFP on screen: the only start that
      // makes the rebased lines comparable.
      const starts = withData.map((s) => s.points[0][0]);
      baseDate = starts.reduce((m, d) => (d > m ? d : m), starts[0]);
    }
    const onScreen = withData.map((s) => s.afp);
    return withData.map((s) => {
      const isHouse = s.afp === house;
      const color = inkOf(s.afp, onScreen);
      let pts = s.points;
      let base = null;
      if (scale === 'base') {
        pts = s.points.filter((p) => p[0] >= baseDate);
        base = pts.length ? pts[0][1] : null;
      }
      // Cumulative return since the first point on screen and the day's
      // change, on the raw valor cuota: the same in Level and Base 100.
      const raw = pts.map((p) => p[1]);
      const start = raw[0];
      const returns = raw.map((v, i) => [
        withSign(start ? v / start - 1 : null, pct),
        withSign(i > 0 && raw[i - 1] ? v / raw[i - 1] - 1 : null, pct),
      ]);
      return {
        x: pts.map((p) => p[0]),
        y: pts.map((p) => (base ? (p[1] / base) * 100 : p[1])),
        type: 'scatter', mode: 'lines', name: s.afp,
        // legendrank puts the house first in the LEGEND while the trace
        // order (house last) keeps its line drawn on top of the grays.
        legendrank: houseFirst(cfg, onScreen).indexOf(s.afp) + 1,
        line: { color, width: isHouse ? 2.8 : 1.6 },
        customdata: returns,
        hovertemplate: `<b>${s.afp}</b><br>Cumulative return  %{customdata[0]}`
          + '<br>Daily return  %{customdata[1]}<extra></extra>',
        hoverlabel: { bordercolor: color },
      };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series, scale, cfg, house]);

  // The y range is set by hand - the data span plus 5%, and the zero in
  // Alpha - so the pixels per unit are known and the labels can be kept a
  // line of type apart. The plot area's height is read from Plotly once
  // drawn (the legend under the axis takes part of the bottom margin, so
  // it is not the chart height minus the margins); 309 px is what the
  // 420 px chart measures today, used until the first measurement.
  const [plotPx, setPlotPx] = useState(309);
  const measurePlot = (_, gd) => {
    const len = gd?._fullLayout?.yaxis?._length;
    if (len && Math.abs(len - plotPx) > 1) setPlotPx(len);
  };
  const LABEL_GAP_PX = 14;
  // Top-down pass: each label at least a gap under the one above;
  // bottom-up pass: the stack does not run past the floor.
  const { yRange, endLabels } = useMemo(() => {
    let lo = Infinity; let hi = -Infinity;
    traces.forEach((t) => (t.y || []).forEach((v) => {
      if (v == null) return;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }));
    if (!Number.isFinite(lo)) return { yRange: undefined, endLabels: [] };
    if (scale === 'alpha') { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    const pad = (hi - lo || Math.abs(hi) || 1) * 0.05;
    lo -= pad; hi += pad;
    const pxPerUnit = plotPx / (hi - lo);
    const gap = LABEL_GAP_PX / pxPerUnit;
    const labels = traces
      .filter((t) => t.y?.length && t.y[t.y.length - 1] != null)
      .map((t) => ({
        x: t.x[t.x.length - 1], anchor: t.y[t.y.length - 1], color: t.line?.color,
        text: endLabel(t.name, t.y[t.y.length - 1]),
      }))
      .sort((a, b) => b.anchor - a.anchor);
    labels.forEach((l, i) => { l.y = i ? Math.min(l.anchor, labels[i - 1].y - gap) : l.anchor; });
    for (let i = labels.length - 1; i >= 0; i -= 1) {
      const floor = i === labels.length - 1 ? lo + gap / 2 : labels[i + 1].y + gap;
      labels[i].y = Math.max(labels[i].y, floor);
    }
    return {
      yRange: [lo, hi],
      // The text sits at the spread position, a little right of the last
      // date, with no leader: the stack keeps the order of the returns,
      // which is what the eye needs to pair a label with its line.
      endLabels: labels.map((l) => ({
        x: l.x, y: l.y, xref: 'x', yref: 'y', text: l.text, xshift: 10,
        xanchor: 'left', yanchor: 'middle', align: 'left',
        font: { color: l.color, size: 11 },
        showarrow: false,
      })),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [traces, scale, plotPx]);

  const ct = chartTheme();

  const pdfReport = async () => {
    if (!cfg || !win?.control) return;
    setGenerating(true); setError(null);
    try {
      // The PDF prints what is on screen: benchmarks only when shown.
      await generateReport({ cfg, windows: { ...win, relative: visibleRows(win.relative) } });
    } catch (e) {
      setError(`The report could not be generated: ${e.message}`);
    } finally {
      setGenerating(false);
    }
  };

  const windowLabel = period === 'Custom' && dash?.customFrom && dash?.customTo
    ? `${fDate(dash.customFrom)} to ${fDate(dash.customTo)}` : period;
  // Short title - the reader already knows it is the valor cuota series -
  // and under the chart what it is expressed in. The window and the rest
  // of the selection are visible in the bar above.
  const seriesTitle = scale === 'alpha' ? `Alpha F${fund}` : `Unit value F${fund}`;
  const seriesFooter = scale === 'alpha'
    ? `In basis points: ${house} minus each AFP`
    : scale === 'base' ? 'Rebased to 100'
      : `In ${metricName(cfg, metric).toLowerCase()}`;
  // "Unit value F2 · Rebased to 100 · 1Y" -> a file name
  const fileNameOf = (t) => t.toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  // ---- Windows tables ----------------------------------------------------
  const relativeOrder = houseFirst(cfg, names);
  const hasBenchmarks = (win?.absolute || []).some((r) => r.benchmark);
  const visibleRows = (rows) => (rows || []).filter((r) => showBench || !r.benchmark);

  function WindowsTable({ cols, rows, withUnit, order, bpsDigits = 1 }) {
    // One block per FUND and inside it one row per AFP: the desk's question
    // is "in fund 2, how are we doing against each one", and so the four
    // answers stay together. The fund goes in a column on the left, one
    // cell spanning its rows (rowSpan), not in a row of its own: each title
    // row was one line of data less on screen. The house goes first and in
    // its color; competitors, neutral.
    const afpOrder = order || relativeOrder;
    const funds = (cfg?.funds || []).filter((f) => rows.some((r) => r.fund === f));
    // A thick rule where the unit changes (basis points -> percent -> the
    // level), so the eye does not read +19 bps and +1.00% as one scale.
    const unitBreak = new Set(cols
      .filter((c, i) => withUnit && i > 0 && c[2] !== cols[i - 1][2]).map((c) => c[0]));
    const breakClass = (key) => (unitBreak.has(key) ? ' spp-unit-break' : '');
    return (
      <div className="table-wrap spp-windows spp-grid-windows">
        {/* Fixed layout with one colgroup: the fund and AFP columns have set
            widths and every window column takes an equal share, so the
            relative and the absolute tables line up column for column. */}
        <table>
          <colgroup>
            <col className="spp-col-fund" />
            <col className="spp-col-afp" />
            {cols.map((c) => <col key={c[0]} />)}
          </colgroup>
          <thead><tr><th>Fund</th><th className="spp-afp">AFP</th>
            {cols.map((c) => <th key={c[0]} className={breakClass(c[0]).trim() || undefined}>{c[1]}</th>)}</tr></thead>
          <tbody>
            {funds.map((f) => {
              // AFPs in the house-first order, then the fund's benchmarks in
              // the order the API lists them.
              const ofFund = rows.filter((r) => r.fund === f)
                .sort((x, y) => ((x.benchmark ? 1 : 0) - (y.benchmark ? 1 : 0))
                  || (afpOrder.indexOf(x.afp) - afpOrder.indexOf(y.afp)));
              return ofFund.map((r, i) => {
                const isHouse = r.afp === house;
                // The house row is bold THROUGHOUT - name and figures -
                // keeping each figure's sign color: it stands out by
                // weight, not by color. A benchmark row is an index, not
                // an AFP: italic name, a dashed rule above.
                return (
                  <tr key={`f${f}-${r.afp}`}
                    className={`${i === 0 ? 'spp-block' : ''} ${isHouse ? 'spp-house' : ''} ${r.benchmark ? 'spp-benchmark' : ''}`.trim()}>
                    {i === 0 && (
                      <td rowSpan={ofFund.length} className="spp-fund">Fund {f}</td>
                    )}
                    {/* With the fund in front this is no longer the row's
                        first cell and the global sheet would right-align it. */}
                    <td className="spp-afp"
                      style={isHouse ? { color: colorOf(cfg, r.afp, true) } : undefined}>
                      {r.afp}</td>
                    {cols.map((c) => {
                      const unit = withUnit ? c[2] : 'level';
                      const v = r.values[c[0]];
                      const cls = unit === 'level' ? 'num' : `num ${sign(shownValue(v, unit, bpsDigits))}`;
                      return (
                        <td key={c[0]} className={(v == null ? 'num dim' : cls) + breakClass(c[0])}>
                          {fmtReturn(v, unit, metric, bpsDigits)}</td>
                      );
                    })}
                  </tr>
                );
              });
            })}
          </tbody>
        </table>
      </div>
    );
  }

  // ---- Positions ---------------------------------------------------------
  const periods = pos?.periods || [];
  const posCompeting = names.filter((a) => operates(a, fund));
  const posTotal = Math.max(2, posCompeting.length);
  const posTraces = useMemo(() => names.map((afp) => {
    const row = (pos?.rows || []).find((r) => r.fund === fund && r.afp === afp);
    if (!row) return null;
    const xs = []; const ys = [];
    periods.forEach((p) => {
      const rank = row.ranks[p.key];
      if (rank) { xs.push(p.label); ys.push(rank); }
    });
    if (!xs.length) return null;
    const isHouse = afp === house;
    // Same house-vs-grays scheme as the historic series chart.
    const color = inkOf(afp, posCompeting);
    return {
      x: xs, y: ys, type: 'scatter', mode: 'lines+markers', name: afp,
      legendrank: houseFirst(cfg, posCompeting).indexOf(afp) + 1,
      line: { color, width: isHouse ? 3.2 : 2 },
      marker: { size: isHouse ? 9 : 7 },
      hovertemplate: `<b>${afp}</b> · %{x}: rank %{y}<extra></extra>`,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }).filter(Boolean), [pos, fund, cfg, house, names, periods]);

  return (
    <div className="spp-page">
      {/* The upstream masthead: brand name over the dashboard title, then
          the one-line subtitle, so every dashboard opens the same way. */}
      <div className="page-brand-block">
        <div className="page-brand-name">Profuturo Analytics</div>
        <div className="page-dashboard-title">SPP Unit Value</div>
      </div>
      <p className="page-sub">
        Daily unit value of the SPP funds · SBS source
        {status?.rows ? ` · ${nInt(status.rows)} dates (${fDate(status.from)} to ${fDate(status.to)})` : ''}
      </p>
      {/* An error shows AS A BAND, not replacing the page: it used to take
          away the controls with which the operator could fix the selection
          that caused it, and nothing cleared it but F5. */}
      {error && <div className="panel error">Error: {error}</div>}

      <div className="panel">
        <div className="controls spp-controls">
          <div className="field"><label>Control date</label>
            <input className="date-input" type="date" value={controlDate}
              max={status?.to || undefined}
              onChange={(e) => e.target.value && setControlDate(e.target.value)} /></div>
          <div className="field"><label>Scale</label>
            <SppSeg items={[['Level', 'level'], ['Base 100', 'base'], ['Alpha', 'alpha']]}
              value={scale} onChange={setScale} /></div>
          <div className="field"><label>AFPs on screen</label>
            <SppSeg multi items={names.filter((a) => operates(a, fund)).map((a) => [a, a])}
              value={selectedAfps}
              // Over the fund's COMPLETE list, not the selection: so each
              // AFP's tone does not change when another is toggled.
              colorOf={(a) => inkOf(a, names.filter((x) => operates(x, fund)))}
              onChange={(v) => setSelectedAfps((prev) => {
                const next = prev.includes(v) ? prev.filter((x) => x !== v) : [...prev, v];
                return next.length ? next : [v];
              })} /></div>
        </div>
      </div>

      <div className="panel">
        {/* The title says what is on screen - scale, fund and window -
            because the filters stay above and the chart is looked at alone. */}
        <div className="spp-chart-header">
          <span />
          <div className="panel-title" style={{ margin: 0 }}>{seriesTitle}</div>
          {/* The CSV carries the view as is: level, base 100 or alpha in bps.
              The file name repeats the title, which is what is read when the
              file shows up days later in a folder. */}
          <DataDownload traces={traces}
            name={fileNameOf(`${seriesTitle} · ${seriesFooter} · ${windowLabel}`)} />
        </div>
        {traces.length ? (
          <PlotlyChart
            data={traces}
            onInitialized={measurePlot}
            layout={{
              // " bps" lengthens the axis labels: without the extra margin
              // the minus sign is clipped and -200 reads as 200. The right
              // margin holds the end-of-line labels (name and value). Top
              // and bottom margins are part of the label spacing above.
              margin: { l: scale === 'alpha' ? 95 : 70, r: 150, t: 10, b: 60 },
              annotations: endLabels,
              // One label per date with a block per AFP, like the price
              // viewer's card: the date on top and, below, each AFP with
              // its cumulative and its day.
              hovermode: 'x unified',
              xaxis: { hoverformat: '%d %b %Y' },
              yaxis: {
                type: 'linear',
                title: scale === 'alpha' ? 'Cumulative alpha (bps)'
                  : scale === 'base' ? 'Base 100' : metricName(cfg, metric),
                // In Alpha the zero IS the house: the line marking it is the
                // reading reference, not noise.
                zeroline: scale === 'alpha',
                zerolinecolor: ct.muted, zerolinewidth: 1.5,
                ticksuffix: scale === 'alpha' ? ' bps' : '',
                range: yRange,
              },
            }}
          />
        ) : seriesData == null
          ? <div className="loading">Loading…</div>
          : <p className="page-sub dim">No data for this selection.</p>}
        {traces.length > 0 && (
          <p className="page-sub dim" style={{ textAlign: 'center', margin: '6px 0 0' }}>{seriesFooter}</p>
        )}
      </div>

      {/* The filter bar governs everything ABOVE this line. The windows and
          the ranks below carry their own controls (control date, fund), and
          without a visible cut the reader keeps changing the top filters and
          wondering why the tables do not move. */}
      <div className="spp-divider" role="separator">
        <span>Scale and AFPs govern up to here · the control date governs the whole panel · fund and period are set in the bar above</span>
      </div>

      <div className="panel">
        <div className="controls" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
          <div className="panel-title">Monthly performance ranks</div>
          <div className="controls" style={{ margin: 0 }}>
            <div className="field"><label>View</label>
              <SppSeg items={[['Table', 'table'], ['Chart', 'chart']]}
                value={posView} onChange={setPosView} /></div>
            <div className="field"><label aria-hidden="true">&nbsp;</label>
              {/* Table and chart are the same ranks: one CSV for both. */}
              <DataDownload traces={posTraces} xAxis="period"
                name={`ranks-fund-${fund}`} /></div>
          </div>
        </div>
        {/* One space, two readings of the same ranks: the heat table says
            WHO was where each month; the lines say how each one MOVED.
            Both at once said it twice, and the panel was two screens tall. */}
        {posView === 'chart' && (posTraces.length ? (
          <PlotlyChart
            data={posTraces}
            style={{ height: '300px' }}
            layout={{
              margin: { l: 50, r: 20, t: 10, b: 50 },
              yaxis: {
                autorange: 'reversed', dtick: 1, range: [posTotal + 0.5, 0.5],
                title: 'Rank', zeroline: false,
              },
              // The API's chronological order; by first appearance a trace
              // missing a month would have pushed that month to the end.
              xaxis: { type: 'category', categoryorder: 'array',
                categoryarray: periods.map((p) => p.label) },
            }}
          />
        ) : <div className="dim">Not enough closed months.</div>)}
        {posView === 'table' && (
        <div className="table-wrap spp-windows spp-grid-ranks">
          {/* Same discipline as the windows tables: the AFP column has a set
              width and every month takes an equal share. */}
          <table>
            <colgroup>
              <col className="spp-col-name" />
              {periods.map((p) => <col key={p.key} />)}
            </colgroup>
            <thead><tr><th>AFP</th>{periods.map((p) => <th key={p.key}>{p.label}</th>)}</tr></thead>
            <tbody>
              {/* House row first (it is the question this table answers),
                  heat in its brand ramp; competitors share ONE gray ramp so
                  intensity reads as rank everywhere, not as identity. */}
              {houseFirst(cfg, posCompeting).map((afp) => {
                const row = (pos?.rows || []).find((r) => r.fund === fund && r.afp === afp);
                if (!row) return null;
                const isHouse = afp === house;
                const base = isHouse ? colorOf(cfg, afp, true) : COMPETITOR_GRAY;
                return (
                  <tr key={afp}>
                    <td><span className="spp-chip"
                      style={{ background: inkOf(afp, posCompeting) }} />
                      <b style={{ color: isHouse ? colorOf(cfg, afp, true) : 'inherit' }}>{afp}</b></td>
                    {periods.map((p) => {
                      const rank = row.ranks[p.key];
                      if (!rank) return <td key={p.key} className="num dim">—</td>;
                      const t = posTotal < 2 ? 1 : 1 - (rank - 1) / (posTotal - 1);
                      const share = Math.round(7 + 93 * t);
                      return (
                        <td key={p.key} className="num spp-rank"
                          style={{
                            background: `color-mix(in srgb, ${base} ${share}%, transparent)`,
                            color: share >= 60 ? '#fff' : 'inherit',
                            fontWeight: rank === 1 ? 700 : 500,
                          }}>{rank}</td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        )}
        <p className="page-sub">Rank 1 to {posTotal} by the month's return, within each fund
          type{posView === 'table' ? ' — darker = better rank; the colored row is ' : '; the thick line is '}
          {house}. Each closed month is measured against the previous month's close; the last period is the MTD.</p>
      </div>

      <div className="panel">
        <div className="controls" style={{ justifyContent: 'space-between' }}>
          <div>
            <div className="panel-title">Performance windows</div>
            {win?.control && (
              <p className="page-sub" style={{ margin: '4px 0 0' }}>
                Control {fDate(win.dates.t)} · month start {fDate(win.dates.month)} ·
                M-1 {fDate(win.dates.month1)} · year start {fDate(win.dates.year)} ·
                fiscal year start {fDate(win.dates.fy)}
              </p>
            )}
          </div>
          <div className="controls" style={{ margin: 0 }}>
            {hasBenchmarks && (
              <div className="field"><label>Benchmarks</label>
                <SppSeg items={[['Show', true], ['Hide', false]]}
                  value={showBench} onChange={setShowBench} /></div>
            )}
            <div className="field"><label aria-hidden="true">&nbsp;</label>
              {/* The PDF report: what one takes away from these tables - the
                  relative table + alpha YTD/FY per fund, at the control date,
                  built in the browser. A single icon; the text goes in the
                  tooltip and in aria-label. */}
              <button className="btn primary spp-icon" onClick={pdfReport}
                disabled={generating || !win?.control}
                aria-label="Download the performance report as PDF"
                title={generating ? 'Generating the report…'
                  : 'Performance report (PDF): relative table + YTD and FY alpha of every fund, at the control date'}>
                {generating ? (
                  <span className="spp-spinning">…</span>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
                    strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M8 2v8" /><path d="M4.5 6.5 8 10l3.5-3.5" /><path d="M2.5 12.5v1h11v-1" />
                  </svg>
                )}
              </button></div>
          </div>
        </div>

        {win?.control ? (
          <>
            <details className="spp-collapsible" open>
              <summary>Relative performance · {house} vs each competitor{hasBenchmarks && showBench ? ' and its benchmarks' : ''}</summary>
              {/* Differences in whole basis points: a tenth of a point between
                  two AFPs is noise, and the decimal only widened the columns. */}
              <WindowsTable cols={(win.return_columns || []).filter((c) => c[2] !== 'level')}
                rows={visibleRows(win.relative)} withUnit order={relativeOrder} bpsDigits={0} />
            </details>
            <details className="spp-collapsible">
              <summary>Absolute performance</summary>
              {/* Returns only: the level itself is the Book's business. */}
              <WindowsTable cols={(win.return_columns || []).filter((c) => c[2] !== 'level')}
                rows={visibleRows(win.absolute)} withUnit />
              <p className="page-sub">Newest on the left. Up to 6M in basis points; from YTD on in
                percent. Trailing windows count back from the control date. FY is the fiscal year,
                31/10 to 31/10. The daily columns are that day's move, not cumulative.</p>
            </details>
          </>
        ) : win == null
          ? <div className="loading">Loading…</div>
          : <div className="page-sub dim">No data in the book.</div>}
      </div>
    </div>
  );
}
