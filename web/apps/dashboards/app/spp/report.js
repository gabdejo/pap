// web/apps/dashboards/app/spp/report.js
// ---------------------------------------------------------------------------
// The performance report: a PDF built in the browser from what the Panel
// already knows how to compute, at the chosen control date.
//
//   Page 1      the relative-return table (the house against each
//               competitor), in blocks per fund, as on screen.
//   After it    cumulative alpha in basis points, first YTD then FY: two
//               funds per page, in fund order. Four funds -> eight charts
//               on four pages.
//
// Built here and not on the server for the same reason as each chart's CSV:
// the charts come from the SAME traces the Panel draws (alphaOf), rasterized
// by the same Plotly, so the PDF cannot tell a different story from the
// screen. jspdf and jspdf-autotable load only when the button is pressed:
// 400 KB the page does not need to draw itself.
//
// The PDF is printed: white background and dark tones even when the tablero
// is in dark mode, and no character outside Latin-1 (jsPDF's base font has
// neither the typographic minus nor arrows).
// ---------------------------------------------------------------------------
'use client';

import { apiGet } from '../../lib/api';
import {
  alphaOf, fDate, fmtReturn, operates, houseFirst, shownValue,
} from '../../lib/spp';

// Proportion of each chart: two per A4 portrait page, with title.
const WIDTH_PX = 1600;
const HEIGHT_PX = 900;

// Tones for paper: distinct from each other and legible on white.
const TONES = ['#1f2933', '#6b7c93', '#a7b1bd', '#cfd5db'];
const HOUSE_RED = [208, 43, 31];
const HOUSE_SHADE = [253, 234, 232];
const GREEN = [22, 140, 90];
const RED = [190, 40, 40];
const GRAY = [90, 90, 90];

const latin1 = (t) => String(t).replace(/−/g, '-').replace(/→/g, '->');

function paperLayout(title, subtitle) {
  // Sizes for paper: the 1600 px image prints at 19 cm (A4 portrait), so
  // 13 px of screen came out as 4-point type.
  const font = { family: 'Helvetica, Arial, sans-serif', size: 25, color: '#222' };
  return {
    // Every chart titles itself, as in the Panel ("Alpha F2"), with the
    // range and the unit below: the image has to be understood on its own.
    title: { text: title, font: { ...font, size: 32 }, x: 0.02, xanchor: 'left',
      subtitle: { text: subtitle, font: { ...font, size: 22, color: '#666' } } },
    paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff', font,
    // l: room for "-450 bps" plus the axis title; at 130 the sign was
    // clipped on funds that move in hundreds of bps.
    margin: { l: 165, r: 30, t: 110, b: 100 },
    legend: { orientation: 'h', y: -0.22, font },
    xaxis: { gridcolor: '#e6e6e6', linecolor: '#cccccc', tickfont: font },
    yaxis: {
      title: 'Cumulative alpha (bps)', gridcolor: '#e6e6e6', linecolor: '#cccccc',
      zeroline: true, zerolinecolor: '#666', zerolinewidth: 1.5, ticksuffix: ' bps',
      tickfont: font,
    },
  };
}

async function alphaImage(Plotly, { series, house, cfg, title, subtitle }) {
  const curves = alphaOf(series, house);
  if (!curves.length) return null;
  const order = houseFirst(cfg, curves.map((c) => c.afp)).filter((a) => a !== house);
  const data = curves.map((c) => ({
    x: c.x, y: c.y, type: 'scatter', mode: 'lines', name: c.afp,
    line: { color: TONES[order.indexOf(c.afp) % TONES.length], width: 2.2 },
  }));
  return Plotly.toImage({ data, layout: paperLayout(title, subtitle) },
    // scale 1.2 is enough to print; at 1.5 and uncompressed, eight charts
    // made a 40 MB PDF.
    { format: 'png', width: WIDTH_PX, height: HEIGHT_PX, scale: 1.2 });
}

/**
 * Builds and downloads the PDF. `windows` is the /api/spp/windows response
 * at the control date (it carries the relative table and the reference
 * dates).
 */
