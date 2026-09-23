// web/apps/dashboards/app/spp/reporte.js
// ---------------------------------------------------------------------------
// El "Reporte de rentabilidad": un PDF armado en el navegador con lo que el
// Panel ya sabe calcular, a la fecha de control elegida.
//
//   Pagina 1     la tabla de rendimiento relativo (la casa contra cada
//                competidora), con bloques por fondo, igual que en pantalla.
//   Despues      el alpha acumulado en puntos basicos, primero YTD y luego
//                FY: dos fondos por pagina, en el orden de los fondos.
//                Cuatro fondos -> ocho graficos en cuatro paginas.
//
// Se arma aqui y no en el servidor por la misma razon que el CSV de cada
// grafico: los graficos salen de las MISMAS trazas que pinta el Panel
// (alphaDe), rasterizadas por el mismo Plotly, asi que el PDF no puede
// contar una historia distinta de la pantalla. jspdf y jspdf-autotable se
// cargan solo al pulsar el boton: son 400 KB que la pagina no necesita
// para dibujarse.
//
// El PDF se imprime: fondo blanco y tonos oscuros aunque el tablero este
// en modo oscuro, y ningun caracter fuera de Latin-1 (la fuente base de
// jsPDF no trae el signo menos tipografico ni flechas).
// ---------------------------------------------------------------------------
'use client';

import { apiGet } from '../../lib/api';
import {
  alphaDe, fFecha, fmtRend, opera, ordenCasa, valorMostrado,
} from '../../lib/spp';

// Proporcion de cada grafico: dos por pagina A4 vertical, con titulo.
const ANCHO_PX = 1600;
const ALTO_PX = 900;

// Tonos para papel: distintos entre si y legibles sobre blanco.
const TONOS = ['#1f2933', '#6b7c93', '#a7b1bd', '#cfd5db'];
const ROJO_CASA = [208, 43, 31];
const SOMBRA_CASA = [253, 234, 232];
const VERDE = [22, 140, 90];
const ROJO = [190, 40, 40];
const GRIS = [90, 90, 90];

const latin1 = (t) => String(t).replace(/−/g, '-').replace(/→/g, '->');

function layoutPapel(titulo, subtitulo) {
  // Tamanos para papel: la imagen de 1600 px se imprime en 19 cm (A4
  // vertical), asi que 13 px de pantalla quedaban en letra de 4 puntos.
  const fuente = { family: 'Helvetica, Arial, sans-serif', size: 25, color: '#222' };
  return {
    // Cada grafico se titula solo, como en el Panel ("Alpha F2"), con el
    // rango y la unidad debajo: la imagen tiene que entenderse suelta.
    title: { text: titulo, font: { ...fuente, size: 32 }, x: 0.02, xanchor: 'left',
      subtitle: { text: subtitulo, font: { ...fuente, size: 22, color: '#666' } } },
    paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff', font: fuente,
    // l: sitio para "-450 bps" mas el titulo del eje; con 130 se recortaba
    // el signo en los fondos que se mueven en cientos de bps.
    margin: { l: 165, r: 30, t: 110, b: 100 },
    legend: { orientation: 'h', y: -0.22, font: fuente },
    xaxis: { gridcolor: '#e6e6e6', linecolor: '#cccccc', tickfont: fuente },
    yaxis: {
      title: 'Alpha acumulado (bps)', gridcolor: '#e6e6e6', linecolor: '#cccccc',
      zeroline: true, zerolinecolor: '#666', zerolinewidth: 1.5, ticksuffix: ' bps',
      tickfont: fuente,
    },
  };
}

async function imagenAlpha(Plotly, { series, casa, cfg, titulo, subtitulo }) {
  const curvas = alphaDe(series, casa);
  if (!curvas.length) return null;
  const orden = ordenCasa(cfg, curvas.map((c) => c.afp)).filter((a) => a !== casa);
  const data = curvas.map((c) => ({
    x: c.x, y: c.y, type: 'scatter', mode: 'lines', name: c.afp,
    line: { color: TONOS[orden.indexOf(c.afp) % TONOS.length], width: 2.2 },
  }));
  return Plotly.toImage({ data, layout: layoutPapel(titulo, subtitulo) },
    // scale 1.2 basta para imprimir; con 1.5 y sin comprimir, ocho graficos
    // hacian un PDF de 40 MB.
    { format: 'png', width: ANCHO_PX, height: ALTO_PX, scale: 1.2 });
}

