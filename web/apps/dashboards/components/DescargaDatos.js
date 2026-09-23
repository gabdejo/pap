// web/apps/dashboards/components/DescargaDatos.js
// ---------------------------------------------------------------------------
// NUESTRO. "Bajar los datos de este grafico": un CSV con EXACTAMENTE lo que
// el grafico pinta, armado en el navegador a partir de sus trazas.
//
// Por eso recibe las trazas de Plotly y no vuelve a pedir nada al API: la
// vista ya lleva su conversion - nivel, base 100, alpha en bps, puestos,
// volumen por periodo - y lo que el operador quiere en Excel es ESA lectura,
// no la serie cruda que ya tiene en el Libro. Una columna por traza (su
// nombre en la leyenda), una fila por valor del eje X, en el orden en que
// aparecen. Donde una traza no tiene dato para esa X, la celda va vacia.
//
// UTF-8 con BOM para que Excel lea las tildes; coma como separador, la
// misma del CSV que exporta el Libro desde el servidor.
// ---------------------------------------------------------------------------
'use client';

const esc = (v) => {
  const t = v == null ? '' : String(v);
  return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
};

export function csvDeTrazas(trazas, ejeX = 'fecha') {
  const xs = [];
  const visto = new Set();
  trazas.forEach((t) => (t.x || []).forEach((x) => {
    const k = String(x);
    if (!visto.has(k)) { visto.add(k); xs.push(k); }
  }));
  const porTraza = trazas.map((t) => {
    const m = new Map();
    (t.x || []).forEach((x, i) => m.set(String(x), t.y?.[i]));
    return m;
  });
  const cabecera = [ejeX, ...trazas.map((t) => t.name ?? '')].map(esc).join(',');
  const filas = xs.map((x) => [x, ...porTraza.map((m) => {
    const v = m.get(x);
    return v == null || Number.isNaN(v) ? '' : v;
  })].map(esc).join(','));
  return [cabecera, ...filas].join('\n');
}

export function descargarTexto(nombre, texto, tipo = 'text/csv;charset=utf-8') {
  const blob = new Blob(['﻿' + texto], { type: tipo });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = nombre;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * El boton. `nombre` es el archivo sin extension; conviene que diga lo que
 * el titulo del grafico dice (escala, fondo, ventana), porque el archivo
 * se mira despues, lejos de la pantalla que lo produjo.
 */
export default function DescargaDatos({ trazas, nombre, ejeX = 'fecha', etiqueta = '↓ Datos · CSV' }) {
  const hay = Array.isArray(trazas) && trazas.some((t) => t.x?.length);
  return (
    <button type="button" className="btn" disabled={!hay}
      title={hay ? 'Baja lo que muestra el gráfico, con su conversión' : 'Sin datos que bajar'}
      onClick={() => descargarTexto(`${nombre}.csv`, csvDeTrazas(trazas, ejeX))}>
      {etiqueta}
    </button>
  );
}
