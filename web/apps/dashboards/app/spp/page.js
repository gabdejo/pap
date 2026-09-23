
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
  GRIS_COMPETIDOR, VENTANAS, alphaDe, colorDe, desdeVentana, fFecha, fmtRend,
  grisLinea, nEnt, nombreMetrica, opera as operaCfg, ordenCasa, signo,
  valorMostrado,
} from '../../lib/spp';
import SppSeg from '../../components/SppSeg';
import SppTabs from '../../components/SppTabs';
import DescargaDatos from '../../components/DescargaDatos';
import { generarReporte } from './reporte';
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
  // La fecha de control manda en TODO el panel: grafico, ventanas y
  // posiciones. Nace en la ultima fecha con valor cuota COMPLETO (todas las
  // AFP y fondos publicados); cuotas y fondo no cuentan para eso.
  const [fechaControl, setFechaControl] = useState('');
  const [generando, setGenerando] = useState(false);
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

  useEffect(() => {
    if (estado?.hasta_completa && !fechaControl) setFechaControl(estado.hasta_completa);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estado?.hasta_completa]);
  const opera = (afp, f) => operaCfg(cfg, afp, f);

  // Historic series follows the metric/fund/window/AFP selection - and the
  // reference dates, which is what MTD/YTD actually start from. Leaving
  // vent?.fechas out of the deps meant the chart kept the previous month's
  // base after a change of control date, disagreeing with the windows
  // tables right beside it, and never adopted the true prior close the
  // first time it loaded (it used the calendar fallback instead).
  useEffect(() => {
    if (!cfg || !afpsSel.length || !fechaControl) return;
    setError(null);
    // Las ventanas de años (1A, 3A...) cuentan hacia atras desde la fecha
    // de control, no desde el ultimo dato del libro.
    const desde = desdeVentana(ventana, { hasta: fechaControl }, vent?.fechas);
    // En Alpha la casa es el punto de referencia de cada linea: se pide
    // siempre, aunque el operador la haya apagado en "AFP en pantalla".
    const afps = (escala === 'alpha' && casa && !afpsSel.includes(casa))
      ? [casa, ...afpsSel] : afpsSel;
    const q = new URLSearchParams({
      fondo: String(fondo), afps: afps.join(','), metrica, desde, hasta: fechaControl,
    });
    apiGet(`/api/spp/serie?${q}`).then(setSerieData).catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg, fechaControl, metrica, fondo, ventana, afpsSel, escala === 'alpha',
      vent?.fechas?.mes, vent?.fechas?.anio, vent?.fechas?.fy]);

  // Windows + positions follow metric and control date.
  useEffect(() => {
    if (!cfg || !fechaControl) return;
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

  // Lo que dice la etiqueta al pasar el cursor: por AFP, cuanto lleva desde
  // el inicio de la ventana y cuanto se movio ese dia, cada cifra en el
  // color de su signo. Van como texto ya armado en customdata porque el
  // color depende del valor y la plantilla del hover no sabe de condiciones.
  const conSigno = (v, fmt) => {
    if (v == null || !Number.isFinite(v)) return '<span style="opacity:0.6">—</span>';
    const t = chartTheme();
    const color = v > 0 ? t.positive : v < 0 ? t.negative : t.muted;
    return `<span style="color:${color}">${fmt(v)}</span>`;
  };
  const pct = (v) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`;
  const bps = (v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)} bps`;
  const acumYDiaria = (ys, fmt, acumDe) => ys.map((y, i) => [
    conSigno(acumDe(y), fmt),
    conSigno(i > 0 ? y - ys[i - 1] : null, fmt),
  ]);

  // Alpha: cuanto le ha ganado la casa a CADA competidora desde el inicio
  // de la ventana, en puntos basicos. Es la resta de las dos curvas de
  // Base 100 - casa menos la otra - asi que positivo es la casa delante y
  // la pendiente de cada dia es lo que ese dia abrio o cerro la brecha.
  // Una linea por competidora, ninguna para la casa: la casa es el cero.
  const trazasAlpha = () => {
    const conDatos = series.filter((s) => s.puntos.length);
    const enPantalla = conDatos.map((s) => s.afp);
    // Sin linea de casa, los grises ya no dicen "esta no es la casa": son
    // lineas iguales. Misma tinta, pero los tonos abiertos de punta a punta
    // (el reparto normal los junta para que la casa destaque), y todas
    // continuas: los trazos a rayas se leian como datos con huecos.
    const orden = ordenCasa(cfg, enPantalla).filter((a) => a !== casa);
    const tonoDe = (afp) => {
      const i = orden.indexOf(afp);
      const a = orden.length < 2 ? 0.9 : 0.95 - (0.6 * i) / (orden.length - 1);
      return grisLinea(a.toFixed(2));
    };
    return alphaDe(series, casa).map((c) => {
      const color = tonoDe(c.afp);
      return {
        // Solo la AFP: el titulo ya dice que todo es contra la casa.
        x: c.x, y: c.y, type: 'scatter', mode: 'lines', name: c.afp,
        legendrank: ordenCasa(cfg, enPantalla).indexOf(c.afp) + 1,
        line: { color, width: 2 },
        // En bps el acumulado ES el valor de la linea; el diario, su paso.
        customdata: acumYDiaria(c.y, bps, (y) => y),
        hovertemplate: `<b>${c.afp}</b><br>Alpha acumulado  %{customdata[0]}`
          + '<br>Del día  %{customdata[1]}<extra></extra>',
        hoverlabel: { bordercolor: color },
      };
    });
  };

  const traces = useMemo(() => {
    if (escala === 'alpha') return trazasAlpha();
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
      // Rentabilidad acumulada desde el primer punto en pantalla y variacion
      // del dia, sobre el valor cuota crudo: es lo mismo en Nivel y Base 100.
      const crudos = pts.map((p) => p[1]);
      const inicio = crudos[0];
      const rent = crudos.map((v, i) => [
        conSigno(inicio ? v / inicio - 1 : null, pct),
        conSigno(i > 0 && crudos[i - 1] ? v / crudos[i - 1] - 1 : null, pct),
      ]);
      return {
        x: pts.map((p) => p[0]),
        y: pts.map((p) => (base ? (p[1] / base) * 100 : p[1])),
        type: 'scatter', mode: 'lines', name: s.afp,
        // legendrank puts the house first in the LEGEND while the trace
        // order (house last) keeps its line drawn on top of the grays.
        legendrank: ordenCasa(cfg, enPantalla).indexOf(s.afp) + 1,
        line: { color, width: esCasa ? 2.8 : 1.6 },
        customdata: rent,
        hovertemplate: `<b>${s.afp}</b><br>Rent. acumulada  %{customdata[0]}`
          + '<br>Rent. diaria  %{customdata[1]}<extra></extra>',
        hoverlabel: { bordercolor: color },
      };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series, escala, cfg, casa]);

  const ct = chartTheme();

  const reportePdf = async () => {
    if (!cfg || !vent?.control) return;
    setGenerando(true); setError(null);
    try {
      await generarReporte({ cfg, vent });
    } catch (e) {
      setError(`No se pudo generar el reporte: ${e.message}`);
    } finally {
      setGenerando(false);
    }
  };

  const etiquetaVentana = (VENTANAS.find(([, v]) => v === ventana) || ['—'])[0];
  // Titulo corto - el lector ya sabe que es la serie del valor cuota - y
  // al pie del grafico en que esta expresado. La ventana y el resto de la
  // seleccion estan a la vista en la barra de arriba.
  const tituloSerie = escala === 'alpha' ? `Alpha F${fondo}` : `Evolución F${fondo}`;
  const pieSerie = escala === 'alpha'
    ? `Expresado en puntos básicos: ${casa} menos cada AFP`
    : escala === 'base' ? 'Expresado en base 100'
      : `Expresado en ${nombreMetrica(cfg, metrica).toLowerCase()}`;
  // "Evolución F2 · Expresado en base 100 · 1A" -> un nombre de archivo
  const archivoDe = (t) => t.toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  // ---- Ventanas tables ---------------------------------------------------
  const ordenRel = ordenCasa(cfg, nombres);

  function TablaVentanas({ cols, filas, conUnidad, orden }) {
    // Un bloque por FONDO y dentro una fila por AFP: la pregunta de la mesa
    // es "en el fondo 2, como vamos contra cada una", y asi las cuatro
    // respuestas quedan juntas. El fondo va en una columna a la izquierda,
    // una celda que abarca sus filas (rowSpan), no en una fila propia: cada
    // fila de titulo era una linea menos de datos en pantalla. La casa va
    // primero y en su color; las competidoras, neutras.
    const ordenAfp = orden || ordenRel;
    const fondos = (cfg?.fondos || []).filter((f) => filas.some((r) => r.fondo === f));
    return (
      <div className="table-wrap spp-vent">
        <table>
          <thead><tr><th>Fondo</th><th className="spp-afp">AFP</th>{cols.map((c) => <th key={c[0]}>{c[1]}</th>)}</tr></thead>
          <tbody>
            {fondos.map((f) => {
              const delFondo = filas.filter((r) => r.fondo === f)
                .sort((x, y) => ordenAfp.indexOf(x.afp) - ordenAfp.indexOf(y.afp));
              return delFondo.map((r, i) => {
                  const esCasa = r.afp === casa;
                  // La fila de la casa va en negrita ENTERA - nombre y cifras -
                  // conservando el color de signo de cada cifra: destaca por
                  // peso, no por color.
                  return (
                    <tr key={`f${f}-${r.afp}`}
                      className={`${i === 0 ? 'spp-bloque' : ''} ${esCasa ? 'spp-casa' : ''}`.trim()}>
                      {i === 0 && (
                        <td rowSpan={delFondo.length} className="spp-fondo">Fondo {f}</td>
                      )}
                      {/* Con el fondo delante, esta ya no es la primera celda de la
                          fila y la hoja global la alinearia a la derecha. */}
                      <td className="spp-afp"
                        style={esCasa ? { color: colorDe(cfg, r.afp, true) } : undefined}>
                        {r.afp}</td>
                      {cols.map((c) => {
                        const unidad = conUnidad ? c[2] : 'nivel';
                        const v = r.valores[c[0]];
                        const clase = unidad === 'nivel' ? 'num' : `num ${signo(valorMostrado(v, unidad))}`;
                        return <td key={c[0]} className={v == null ? 'num dim' : clase}>{fmtRend(v, unidad, metrica)}</td>;
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
      <div className="spp-fila-tabs">
        <SppTabs />
        {/* El reporte PDF: un solo icono, a la derecha de las pestanas. Es lo
            que se lleva de la pagina, no un filtro; el texto va en el tooltip
            y en aria-label. Tabla relativa + alpha YTD/FY por fondo, a la
            fecha de control, armado en el navegador. */}
        <button className="btn principal spp-icono" onClick={reportePdf}
          disabled={generando || !vent?.control}
          aria-label="Descargar el reporte de rentabilidad en PDF"
          title={generando ? 'Generando el reporte…'
            : 'Reporte de rentabilidad (PDF): tabla relativa + alpha YTD y FY de cada fondo, a la fecha de control'}>
          {generando ? (
            <span className="spp-girando">…</span>
          ) : (
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
              strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M8 2v8" /><path d="M4.5 6.5 8 10l3.5-3.5" /><path d="M2.5 12.5v1h11v-1" />
            </svg>
          )}
        </button>
      </div>

      {/* Un error se muestra COMO BANDA, no reemplazando la página: antes se
          llevaba por delante los controles con los que el operador podría
          corregir la selección que lo causó, y nada lo limpiaba salvo F5. */}
      {error && <div className="panel error">Error: {error}</div>}

      <div className="panel">
        <div className="controls spp-controls">
          <div className="field"><label>Fecha de control</label>
            <input className="date-input" type="date" value={fechaControl}
              max={estado?.hasta || undefined}
              onChange={(e) => e.target.value && setFechaControl(e.target.value)} /></div>
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
            <SppSeg items={[['Nivel', 'nivel'], ['Base 100', 'base'], ['Alpha', 'alpha']]}
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
        {/* El titulo dice lo que hay en pantalla - escala, fondo y ventana -
            porque los filtros quedan arriba y el grafico se mira solo. */}
        <div className="spp-cabecera-grafico">
          <span />
          <div className="panel-title" style={{ margin: 0 }}>{tituloSerie}</div>
          {/* El CSV lleva la vista tal cual: nivel, base 100 o alpha en bps.
              El nombre del archivo repite el titulo, que es lo que se lee
              cuando el archivo aparece dias despues en una carpeta. */}
          <DescargaDatos trazas={traces}
            nombre={archivoDe(`${tituloSerie} · ${pieSerie} · ${etiquetaVentana}`)} />
        </div>
        {traces.length ? (
          <PlotlyChart
            data={traces}
            layout={{
              // " bps" alarga las etiquetas del eje: sin el margen extra el
              // signo menos se recorta y -200 se lee como 200.
              margin: { l: escala === 'alpha' ? 95 : 70, r: 20, t: 10, b: 60 },
              // Una sola etiqueta por fecha con un bloque por AFP, como la
              // tarjeta del visor de precios: la fecha arriba y, debajo,
              // cada AFP con su acumulado y su dia.
              hovermode: 'x unified',
              xaxis: { hoverformat: '%d %b %Y' },
              yaxis: {
                type: 'linear',
                title: escala === 'alpha' ? 'Alpha acumulado (bps)'
                  : escala === 'base' ? 'Base 100' : nombreMetrica(cfg, metrica),
                // En Alpha el cero ES la casa: la linea que lo marca es la
                // referencia de lectura, no ruido.
                zeroline: escala === 'alpha',
                zerolinecolor: ct.muted, zerolinewidth: 1.5,
                ticksuffix: escala === 'alpha' ? ' bps' : '',
              },
            }}
          />
        ) : serieData == null
          ? <div className="loading">Cargando…</div>
          : <p className="page-sub dim">Sin datos para esta selección.</p>}
        {traces.length > 0 && (
          <p className="page-sub dim" style={{ textAlign: 'center', margin: '6px 0 0' }}>{pieSerie}</p>
        )}
      </div>

      {/* The filter bar governs everything ABOVE this line. The windows and
          the ranks below carry their own controls (control date, fund), and
          without a visible cut the reader keeps changing the top filters and
          wondering why the tables do not move. */}
      <div className="spp-corte" role="separator">
        <span>Fondo, ventana, escala y AFP mandan hasta aquí · la fecha de control manda en todo el panel</span>
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
        </div>

        {vent?.control ? (
          <>
            <details className="spp-desplegable" open>
              <summary>Rendimiento relativo · {casa} contra cada competidora</summary>
              <TablaVentanas cols={(vent.cols_rend || []).filter((c) => c[2] !== 'nivel')}
                filas={vent.relativos} conUnidad orden={ordenRel} />
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
        <div className="controls" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
          <div className="panel-title">Posiciones mensuales por rendimiento</div>
          <div className="controls" style={{ margin: 0 }}>
            <div className="field"><label>Tipo de fondo</label>
              <SppSeg items={(cfg?.fondos || []).map((f) => [`Fondo ${f}`, f])}
                value={fondoPos} onChange={setFondoPos} /></div>
            <div className="field"><label>Vista</label>
              <SppSeg items={[['Tabla', 'tabla'], ['Gráfico', 'grafico']]}
                value={vistaPos} onChange={setVistaPos} /></div>
            <div className="field"><label aria-hidden="true">&nbsp;</label>
              {/* Tabla y grafico son los mismos puestos: un solo CSV para ambas. */}
              <DescargaDatos trazas={trazasPos} ejeX="periodo"
                nombre={`posiciones-fondo-${fondoPos}`} /></div>
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