/**
 * Genera y descarga el PDF. `vent` es la respuesta de /api/spp/ventanas a la
 * fecha de control (trae la tabla relativa y las fechas de referencia).
 */
export async function generarReporte({ cfg, vent }) {
  const [{ jsPDF }, { default: autoTable }, Plotly] = await Promise.all([
    import('jspdf'), import('jspdf-autotable'), import('plotly.js-dist-min'),
  ]);
  const casa = cfg.casa;
  const fondos = (cfg.fondos || []).filter((f) => (vent.relativos || []).some((r) => r.fondo === f));
  const nombres = (cfg.afps || []).map((a) => a.nombre);
  const fecha = vent.fechas.t;

  // Los ocho graficos se piden a la vez; cada uno es una consulta corta.
  const ventanas = [['YTD', vent.fechas.anio], ['FY', vent.fechas.fy]];
  const pedidos = [];
  fondos.forEach((f) => ventanas.forEach(([etiqueta, desde]) => {
    const afps = nombres.filter((a) => opera(cfg, a, f));
    const q = new URLSearchParams({
      fondo: String(f), afps: afps.join(','), metrica: 'valor_cuota', desde, hasta: fecha,
    });
    pedidos.push(apiGet(`/api/spp/serie?${q}`).then((d) => ({ f, etiqueta, series: d.series || [] })));
  }));
  const seriesPorGrafico = await Promise.all(pedidos);

  // A4 vertical, como se archiva y se imprime.
  const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
  const W = doc.internal.pageSize.getWidth();
  const M = 10;

  // ---- Pagina 1: la tabla relativa -----------------------------------------
  doc.setFont('helvetica', 'bold'); doc.setFontSize(15); doc.setTextColor(30);
  doc.text(latin1(`Reporte de rentabilidad · ${fFecha(fecha)}`), M, 15);
  doc.setFont('helvetica', 'normal'); doc.setFontSize(8); doc.setTextColor(...GRIS);
  doc.text(doc.splitTextToSize(latin1(
    `Rendimiento relativo · ${casa} contra cada competidora · inicio de mes ${fFecha(vent.fechas.mes)}`
    + ` · M-1 ${fFecha(vent.fechas.mes1)} · inicio de año ${fFecha(vent.fechas.anio)}`
    + ` · inicio de ejercicio ${fFecha(vent.fechas.fy)}`), W - 2 * M), M, 20);

  const cols = (vent.cols_rend || []).filter((c) => c[2] !== 'nivel');
  const orden = ordenCasa(cfg, nombres);
  const body = [];
  const meta = [];
  fondos.forEach((f) => {
    const filas = (vent.relativos || []).filter((r) => r.fondo === f)
      .sort((a, b) => orden.indexOf(a.afp) - orden.indexOf(b.afp));
    filas.forEach((r, i) => {
      const fila = [];
      if (i === 0) {
        fila.push({ content: `Fondo ${f}`, rowSpan: filas.length,
          styles: { valign: 'middle', fontStyle: 'bold', textColor: GRIS, halign: 'left' } });
      }
      fila.push(latin1(r.afp));
      cols.forEach((c) => fila.push(latin1(fmtRend(r.valores[c[0]], c[2], 'valor_cuota'))));
      body.push(fila);
      meta.push({ esCasa: r.afp === casa, r });
    });
  });
  autoTable(doc, {
    startY: 26, margin: { left: M, right: M },
    head: [['Fondo', 'AFP', ...cols.map((c) => latin1(c[1]))]],
    body, theme: 'grid',
    // Trece columnas en 19 cm: letra de 6 puntos, la minima que se lee bien
    // impresa, y celdas justas; la columna AFP ancha para la casa en negrita.
    styles: { font: 'helvetica', fontSize: 6, cellPadding: 0.9, halign: 'right',
      lineColor: [225, 225, 225], lineWidth: 0.2, textColor: [40, 40, 40], overflow: 'visible' },
    headStyles: { fillColor: [240, 240, 240], textColor: [60, 60, 60], fontStyle: 'bold' },
    columnStyles: { 0: { halign: 'left', cellWidth: 11 }, 1: { halign: 'left', cellWidth: 29 } },
    didParseCell: (d) => {
      if (d.section !== 'body') return;
      const m = meta[d.row.index];
      const col = d.column.index;
      if (col === 0) return;                       // la celda del fondo
      if (m.esCasa) {
        d.cell.styles.fontStyle = 'bold';
        d.cell.styles.fillColor = SOMBRA_CASA;
        if (col === 1) d.cell.styles.textColor = ROJO_CASA;
      }
      if (col >= 2) {
        const c = cols[col - 2];
        const n = Number(valorMostrado(m.r.valores[c[0]], c[2]));
        if (Number.isFinite(n) && n > 0) d.cell.styles.textColor = VERDE;
        else if (Number.isFinite(n) && n < 0) d.cell.styles.textColor = ROJO;
      }
    },
  });
  doc.setFontSize(7); doc.setTextColor(...GRIS);
  // Partida al ancho de la pagina: de una pieza se salia por la derecha.
  const nota = doc.splitTextToSize(latin1(
    'Hasta meses en puntos básicos; FY, YTD y año pasado en porcentaje.'), W - 2 * M);
  doc.text(nota, M, doc.lastAutoTable.finalY + 6);

  // ---- Alpha: todos los YTD, despues todos los FY; dos fondos por pagina ----
  const anchoImg = W - 2 * M;
  const altoImg = anchoImg * ALTO_PX / ANCHO_PX;
  const POR_PAGINA = 2;
  for (const [etiqueta, desde] of ventanas) {
    for (let i = 0; i < fondos.length; i += POR_PAGINA) {
      doc.addPage();
      doc.setFont('helvetica', 'bold'); doc.setFontSize(13); doc.setTextColor(30);
      doc.text(latin1(`Alpha acumulado ${etiqueta} · ${casa} contra cada AFP`), M, 15);
      let y = 22;
      for (const f of fondos.slice(i, i + POR_PAGINA)) {
        const g = seriesPorGrafico.find((x) => x.f === f && x.etiqueta === etiqueta);
        const titulo = `Alpha F${f} · ${etiqueta}`;
        const subtitulo = `del ${fFecha(desde)} al ${fFecha(fecha)} · puntos básicos, ${casa} menos cada AFP`;
        const img = g ? await imagenAlpha(Plotly, { series: g.series, casa, cfg, titulo, subtitulo }) : null;
        if (img) {
          // 'FAST' = Flate: jsPDF guarda el PNG sin comprimir si no se le pide.
          doc.addImage(img, 'PNG', M, y, anchoImg, altoImg, undefined, 'FAST');
        } else {
          doc.setFont('helvetica', 'normal'); doc.setFontSize(10); doc.setTextColor(...GRIS);
          doc.text(latin1(`${titulo}: sin datos suficientes en la ventana.`), M, y + 10);
        }
        y += altoImg + 10;
      }
      doc.setFont('helvetica', 'normal'); doc.setFontSize(7); doc.setTextColor(...GRIS);
      doc.text(latin1(`Expresado en puntos básicos: ${casa} menos cada AFP, acumulado desde el`
        + ` primer día de la ventana con dato en todas.`), M, y - 2);
    }
  }

  // "dd-mm-aaaa": Windows no admite "/" en un nombre de archivo.
  doc.save(`Reporte de rentabilidad ${fFecha(fecha).replace(/\//g, '-')}.pdf`);
  return { paginas: 1 + fondos.length, graficos: fondos.length * ventanas.length };
}
