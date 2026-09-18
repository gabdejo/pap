
// web/apps/dashboards/lib/spp.js
// ---------------------------------------------------------------------------
// SPP tablero helpers: es-PE number/date formatting and the domain constants,
// ported from the monitor's base.js. Valor cuota carries 7 decimals (as the
// SBS publishes it); cuotas and fondo are billions and abbreviate with the
// Peruvian desk convention (M = thousands, MM = millions).
// ---------------------------------------------------------------------------

import { BASE } from './api';

// Absolute URL for download links (CSV/XLSX) that bypass fetch.
export const apiUrl = (path) => `${BASE}${path}`;

// Lo que significa cada codigo EN ESTE TABLERO, no en abstracto. Solo se
// usa cuando la API no mando motivo: si lo mando, manda el suyo, que
// siempre es mas concreto que esto.
const ESTADOS = {
  400: 'Los datos enviados no son validos.',
  404: 'Esa operacion no existe en la API. Suele ser que el tablero quedo '
    + 'de una version mas nueva que la API: cierra y vuelve a abrir «Tablero SPP».',
  409: 'La operacion choca con el estado actual: o hay otra corriendo, o lo '
    + 'que intentas borrar o cambiar esta en uso. El detalle esta en la '
    + 'ventana «Tablero SPP (API)».',
  503: 'Esta maquina no puede hacer eso (falta el terminal o el permiso en el .env).',
};
const explicarEstado = (estado) => ESTADOS[estado]
  || `La API respondio ${estado}. El detalle esta en la ventana «Tablero SPP (API)».`;

