
// web/apps/dashboards/app/spp/page.js
// ---------------------------------------------------------------------------
// SPP tablero · Panel: KPIs, historic series, close-by-AFP, performance
// windows (levels / absolute / relative) and monthly rank positions.
// Ported from the monitor's vista 01 onto the pap stack: Plotly instead of
// hand-built SVG, pap panels/KPI bar, and the /api/spp/* contract.
//
// The interface names no AFP: names, colors, the house and which funds each
// one operates all come from /api/spp/config, loaded before anything else.
// ---------------------------------------------------------------------------
'use client';

import { useEffect, useMemo, useState } from 'react';
import dynamic from 'next/dynamic';
import { apiGet } from '../../lib/api';
import {
  GRIS_COMPETIDOR, VENTANAS, colorDe, desdeVentana, fFecha,
  fmtMetrica, fmtRend, grisLinea, nEnt, nf, nombreMetrica,
  opera as operaCfg, ordenCasa, signo, valorMostrado,
} from '../../lib/spp';
import SppSeg from '../../components/SppSeg';
import SppTabs from '../../components/SppTabs';
import { chartTheme } from '../../lib/theme';

const PlotlyChart = dynamic(() => import('../../components/PlotlyChart'), { ssr: false });

export default function SppPanelPage() {
  const [cfg, setCfg] = useState(null);
  const [estado, setEstado] = useState(null);
  const [error, setError] = useState(null);

  // The panel reads ONE metric: the price. Fondo (S/) and cuotas are
  // still stored and the Libro still offers them; here they are the
  // closing table's columns, not a series worth a chart.
  const metrica = 'valor_cuota';
  const [fondo, setFondo] = useState(2);
  const [ventana, setVentana] = useState(1);
  // Base 100 by default: it is the only reading in which four funds that
  // started on different dates and bases can share one chart.
  const [escala, setEscala] = useState('base');
  const [afpsSel, setAfpsSel] = useState([]);

  const [serieData, setSerieData] = useState(null);
  const [vent, setVent] = useState(null);
  const [pos, setPos] = useState(null);
  const [fechaControl, setFechaControl] = useState('');
  const [fondoPos, setFondoPos] = useState(2);
  const [vistaPos, setVistaPos] = useState('tabla');

  // Config first: without it we don't know how many AFPs exist.
  useEffect(() => {
    apiGet('/api/spp/config')
      .then((c) => { setCfg(c); setAfpsSel(c.afps.map((a) => a.nombre)); })
      .catch((e) => setError(e.message));
    apiGet('/api/spp/estado').then(setEstado).catch((e) => setError(e.message));
  }, []);

  const nombres = useMemo(() => (cfg?.afps || []).map((a) => a.nombre), [cfg]);
  const casa = cfg?.casa;
  const opera = (afp, f) => operaCfg(cfg, afp, f);

  // Historic series follows the metric/fund/window/AFP selection - and the
  // reference dates, which is what MTD/YTD actually start from. Leaving
  // vent?.fechas out of the deps meant the chart kept the previous month's
  // base after a change of control date, disagreeing with the windows
  // tables right beside it, and never adopted the true prior close the
  // first time it loaded (it used the calendar fallback instead).
  useEffect(() => {
    if (!cfg || !afpsSel.length) return;
    setError(null);
    const desde = desdeVentana(ventana, estado, vent?.fechas);
    const q = new URLSearchParams({
      fondo: String(fondo), afps: afpsSel.join(','), metrica, desde,
    });
    apiGet(`/api/spp/serie?${q}`).then(setSerieData).catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg, estado, metrica, fondo, ventana, afpsSel,
      vent?.fechas?.mes, vent?.fechas?.anio, vent?.fechas?.fy]);

  // Windows + positions follow metric and control date.
  useEffect(() => {
    if (!cfg) return;
    setError(null);
    const q = new URLSearchParams({ metrica });
    if (fechaControl) q.set('fecha', fechaControl);
    apiGet(`/api/spp/ventanas?${q}`).then(setVent).catch((e) => setError(e.message));
    const q2 = new URLSearchParams({ metrica, meses: '12' });
    if (fechaControl) q2.set('fecha', fechaControl);
    apiGet(`/api/spp/posiciones?${q2}`).then(setPos).catch((e) => setError(e.message));
  }, [cfg, metrica, fechaControl]);

  // ---- Chart traces ------------------------------------------------------
  // The house AFP stands out in its brand color; competitors render in a
  // gray scale of decreasing weight (the monitor's positions-chart design):
  // one glance says how the house is doing without losing who it competes
  // against. Grays are spread over however many competitors are on screen.
  const grisDe = (afp, enPantalla) => {
    const otras = enPantalla.filter((a) => a !== casa);
    const i = otras.indexOf(afp);
    const alpha = otras.length < 2 ? 0.8 : 0.85 - (0.5 * i) / (otras.length - 1);
    return grisLinea(alpha.toFixed(2));
  };

  // La MISMA tinta en todas partes: lineas, chips del selector, puntos de las
  // tablas. El color dice "esta es la casa", no "esta es tal AFP" - si cada
  // competidora lleva su color de marca, con un tema rojo Habitat (#c50331) y
  // Prima (#ca3f04) compiten visualmente con Profuturo en vez de quedar
  // detras. `lista` decide el reparto de la escala de grises.
  const tintaDe = (afp, lista) =>
    (afp === casa ? colorDe(cfg, afp, true) : grisDe(afp, lista));

  const series = serieData?.series || [];
  const traces = useMemo(() => {
    const conDatos = series.filter((s) => s.puntos.length);
    let baseFecha = null;
    if (escala === 'base' && conDatos.length) {
      // First date with data in every AFP on screen: the only start that
      // makes the rebased lines comparable.
      const inicios = conDatos.map((s) => s.puntos[0][0]);
      baseFecha = inicios.reduce((m, f) => (f > m ? f : m), inicios[0]);
    }
    const enPantalla = conDatos.map((s) => s.afp);
    return conDatos.map((s) => {
      const esCasa = s.afp === casa;
      const color = tintaDe(s.afp, enPantalla);
      let pts = s.puntos;
      let base = null;
      if (escala === 'base') {
        pts = s.puntos.filter((p) => p[0] >= baseFecha);
        base = pts.length ? pts[0][1] : null;
      }
      return {
        x: pts.map((p) => p[0]),
        y: pts.map((p) => (base ? (p[1] / base) * 100 : p[1])),
        type: 'scatter', mode: 'lines', name: s.afp,
        // legendrank puts the house first in the LEGEND while the trace
        // order (house last) keeps its line drawn on top of the grays.
        legendrank: ordenCasa(cfg, enPantalla).indexOf(s.afp) + 1,
        line: { color, width: esCasa ? 2.8 : 1.6 },
        // La etiqueta de un solo valor, que es lo que el grafico necesita:
        // la comparacion entre AFP ya esta en las tablas de abajo.
        hovertemplate: `<b>${s.afp}</b> · %{x}<br>%{y:,.4f}<extra></extra>`,
        hoverlabel: { bordercolor: color },
      };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series, escala, cfg, casa]);

  const ct = chartTheme();

  // ---- Cierre por AFP (house first via the one shared ordering) ----------
  const ordenGlobal = ordenCasa(cfg, nombres);
  const cierres = (estado?.series || [])
    .filter((x) => x.fondo === fondo && afpsSel.includes(x.afp))
    .sort((a, b) => ordenGlobal.indexOf(a.afp) - ordenGlobal.indexOf(b.afp));

  // ---- Ventanas tables ---------------------------------------------------
  const ordenRel = ordenCasa(cfg, nombres);

  function TablaVentanas({ cols, filas, conUnidad, orden }) {
    // House first and in its brand color; competitor section headers stay
    // neutral - same criterion as the charts and the positions heatmap.
    return (
      <div className="table-wrap spp-vent">
        <table>
          <thead><tr><th>Fondo</th>{cols.map((c) => <th key={c[0]}>{c[1]}</th>)}</tr></thead>
          <tbody>
            {(orden || ordenRel).map((afp) => {
              const suyas = filas.filter((r) => r.afp === afp);
              if (!suyas.length) return null;
              const esCasa = afp === casa;
              const nota = suyas[0].absoluto === true ? ' · rendimiento absoluto' : '';
              return [
                <tr key={`${afp}-h`} className="spp-seccion">
                  <td colSpan={cols.length + 1}
                    className={esCasa ? '' : 'muted'}
                    style={esCasa ? { color: colorDe(cfg, afp, true) } : undefined}>
                    {afp}{nota}</td>
                </tr>,
                ...suyas.map((r) => (
                  <tr key={`${afp}-${r.fondo}`}>
                    <td>Fondo {r.fondo}</td>
                    {cols.map((c) => {
                      const unidad = conUnidad ? c[2] : 'nivel';
                      const v = r.valores[c[0]];
                      const clase = unidad === 'nivel' ? 'num' : `num ${signo(valorMostrado(v, unidad))}`;
                      return <td key={c[0]} className={v == null ? 'num dim' : clase}>{fmtRend(v, unidad, metrica)}</td>;
                    })}
                  </tr>
                )),
              ];
            })}
          </tbody>
        </table>
      </div>
    );
  }

  // ---- Posiciones --------------------------------------------------------
  const periodos = pos?.periodos || [];
  const compitenPos = nombres.filter((a) => opera(a, fondoPos));
  const totalPos = Math.max(2, compitenPos.length);
  const trazasPos = useMemo(() => nombres.map((afp) => {
    const fila = (pos?.filas || []).find((r) => r.fondo === fondoPos && r.afp === afp);
    if (!fila) return null;
    const xs = []; const ys = [];
    periodos.forEach((p) => {
      const q = fila.puestos[p.clave];
      if (q) { xs.push(p.etiqueta); ys.push(q); }
    });
    if (!xs.length) return null;
    const esCasa = afp === casa;
    // Same house-vs-grays scheme as the historic series chart.
    const color = tintaDe(afp, compitenPos);
    return {
      x: xs, y: ys, type: 'scatter', mode: 'lines+markers', name: afp,
      legendrank: ordenCasa(cfg, compitenPos).indexOf(afp) + 1,
      line: { color, width: esCasa ? 3.2 : 2 },
      marker: { size: esCasa ? 9 : 7 },
      hovertemplate: `<b>${afp}</b> · %{x}: puesto %{y}<extra></extra>`,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }).filter(Boolean), [pos, fondoPos, cfg, casa, nombres, periodos]);

  return (
    <div>
      <h1 className="page-title">Valor Cuota SPP</h1>
      <p className="page-sub">
        Valor cuota diario del SPP · fuente SBS
        {estado?.filas ? ` · ${nEnt(estado.filas)} fechas (${fFecha(estado.desde)} a ${fFecha(estado.hasta)})` : ''}
      </p>
      <SppTabs />

      {/* Un error se muestra COMO BANDA, no reemplazando la página: antes se
          llevaba por delante los controles con los que el operador podría
          corregir la selección que lo causó, y nada lo limpiaba salvo F5. */}
      {error && <div className="panel error">Error: {error}</div>}

      <div className="panel">
        <div className="controls spp-controls">
          <div className="field"><label>Tipo de fondo</label>
            <SppSeg items={(cfg?.fondos || []).map((f) => [`Fondo ${f}`, f])}
              value={fondo}
              onChange={(v) => {
                setFondo(v);
                const validas = afpsSel.filter((a) => opera(a, v));
                setAfpsSel(validas.length ? validas : nombres.filter((a) => opera(a, v)));
              }} /></div>
          <div className="field"><label>Ventana</label>
            <SppSeg items={VENTANAS} value={ventana} onChange={setVentana} /></div>
          <div className="field"><label>Escala</label>
            <SppSeg items={[['Nivel', 'nivel'], ['Base 100', 'base']]}
              value={escala} onChange={setEscala} /></div>
          <div className="field"><label>AFP en pantalla</label>
            <SppSeg multi items={nombres.filter((a) => opera(a, fondo)).map((a) => [a, a])}
              value={afpsSel}
              // Sobre la lista COMPLETA del fondo, no sobre la seleccion: asi
              // el tono de cada AFP no cambia al encender o apagar a otra.
              colorOf={(a) => tintaDe(a, nombres.filter((x) => opera(x, fondo)))}
              onChange={(v) => setAfpsSel((prev) => {
                const next = prev.includes(v) ? prev.filter((x) => x !== v) : [...prev, v];
                return next.length ? next : [v];
              })} /></div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">
          Serie histórica · {nombreMetrica(cfg, metrica).toLowerCase()}
          {escala === 'base' ? ' · base 100' : ''}
        </div>
        {traces.length ? (
          <PlotlyChart
            data={traces}
            layout={{
              margin: { l: 70, r: 20, t: 10, b: 60 },
              xaxis: { hoverformat: '%Y-%m-%d' },
              yaxis: {
                type: 'linear',
                title: escala === 'base' ? 'Base 100' : nombreMetrica(cfg, metrica),
                zeroline: false,
              },
            }}
          />
        ) : serieData == null
          ? <div className="loading">Cargando…</div>
          : <p className="page-sub dim">Sin datos para esta selección.</p>}
        {escala === 'base' && (
          <p className="page-sub" style={{ marginTop: 8 }}>
            En Base 100 las series parten de 100 en la primera fecha con dato en todas
            las AFP en pantalla: es la única lectura comparable, porque cada fondo
            arrancó en fechas y bases distintas.
          </p>
        )}
      </div>

      <div className="panel">
        <div className="panel-title">Cierre por AFP · Fondo {fondo}</div>
        <div className="table-wrap">
          <table>
            <thead><tr>
              <th>AFP</th><th className="num">Valor cuota</th><th className="num">Var. diaria</th>
              <th className="num">Cuotas</th><th className="num">Fondo S/</th><th className="num">Desde</th>
            </tr></thead>
            <tbody>
              {cierres.length ? cierres.map((s) => (
                <tr key={s.afp}>
                  <td><span className="spp-chip"
                    style={{ background: tintaDe(s.afp, cierres.map((x) => x.afp)) }} />
                    <b style={{ color: s.afp === casa ? colorDe(cfg, s.afp, true) : 'inherit' }}>
                      {s.afp}</b></td>
                  <td className="num">{nf(s.valor)}</td>
                  <td className={`num ${signo(s.var_bps)}`}>
                    {s.var_bps == null ? '—' : `${s.var_bps >= 0 ? '+' : ''}${s.var_bps} bps`}</td>
                  <td className="num">{fmtMetrica(s.cuotas, 'cuotas', true)}</td>
                  <td className="num">{fmtMetrica(s.fondo_soles, 'fondo', true)}</td>
                  <td className="num">{s.inicio?.slice(0, 4)}</td>
                </tr>
              )) : <tr><td colSpan={6} className="dim">Sin datos para esta selección.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* The filter bar governs everything ABOVE this line. The windows and
          the ranks below carry their own controls (control date, fund), and
          without a visible cut the reader keeps changing the top filters and
          wondering why the tables do not move. */}
      <div className="spp-corte" role="separator">
        <span>Los filtros de arriba mandan hasta aquí · lo que sigue tiene sus propios controles</span>
      </div>

      <div className="panel">
        <div className="controls" style={{ justifyContent: 'space-between' }}>
          <div>
            <div className="panel-title">Ventanas de rendimiento</div>
            {vent?.control && (
              <p className="page-sub" style={{ margin: '4px 0 0' }}>
                Control {fFecha(vent.fechas.t)} · inicio de mes {fFecha(vent.fechas.mes)} ·
                M-1 {fFecha(vent.fechas.mes1)} · inicio de año {fFecha(vent.fechas.anio)} ·
                inicio de ejercicio {fFecha(vent.fechas.fy)}
              </p>
            )}
          </div>
          <div className="field"><label>Fecha de control</label>
            <input className="date-input" type="date" value={fechaControl}
              onChange={(e) => setFechaControl(e.target.value)} /></div>
        </div>

        {vent?.control ? (
          <>
            <details className="spp-desplegable" open>
              <summary>Rendimiento relativo · {casa} contra cada competidora</summary>
              <TablaVentanas cols={(vent.cols_rend || []).filter((c) => c[2] !== 'nivel')}
                filas={vent.relativos} conUnidad orden={ordenRel} />
              <p className="page-sub">Cada sección es {casa} menos esa AFP: positivo significa que
                {' '}{casa} rinde más. La sección de {casa} va en rendimiento absoluto.</p>
            </details>
            <details className="spp-desplegable">
              <summary>Rendimiento absoluto</summary>
              <TablaVentanas cols={vent.cols_rend} filas={vent.absolutos} conUnidad />
              <p className="page-sub">Hasta meses en puntos básicos; FY, YTD y año pasado en
                porcentaje. FY es el ejercicio, del 31/10 al 31/10.
                Las columnas diarias son el movimiento de ese día, no acumulados.</p>
            </details>
            <details className="spp-desplegable">
              <summary>Valor cuota en cada fecha base</summary>
              <TablaVentanas cols={vent.cols_nivel} filas={vent.niveles} conUnidad={false} />
            </details>
          </>
        ) : vent == null
          ? <div className="loading">Cargando…</div>
          : <div className="page-sub dim">Sin datos en el libro.</div>}
      </div>

      <div className="panel">
        <div className="controls" style={{ justifyContent: 'space-between' }}>
          <div className="panel-title">Posiciones mensuales por rendimiento</div>
          <div className="controls" style={{ margin: 0 }}>
            <div className="field"><label>Tipo de fondo</label>
              <SppSeg items={(cfg?.fondos || []).map((f) => [`Fondo ${f}`, f])}
                value={fondoPos} onChange={setFondoPos} /></div>
            <div className="field"><label>Vista</label>
              <SppSeg items={[['Tabla', 'tabla'], ['Gráfico', 'grafico']]}
                value={vistaPos} onChange={setVistaPos} /></div>
          </div>
        </div>
        {/* One space, two readings of the same ranks: the heat table says
            WHO was where each month; the lines say how each one MOVED.
            Both at once said it twice, and the panel was two screens tall. */}
        {vistaPos === 'grafico' && (trazasPos.length ? (
          <PlotlyChart
            data={trazasPos}
            style={{ height: '300px' }}
            layout={{
              margin: { l: 50, r: 20, t: 10, b: 50 },
              yaxis: {
                autorange: 'reversed', dtick: 1, range: [totalPos + 0.5, 0.5],
                title: 'Puesto', zeroline: false,
              },
              xaxis: { type: 'category' },
            }}
          />
        ) : <div className="dim">Sin meses cerrados suficientes.</div>)}
        {vistaPos === 'tabla' && (
        <div className="table-wrap spp-vent">
          <table>
            <thead><tr><th>AFP</th>{periodos.map((p) => <th key={p.clave}>{p.etiqueta}</th>)}</tr></thead>
            <tbody>
              {/* House row first (it is the question this table answers),
                  heat in its brand ramp; competitors share ONE gray ramp so
                  intensity reads as rank everywhere, not as identity. */}
              {ordenCasa(cfg, compitenPos).map((afp) => {
                const fila = (pos?.filas || []).find((r) => r.fondo === fondoPos && r.afp === afp);
                if (!fila) return null;
                const esCasa = afp === casa;
                const base = esCasa ? colorDe(cfg, afp, true) : GRIS_COMPETIDOR;
                return (
                  <tr key={afp}>
                    <td><span className="spp-chip"
                      style={{ background: tintaDe(afp, compitenPos) }} />
                      <b style={{ color: esCasa ? colorDe(cfg, afp, true) : 'inherit' }}>{afp}</b></td>
                    {periodos.map((p) => {
                      const q = fila.puestos[p.clave];
                      if (!q) return <td key={p.clave} className="num dim">—</td>;
                      const t = totalPos < 2 ? 1 : 1 - (q - 1) / (totalPos - 1);
                      const pct = Math.round(7 + 93 * t);
                      return (
                        <td key={p.clave} className="num spp-puesto"
                          style={{
                            background: `color-mix(in srgb, ${base} ${pct}%, transparent)`,
                            color: pct >= 60 ? '#fff' : 'inherit',
                            fontWeight: q === 1 ? 700 : 500,
                          }}>{q}</td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        )}
        <p className="page-sub">Puesto 1 al {totalPos} por rendimiento del mes, dentro de cada tipo
          de fondo{vistaPos === 'tabla' ? ' — más intenso = mejor puesto; la fila en color es ' : '; la línea gruesa es '}
          {casa}. Cada mes cerrado rinde contra el cierre del mes anterior; el último periodo es el MTD.</p>
      </div>
    </div>
  );
}