export async function generateReport({ cfg, windows }) {
  const [{ jsPDF }, { default: autoTable }, Plotly] = await Promise.all([
    import('jspdf'), import('jspdf-autotable'), import('plotly.js-dist-min'),
  ]);
  const house = cfg.house;
  const funds = (cfg.funds || []).filter((f) => (windows.relative || []).some((r) => r.fund === f));
  const names = (cfg.afps || []).map((a) => a.name);
  const date = windows.dates.t;

  // The eight charts are requested at once; each is a short query.
  const spans = [['YTD', windows.dates.year], ['FY', windows.dates.fy]];
  const requests = [];
  funds.forEach((f) => spans.forEach(([label, from]) => {
    const afps = names.filter((a) => operates(cfg, a, f));
    const q = new URLSearchParams({
      fund: String(f), afps: afps.join(','), metric: 'valor_cuota', from, to: date,
    });
    requests.push(apiGet(`/api/spp/series?${q}`).then((d) => ({ f, label, series: d.series || [] })));
  }));
  const seriesPerChart = await Promise.all(requests);

  // A4 portrait, as it is filed and printed.
  const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
  const W = doc.internal.pageSize.getWidth();
  const M = 10;

  // ---- Page 1: the relative table -------------------------------------------
  doc.setFont('helvetica', 'bold'); doc.setFontSize(15); doc.setTextColor(30);
  doc.text(latin1(`Performance report · ${fDate(date)}`), M, 15);
  doc.setFont('helvetica', 'normal'); doc.setFontSize(8); doc.setTextColor(...GRAY);
  doc.text(doc.splitTextToSize(latin1(
    `Relative performance · ${house} vs each competitor · month start ${fDate(windows.dates.month)}`
    + ` · M-1 ${fDate(windows.dates.month1)} · year start ${fDate(windows.dates.year)}`
    + ` · fiscal year start ${fDate(windows.dates.fy)}`), W - 2 * M), M, 20);

  // The filed page keeps the thirteen columns it was laid out for: the
  // trailing windows (3M ... 10Y annualized) stay on screen.
  const TRAILING = new Set(['a10', 'a5', 'a3', 'm12', 'm6', 'm3']);
  const cols = (windows.return_columns || []).filter((c) => c[2] !== 'level' && !TRAILING.has(c[0]));
  const order = houseFirst(cfg, names);
  const body = [];
  const meta = [];
  funds.forEach((f) => {
    const rows = (windows.relative || []).filter((r) => r.fund === f)
      .sort((a, b) => order.indexOf(a.afp) - order.indexOf(b.afp));
    rows.forEach((r, i) => {
      const row = [];
      if (i === 0) {
        row.push({ content: `Fund ${f}`, rowSpan: rows.length,
          styles: { valign: 'middle', fontStyle: 'bold', textColor: GRAY, halign: 'left' } });
      }
      row.push(latin1(r.afp));
      cols.forEach((c) => row.push(latin1(fmtReturn(r.values[c[0]], c[2], 'valor_cuota'))));
      body.push(row);
      meta.push({ isHouse: r.afp === house, r });
    });
  });
  autoTable(doc, {
    startY: 26, margin: { left: M, right: M },
    head: [['Fund', 'AFP', ...cols.map((c) => latin1(c[1]))]],
    body, theme: 'grid',
    // Thirteen columns in 19 cm: 6-point type, the smallest that prints
    // legibly, and tight cells; a wide AFP column for the house in bold.
    styles: { font: 'helvetica', fontSize: 6, cellPadding: 0.9, halign: 'right',
      lineColor: [225, 225, 225], lineWidth: 0.2, textColor: [40, 40, 40], overflow: 'visible' },
    headStyles: { fillColor: [240, 240, 240], textColor: [60, 60, 60], fontStyle: 'bold' },
    columnStyles: { 0: { halign: 'left', cellWidth: 11 }, 1: { halign: 'left', cellWidth: 29 } },
    didParseCell: (d) => {
      if (d.section !== 'body') return;
      const m = meta[d.row.index];
      const col = d.column.index;
      if (col === 0) return;                       // the fund cell
      if (m.isHouse) {
        d.cell.styles.fontStyle = 'bold';
        d.cell.styles.fillColor = HOUSE_SHADE;
        if (col === 1) d.cell.styles.textColor = HOUSE_RED;
      }
      if (col >= 2) {
        const c = cols[col - 2];
        const n = Number(shownValue(m.r.values[c[0]], c[2]));
        if (Number.isFinite(n) && n > 0) d.cell.styles.textColor = GREEN;
        else if (Number.isFinite(n) && n < 0) d.cell.styles.textColor = RED;
      }
    },
  });
  doc.setFontSize(7); doc.setTextColor(...GRAY);
  // Split to the page width: in one piece it ran off the right edge.
  const note = doc.splitTextToSize(latin1(
    'Newest on the left. Up to months in basis points; YTD, FY and last year in percent.'), W - 2 * M);
  doc.text(note, M, doc.lastAutoTable.finalY + 6);

  // ---- Alpha: all the YTD, then all the FY; two funds per page -------------
  const imgWidth = W - 2 * M;
  const imgHeight = imgWidth * HEIGHT_PX / WIDTH_PX;
  const PER_PAGE = 2;
  for (const [label, from] of spans) {
    for (let i = 0; i < funds.length; i += PER_PAGE) {
      doc.addPage();
      doc.setFont('helvetica', 'bold'); doc.setFontSize(13); doc.setTextColor(30);
      doc.text(latin1(`Cumulative alpha ${label} · ${house} vs each AFP`), M, 15);
      let y = 22;
      for (const f of funds.slice(i, i + PER_PAGE)) {
        const g = seriesPerChart.find((x) => x.f === f && x.label === label);
        const title = `Alpha F${f} · ${label}`;
        const subtitle = `from ${fDate(from)} to ${fDate(date)} · basis points, ${house} minus each AFP`;
        const img = g ? await alphaImage(Plotly, { series: g.series, house, cfg, title, subtitle }) : null;
        if (img) {
          // 'FAST' = Flate: jsPDF stores the PNG uncompressed unless asked.
          doc.addImage(img, 'PNG', M, y, imgWidth, imgHeight, undefined, 'FAST');
        } else {
          doc.setFont('helvetica', 'normal'); doc.setFontSize(10); doc.setTextColor(...GRAY);
          doc.text(latin1(`${title}: not enough data in the window.`), M, y + 10);
        }
        y += imgHeight + 10;
      }
      doc.setFont('helvetica', 'normal'); doc.setFontSize(7); doc.setTextColor(...GRAY);
      doc.text(latin1(`In basis points: ${house} minus each AFP, cumulative from the first`
        + ` day of the window with data in every series.`), M, y - 2);
    }
  }

  // "dd-mm-yyyy": Windows does not allow "/" in a file name.
  doc.save(`Performance report ${fDate(date).replace(/\//g, '-')}.pdf`);
  return { pages: 1 + funds.length, charts: funds.length * spans.length };
}