// Mutating fetch (JSON or FormData). Returns {ok, status, data} instead of
// throwing so callers can show the backend's `motivo` verbatim.
//
// That contract has to hold for a DEAD API too, not just for an error
// response: every caller is an async onClick, so a rejected fetch used to
// surface as an unhandled rejection - the form simply went quiet and the
// operator could not tell whether the write had landed.
export async function apiSend(path, method, body, isForm = false) {
  const opts = { method };
  if (body !== undefined) {
    if (isForm) { opts.body = body; } else {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
  }
  try {
    const res = await fetch(`${BASE}${path}`, opts);
    const data = await res.json().catch(() => ({}));
    // Una respuesta de error SIEMPRE sale con motivo. Los dieciseis
    // sitios que llaman aqui muestran el motivo o, si no viene, "Error
    // 409" - un numero que es del protocolo, no del problema, y que al
    // operador no le dice nada. Rellenarlo aqui los arregla a todos.
    if (!res.ok && !data.motivo) data.motivo = explicarEstado(res.status);
    return { ok: res.ok, status: res.status, data };
  } catch {
    return {
      ok: false,
      status: 0,
      data: { motivo: 'No se pudo hablar con la API (¿se cerró la ventana «Tablero SPP (API)»?). No se guardó nada.' },
    };
  }
}

// The ONE name for each price store. The same source read 'BBG', 'bloomberg'
// and 'Bloomberg' within one screen; the benchmark editor shows them side by
// side, so they have to be said the same way.
export const NOMBRE_FUENTE = {
  bloomberg: 'Bloomberg', manual: 'Manual', fact: 'Histórico',
};
export const nombreFuente = (f) => NOMBRE_FUENTE[f] || f;

// FALLBACKS only, for the instant before /api/spp/config arrives: the
// metric universe and its labels are owned by the backend (cfg.metricas)
// - use metricasDe(cfg) / nombreMetrica(cfg, clave) below.
// RESPALDO del selector, para el instante antes de que llegue
// /api/spp/config, que es quien manda. 'Cuotas' no esta aqui ni alli: el
// dato existe y la tabla de cierres lo muestra, pero graficarlo no dice
// nada. NOMBRE_METRICA si la conserva, porque la API sigue aceptando
// metrica=cuotas y hay que saber como llamarla.
export const METRICAS = [
  ['Valor cuota', 'valor_cuota'],
  ['Fondo (S/)', 'fondo'],
];
export const NOMBRE_METRICA = {
  valor_cuota: 'Valor cuota', cuotas: 'Cuotas', fondo: 'Fondo (S/)',
};

// ---- Config-driven helpers ------------------------------------------------
// The interface names no AFP and no metric: everything comes from
// /api/spp/config. These are the one copy of the cfg lookups the pages share.
// The ONE neutral gray of the house-vs-grays scheme (charts, heatmap,
// fallback chip) - retune it here and every consumer follows.
export const GRIS_COMPETIDOR = 'rgb(128, 138, 152)';
export const GRIS_HEX = '#8892A4';
export const grisLinea = (alpha) => `rgba(128, 138, 152, ${alpha})`;

// The ONE house-first ordering (legends, tables, heatmap rows): the house
// AFP (cfg.casa) leads, the rest keep their relative order.
export const ordenCasa = (cfg, lista) => {
  const casa = cfg?.casa;
  return casa && lista.includes(casa)
    ? [casa, ...lista.filter((a) => a !== casa)]
    : [...lista];
};

export const afpDe = (cfg, afp) => (cfg?.afps || []).find((a) => a.nombre === afp);
export const opera = (cfg, afp, f) => {
  const a = afpDe(cfg, afp);
  return a ? a.fondos.includes(f) : true;
};
export const fondosDe = (cfg, afp) => (cfg?.fondos || []).filter((f) => opera(cfg, afp, f));
export const colorDe = (cfg, afp, solido = false) => {
  const a = afpDe(cfg, afp);
  return a ? (solido ? a.color_solido : a.color) : GRIS_HEX;
};
export const metricasDe = (cfg) =>
  (cfg?.metricas?.length ? cfg.metricas.map((m) => [m.etiqueta, m.clave]) : METRICAS);
export const nombreMetrica = (cfg, clave) =>
  (cfg?.metricas || []).find((m) => m.clave === clave)?.etiqueta
    || NOMBRE_METRICA[clave] || clave;
// Ordenadas por lo que abarcan. FY es el ejercicio, de 31/10 a 31/10: va
// despues de YTD porque casi siempre es mas largo - solo en noviembre y
// diciembre, recien empezado, es mas corto que el año calendario.
export const VENTANAS = [
  ['MTD', 'mtd'], ['YTD', 'ytd'], ['FY', 'fy'], ['1A', 1], ['3A', 3], ['5A', 5], ['10A', 10],
];

export const signo = (v) => (v == null ? '' : (v < 0 ? 'neg' : (v > 0 ? 'pos' : '')));

export const nf = (v, d = 7) =>
  (v == null ? '—' : v.toLocaleString('es-PE',
    { minimumFractionDigits: d, maximumFractionDigits: d }));

export const nEnt = (v) => (v == null ? '—' : v.toLocaleString('es-PE'));

export function fmtMetrica(v, metrica, compacto = false) {
  if (v == null) return '—';
  const fijo = (x, d) => x.toLocaleString('es-PE',
    { minimumFractionDigits: d, maximumFractionDigits: d });
  if (metrica === 'valor_cuota') return fijo(v, 7);
  if (compacto) {
    const a = Math.abs(v);
    if (a >= 1e6) return `${fijo(v / 1e6, 2)} MM`;
    if (a >= 1e3) return `${fijo(v / 1e3, 2)} M`;
  }
  return fijo(v, 2);
}

export const fFecha = (s) => {
  if (!s) return '—';
  const [a, m, d] = String(s).split('-');
  return `${d}/${m}/${a}`;
};

// Timestamp with the hour in 24h, like every other hour in the tablero (the
// scheduled task reads "18:00"; es-PE's default would print "06:00 p. m."
// right next to it). The API serializes task timestamps as ISO at the source,
// so plain Date parsing here is unambiguous.
export const fHora = (t) => {
  if (!t) return '—';
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? String(t) : d.toLocaleString('es-PE', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
};

// Today in the user's zone. valueAsDate interprets Dates in UTC, so from
// 19:00 Lima onward the naive approach yields tomorrow and the form
// rejects it as a future date.
export function hoyLocal() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// Value as displayed, already rounded. Sign and color derive from THIS
// number, not the raw one - otherwise -0.4 bps prints as "-0 bps".
export function valorMostrado(v, unidad) {
  if (v == null) return null;
  if (unidad === 'pct') return +(v * 100).toFixed(2);
  if (unidad === 'nivel') return v;
  return +(v * 10000).toFixed(1);
}

export function fmtRend(v, unidad, metrica = 'valor_cuota') {
  const x = valorMostrado(v, unidad);
  if (x == null) return '—';
  if (unidad === 'nivel') return fmtMetrica(x, metrica, metrica !== 'valor_cuota');
  const s = x > 0 ? '+' : (x < 0 ? '-' : '');
  const abs = Math.abs(x).toLocaleString('es-PE', {
    minimumFractionDigits: unidad === 'pct' ? 2 : 1,
    maximumFractionDigits: unidad === 'pct' ? 2 : 1,
  });
  return s + abs + (unidad === 'pct' ? '%' : ' bps');
}

// MTD/YTD/FY start at the LAST CLOSE of the prior period (same base the
// windows tables use); numeric values are years back from the last close.
//
// Lo de abajo es el RESPALDO de calendario, para el instante antes de que
// llegue /api/spp/ventanas. Cae en el dia natural, que puede no ser dia de
// cotizacion; en cuanto llegan las fechas de control manda la del cierre.
export function desdeVentana(v, estado, fechasVent) {
  const base = fechasVent || {};
  if (v === 'mtd' && base.mes) return base.mes;
  if (v === 'ytd' && base.anio) return base.anio;
  if (v === 'fy' && base.fy) return base.fy;
  const h = estado && estado.hasta ? new Date(`${estado.hasta}T00:00:00`) : new Date();
  if (v === 'mtd') { const d = new Date(h.getFullYear(), h.getMonth(), 0); return d.toISOString().slice(0, 10); }
  if (v === 'ytd') { const d = new Date(h.getFullYear(), 0, 0); return d.toISOString().slice(0, 10); }
  if (v === 'fy') {
    // El 31/10 cierra el ejercicio, no lo abre: parado en esa fecha se mira
    // el año que termina. Por eso el corte es "despues del 31/10", igual
    // que en el backend (_inicio_fy).
    const finDeEjercicio = new Date(h.getFullYear(), 9, 31);
    const anio = h > finDeEjercicio ? h.getFullYear() : h.getFullYear() - 1;
    return new Date(anio, 9, 31).toISOString().slice(0, 10);
  }
  h.setFullYear(h.getFullYear() - v);
  return h.toISOString().slice(0, 10);
}
