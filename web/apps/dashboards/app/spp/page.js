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

import { useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { apiGet } from '../../lib/api';
import {
  alphaOf, colorOf, periodWindow, fDate, fmtMetric, fmtReturn,
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
const HOVER_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

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

  // What the hover card says: per AFP, how much since the start of the
  // window and how much it moved that day, each figure in the color of its
  // sign. They travel as pre-built text in customdata, and the card is
  // drawn by the page, not by Plotly: the unified hover lists the traces
  // in their drawing order, with no way to sort them, and the desk wants
  // the AFPs in the order of the lines at that date - the highest first,
  // the order changing where the lines cross. The traces carry
  // hoverinfo 'none', so Plotly still raises the hover event with every
  // trace at the date but draws nothing.
  const pct = (v) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`;
  const bps = (v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)} bps`;
  // A figure in the color of its sign, for the hover card.
  const signed = (v, fmt) => {
    if (v == null || !Number.isFinite(v)) return <span style={{ opacity: 0.6 }}>—</span>;
    const t = chartTheme();
    return <span style={{ color: v > 0 ? t.positive : v < 0 ? t.negative : t.muted }}>{fmt(v)}</span>;
  };
  // customdata per point: [cumulative since the window start, the day's move].
  const cumulativeAndDaily = (ys, cumulativeOf) => ys.map((y, i) => [
    cumulativeOf(y),
    i > 0 ? y - ys[i - 1] : null,
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
    const order = houseFirst(cfg, onScreen).filter((a) => a !== house && !series.find((s) => s.afp === a)?.benchmark);
    const toneOf = (afp) => {
      const i = order.indexOf(afp);
      const a = order.length < 2 ? 0.9 : 0.95 - (0.6 * i) / (order.length - 1);
      return grayLine(a.toFixed(2));
    };
    return alphaOf(series, house).map((c) => {
      const isBench = !!series.find((s) => s.afp === c.afp)?.benchmark;
      const color = isBench ? chartTheme().text : toneOf(c.afp);
      return {
        // Only the AFP: the title already says everything is against the house.
        x: c.x, y: c.y, type: 'scatter', mode: 'lines', name: c.afp,
        legendrank: houseFirst(cfg, onScreen).indexOf(c.afp) + 1,
        line: { color, width: 2, dash: isBench ? 'dash' : 'solid' },
        benchmark: isBench,
        // In bps the cumulative IS the line's value; the daily, its step.
        customdata: cumulativeAndDaily(c.y, (y) => y),
        hoverinfo: 'none',
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
    const onScreen = withData.filter((s) => !s.benchmark).map((s) => s.afp);
    return withData.map((s) => {
      const isHouse = s.afp === house;
      // The fund's assigned benchmark: a dashed line in the text color,
      // neither the house's red nor a competitor's gray.
      const color = s.benchmark ? chartTheme().text : inkOf(s.afp, onScreen);
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
        start ? v / start - 1 : null,
        i > 0 && raw[i - 1] ? v / raw[i - 1] - 1 : null,
      ]);
      return {
        x: pts.map((p) => p[0]),
        y: pts.map((p) => (base ? (p[1] / base) * 100 : p[1])),
        type: 'scatter', mode: 'lines', name: s.afp,
        // legendrank puts the house first in the LEGEND while the trace
        // order (house last) keeps its line drawn on top of the grays.
        legendrank: houseFirst(cfg, onScreen).indexOf(s.afp) + 1,
        line: { color, width: isHouse ? 2.8 : 1.6, dash: s.benchmark ? 'dash' : 'solid' },
        customdata: returns,
        hoverinfo: 'none',
        benchmark: !!s.benchmark,
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
  const { yRange, xRange, endLabels } = useMemo(() => {
    let lo = Infinity; let hi = -Infinity;
    traces.forEach((t) => (t.y || []).forEach((v) => {
      if (v == null) return;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }));
    if (!Number.isFinite(lo)) return { yRange: undefined, xRange: undefined, endLabels: [] };
    if (scale === 'alpha') { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    const pad = (hi - lo || Math.abs(hi) || 1) * 0.05;
    lo -= pad; hi += pad;
    const pxPerUnit = plotPx / (hi - lo);
    const gap = LABEL_GAP_PX / pxPerUnit;
    const labels = traces
      .filter((t) => t.y?.length && t.y[t.y.length - 1] != null)
      .map((t) => ({
        name: t.name, anchor: t.y[t.y.length - 1], color: t.line?.color,
        text: endLabel(t.name, t.y[t.y.length - 1]),
      }))
      .sort((a, b) => b.anchor - a.anchor);
    labels.forEach((l, i) => { l.y = i ? Math.min(l.anchor, labels[i - 1].y - gap) : l.anchor; });
    for (let i = labels.length - 1; i >= 0; i -= 1) {
      const floor = i === labels.length - 1 ? lo + gap / 2 : labels[i + 1].y + gap;
      labels[i].y = Math.max(labels[i].y, floor);
    }
    // The x range ends on the last date, so the lines run to the edge of
    // the grid and the labels sit just outside it, in the right margin.
    let x0 = null; let x1 = null;
    traces.forEach((t) => {
      if (!t.x?.length) return;
      if (x0 == null || t.x[0] < x0) x0 = t.x[0];
      const last = t.x[t.x.length - 1];
      if (x1 == null || last > x1) x1 = last;
    });
    return {
      yRange: [lo, hi],
      xRange: x0 && x1 ? [x0, x1] : undefined,
      // The text sits at the spread position, outside the grid, with no
      // leader: the stack keeps the order of the returns, which is what
      // the eye needs to pair a label with its line.
      endLabels: labels.map((l) => ({
        x: 1, xref: 'paper', y: l.y, yref: 'y', text: l.text, xshift: 10,
        xanchor: 'left', yanchor: 'middle', align: 'left',
        font: { color: l.color, size: 12, weight: l.name === house ? 700 : 400 },
        showarrow: false,
      })),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [traces, scale, plotPx]);

  const ct = chartTheme();

  // ---- The hover card ---------------------------------------------------
  // One card per hovered date, placed next to the pointer inside the chart
  // wrapper, flipped to the left near the right edge so it never leaves
  // the panel. Rows sorted by the value at that date, highest first.
  const chartWrap = useRef(null);
  const [hover, setHover] = useState(null);
  const hoverDate = (x) => {
    const [y, m, d] = String(x).slice(0, 10).split('-');
    return `${Number(d)} ${HOVER_MONTHS[Number(m) - 1]} ${y}`;
  };
  const onChartHover = (ev) => {
    const pts = (ev?.points || []).filter((p) => p.y != null);
    const wrap = chartWrap.current;
    if (!pts.length || !wrap || !ev.event) return;
    const box = wrap.getBoundingClientRect();
    const x = ev.event.clientX - box.left;
    const y = ev.event.clientY - box.top;
    setHover({
      date: hoverDate(pts[0].x),
      rows: pts.map((p) => ({
        name: p.data.name, color: p.data.line?.color, y: p.y, benchmark: !!p.data.benchmark,
        cumulative: p.customdata?.[0], day: p.customdata?.[1],
      })).sort((a, b) => b.y - a.y),
      x, y, flip: x > box.width * 0.62,
    });
  };
  // One row per line: name, the value at the date and, in the color of
  // its sign, the cumulative since the window start (the day's step in
  // Alpha, where the value already is the cumulative).
  const hoverValue = (y) => (scale === 'alpha' ? bps(y)
    : scale === 'base' ? y.toFixed(1) : fmtMetric(y, metric, true));
  const hoverChange = (r) => (scale === 'alpha' ? signed(r.day, bps) : signed(r.cumulative, pct));

  // ---- Drawdown -----------------------------------------------------------
  // Under the lines, the house's drawdown (and the benchmark's) over the
  // same window: how far below its running peak each sits. Not in Alpha,
  // where the line is already a difference.
  const drawdown = useMemo(() => {
    if (scale === 'alpha') return [];
    return traces.filter((t) => t.name === house || t.benchmark).map((t) => {
      let peak = -Infinity; let min = 0;
      const y = (t.y || []).map((v) => {
        if (v > peak) peak = v;
        const d = peak > 0 ? v / peak - 1 : 0;
        if (d < min) min = d;
        return d;
      });
      return { name: t.name, x: t.x, y, min, color: t.line?.color, dash: t.line?.dash, isHouse: t.name === house };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [traces, scale, house]);
  const withAlpha = (color, a) => {
    if (!color) return color;
    if (color.startsWith('#') && color.length === 7) {
      return `rgba(${parseInt(color.slice(1, 3), 16)}, ${parseInt(color.slice(3, 5), 16)}, ${parseInt(color.slice(5, 7), 16)}, ${a})`;
    }
    return color.replace(/^rgb\(([^)]+)\)$/, `rgba($1, ${a})`);
  };
  const ddTraces = drawdown.map((d) => ({
    x: d.x, y: d.y, type: 'scatter', mode: 'lines', name: d.name, hoverinfo: 'none',
    line: { color: d.color, width: d.isHouse ? 1.8 : 1.2, dash: d.dash || 'solid' },
    ...(d.isHouse ? { fill: 'tozeroy', fillcolor: withAlpha(d.color, 0.14) } : {}),
  }));
  const ddLabels = [
    { text: 'DRAWDOWN', y: 0.95, color: ct.muted, size: 11 },
    ...drawdown.map((d, i) => ({
      text: `${d.name} max ${(d.min * 100).toFixed(1).replace('-', '−')}%`,
      y: 0.62 - i * 0.3, color: d.isHouse ? d.color : ct.text, size: 12,
    })),
  ].map((l) => ({
    x: 1, xref: 'paper', y: l.y, yref: 'paper', text: l.text, xshift: 10,
    xanchor: 'left', yanchor: 'middle', showarrow: false, font: { color: l.color, size: l.size },
  }));

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
  const seriesTitle = scale === 'alpha' ? `Alpha F${fund}`
    : scale === 'base' ? `Unit value base 100 F${fund}` : `${metricName(cfg, metric)} F${fund}`;
  const seriesFooter = scale === 'alpha'
    ? `In basis points: ${house} minus each AFP`
    : scale === 'base' ? 'Rebased to 100'
      : `In ${metricName(cfg, metric).toLowerCase()}`;
  const firstOnScreen = traces.reduce((m, t) => (t.x?.length && (!m || t.x[0] < m) ? t.x[0] : m), null);
  const seriesSubtitle = (scale === 'alpha'
    ? `Basis points, ${house} minus each line.`
    : scale === 'base' ? 'All series rebased to 100 at the start date.'
      : `In ${metricName(cfg, metric).toLowerCase()}.`)
    + (firstOnScreen ? ` From ${fDate(firstOnScreen)}.` : '');
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
          <div>
            <div className="panel-title" style={{ margin: 0 }}>{seriesTitle} · {windowLabel}</div>
            <div className="page-sub dim" style={{ margin: '2px 0 0' }}>{seriesSubtitle}</div>
          </div>
          {/* The CSV carries the view as is: level, base 100 or alpha in bps.
              The file name repeats the title, which is what is read when the
              file shows up days later in a folder. */}
          <DataDownload traces={traces}
            name={fileNameOf(`${seriesTitle} · ${seriesFooter} · ${windowLabel}`)} />
        </div>
        {traces.length ? (
          <div className="spp-chart-wrap" ref={chartWrap} onMouseLeave={() => setHover(null)}>
          <PlotlyChart
            data={traces}
            onInitialized={measurePlot}
            onHover={onChartHover}
            onUnhover={() => setHover(null)}
            layout={{
              // " bps" lengthens the axis labels: without the extra margin
              // the minus sign is clipped and -200 reads as 200. The right
              // margin holds the end-of-line labels (name and value). Top
              // and bottom margins are part of the label spacing above.
              margin: { l: scale === 'alpha' ? 95 : 70, r: 150, t: 10, b: 60 },
              annotations: endLabels,
              // 'x unified' so the hover event carries every trace at the
              // date; the card itself is the page's (see onChartHover). The
              // spike is the vertical line that marks the hovered date.
              hovermode: 'x unified',
              // The labels at the right replace the legend.
              showlegend: false,
              xaxis: {
                range: xRange,
                showspikes: true, spikemode: 'across', spikesnap: 'data',
                spikethickness: 1, spikedash: 'solid', spikecolor: ct.muted,
              },
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
                // Only the date line: a horizontal spike at the pointer
                // said nothing (the card carries the values).
                showspikes: false,
              },
            }}
          />
          {hover && (
            <div className="spp-hover"
              style={hover.flip ? { right: `calc(100% - ${hover.x}px + 14px)`, top: hover.y + 12 }
                : { left: hover.x + 14, top: hover.y + 12 }}>
              <div className="spp-hover-date">{hover.date}</div>
              {hover.rows.map((r) => (
                <div key={r.name} className={`spp-hover-row${r.name === house ? ' spp-hover-house' : ''}`}>
                  <span className="spp-hover-swatch" style={{ borderColor: r.color, borderTopStyle: r.benchmark ? 'dashed' : 'solid' }} />
                  <span className="spp-hover-name">{r.name}</span>
                  <span className="spp-hover-val">{hoverValue(r.y)}</span>
                  <span className="spp-hover-val">{hoverChange(r)}</span>
                </div>
              ))}
            </div>
          )}
          </div>
        ) : seriesData == null
          ? <div className="loading">Loading…</div>
          : <p className="page-sub dim">No data for this selection.</p>}
        {ddTraces.length > 0 && (
          <PlotlyChart
            data={ddTraces}
            style={{ height: '170px' }}
            layout={{
              // Same left and right margins as the lines, so the two grids
              // share their x axis; the annotations sit in the right margin.
              margin: { l: 70, r: 150, t: 6, b: 40 },
              showlegend: false, hovermode: false,
              annotations: ddLabels,
              xaxis: { range: xRange },
              yaxis: { tickformat: '.1%', nticks: 3, zeroline: true, zerolinecolor: ct.muted, rangemode: 'tozero' },
            }}
          />
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
          <div className="panel-title">Monthly rank among AFPs</div>
          <div className="controls" style={{ margin: 0, alignItems: 'center', gap: 14 }}>
            <span className="dim" style={{ fontSize: 12 }}>Fund {fund} · 1 = best return of the month</span>
            <DataDownload traces={posTraces} xAxis="period" name={`ranks-fund-${fund}`} />
          </div>
        </div>
        {/* One reading: each cell says WHERE the AFP finished that month and
            WITH WHAT return, shaded by rank on one ramp for every row, so
            intensity reads as rank, not as identity. The house row is
            framed in its color. The line chart that once sat beside this
            table said the same thing twice. */}
        {periods.length ? (
        <div className="table-wrap spp-windows spp-grid-ranks">
          <table>
            <colgroup>
              <col className="spp-col-name" />
              {periods.map((p) => <col key={p.key} />)}
            </colgroup>
            <thead><tr><th /> {periods.map((p) => <th key={p.key}>{p.label}</th>)}</tr></thead>
            <tbody>
              {houseFirst(cfg, posCompeting).map((afp) => {
                const row = (pos?.rows || []).find((r) => r.fund === fund && r.afp === afp);
                if (!row) return null;
                const isHouse = afp === house;
                return (
                  <tr key={afp} className={isHouse ? 'spp-ranks-house' : undefined}>
                    <td><b style={{ color: isHouse ? colorOf(cfg, afp, true) : 'inherit' }}>{afp}</b></td>
                    {periods.map((p) => {
                      const rank = row.ranks[p.key];
                      if (!rank) return <td key={p.key} className="num dim">—</td>;
                      const ret = row.returns?.[p.key];
                      // Rank 1 almost solid ink, the last rank a faint wash.
                      const t = posTotal < 2 ? 1 : 1 - (rank - 1) / (posTotal - 1);
                      const share = Math.round(12 + 78 * t);
                      return (
                        <td key={p.key} className="spp-rank"
                          style={{
                            background: `color-mix(in srgb, var(--text-primary) ${share}%, transparent)`,
                            color: share >= 50 ? 'var(--s2)' : 'var(--text-primary)',
                          }}>
                          <div className="spp-ranks-cell">
                            <span className="spp-ranks-rank">{rank}</span>
                            <span className="spp-ranks-ret">
                              {ret == null ? '—' : `${ret >= 0 ? '+' : '−'}${Math.abs(ret * 100).toFixed(1)}%`}
                            </span>
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        ) : <div className="dim">Not enough closed months.</div>}
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
