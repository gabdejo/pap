// web/apps/dashboards/app/tradebook/page.js
// ---------------------------------------------------------------------------
// Tradebook · Panel: la actividad agregada. Cuánto se operó por periodo, y
// cómo se reparte entre fondos, monedas, contrapartes e instrumentos.
//
// Los montos NO se convierten entre monedas. No hay tipo de cambio en este
// almacén, y sumar soles con dólares porque ambos son números produce
// justamente el total que parece correcto y no lo es. Por eso la moneda es
// un filtro de primera clase y la pantalla dice siempre cuál está mirando.
// ---------------------------------------------------------------------------
'use client';

import { useEffect, useMemo, useState } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';

import SppSeg from '../../components/SppSeg';
import TradebookTabs from '../../components/TradebookTabs';
import useVerTodo from '../../components/useVerTodo';
import { apiGet } from '../../lib/api';
import { fFecha, nEnt } from '../../lib/spp';
import { chartTheme, useThemeVersion } from '../../lib/theme';

// plotly.js toca `window` al cargarse, asi que este import NO puede ser
// estatico: el export estatico prerenderiza esta pagina en Node y revienta
// con "self is not defined".
const PlotlyChart = dynamic(() => import('../../components/PlotlyChart'), { ssr: false });

const PERIODOS = [
  ['Día', 'dia'], ['Semana', 'semana'], ['Mes', 'mes'],
  ['Trimestre', 'trimestre'], ['Año', 'anio'],
];
const LADOS = [['Ambos', ''], ['Compras', 'compra'], ['Ventas', 'venta']];
// Las dos vias con que se llena el libro. NO hay opcion de verlas juntas, y
// eso es a proposito: son las mismas operaciones a distinto nivel de
// agregacion, asi que sumarlas contaria cada una dos veces. Siempre hay una
// elegida; lo que se elige es el nivel de detalle con que se mira.
const LIBROS = [['De traders', 'traders'], ['De FMS', 'fms']];

// Moneda de la casa: es la que se mira por defecto cuando el libro trae
// varias, para que el panel abra diciendo algo y no una suma imposible.
const MONEDA_CASA = 'PEN';

const fMonto = (v, moneda) => {
  if (v == null) return '—';
  const abs = Math.abs(v);
  const [n, sufijo] = abs >= 1e9 ? [v / 1e9, ' MM'] : abs >= 1e6 ? [v / 1e6, ' M'] : [v, ''];
  return `${n.toLocaleString('es-PE', { minimumFractionDigits: sufijo ? 2 : 0,
    maximumFractionDigits: sufijo ? 2 : 0 })}${sufijo}${moneda ? ` ${moneda}` : ''}`;
};

export default function TradebookPanel() {
  const [estado, setEstado] = useState(null);
  const [resumen, setResumen] = useState(null);
  const [error, setError] = useState(null);

  const [periodo, setPeriodo] = useState('mes');
  const [fondo, setFondo] = useState('');
  const [lado, setLado] = useState('');
  const [moneda, setMoneda] = useState('');
  // Arranca en el registro de traders, que es el detallado. Si no hay nada
  // cargado por esa via, el efecto de abajo lo pasa a FMS: abrir en un libro
  // vacio teniendo datos en el otro haria pensar que no hay nada.
  const [libro, setLibro] = useState('traders');
  const [trader, setTrader] = useState('');
  const [desde, setDesde] = useState('');
  const [hasta, setHasta] = useState('');

  // El rango inicial lo decide el backend sobre lo que HAY en el libro, no
  // sobre la fecha de hoy: un libro que se dejó de cargar en marzo abriría
  // vacío, y el operador leería "sin operaciones" teniendo datos.
  useEffect(() => {
    apiGet('/api/tradebook/estado').then((e) => {
      setEstado(e);
      setDesde(e.rango?.desde || '');
      setHasta(e.rango?.hasta || '');
      if (e.monedas?.length) {
        setMoneda(e.monedas.includes(MONEDA_CASA) ? MONEDA_CASA : e.monedas[0]);
      }
      const deTraders = (e.origenes?.excel || 0) + (e.origenes?.manual || 0);
      if (!deTraders && (e.origenes?.fms || 0) > 0) setLibro('fms');
    }).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!desde || !hasta) return;
    const q = new URLSearchParams({ desde, hasta, periodo });
    if (fondo !== '') q.set('fondo', String(fondo));
    if (lado) q.set('lado', lado);
    if (moneda) q.set('moneda', moneda);
    if (libro) q.set('origen', libro);
    if (trader) q.set('trader', trader);
    apiGet(`/api/tradebook/resumen?${q}`).then(setResumen).catch((e) => setError(e.message));
  }, [desde, hasta, periodo, fondo, lado, moneda, libro, trader]);

  // El color va resuelto, no como var(--x): Plotly no lee variables CSS y se
  // queda con su paleta. `version` lo recalcula al cambiar de tema.
  const version = useThemeVersion();

  // Una barra por lado, apiladas: el alto total es lo operado y el reparto
  // dice de qué fue. Separarlas en dos gráficos obligaría a sumarlas a ojo.
  const traces = useMemo(() => {
    const t = chartTheme();
    const filas = (resumen?.series || []).filter((s) => !moneda || s.moneda === moneda);
    if (!filas.length) return [];
    const periodos = [...new Set(filas.map((s) => s.periodo))].sort();
    return ['compra', 'venta'].map((l) => {
      const porPeriodo = Object.fromEntries(
        filas.filter((s) => s.lado === l).map((s) => [s.periodo, s.monto]));
      return {
        x: periodos,
        y: periodos.map((p) => porPeriodo[p] || 0),
        type: 'bar',
        name: l === 'compra' ? 'Compras' : 'Ventas',
        marker: { color: l === 'compra' ? t.positive : t.negative },
        hovertemplate: `<b>%{x}</b> · ${l}<br>%{y:,.0f} ${moneda || ''}<extra></extra>`,
      };
    }).filter((t) => t.y.some((v) => v));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumen, moneda, version]);

  const total = resumen?.total || {};
  const montoDe = (l) => (total.monedas || [])
    .filter((m) => (!moneda || m.moneda === moneda) && (!l || m.lado === l))
    .reduce((a, m) => a + m.monto, 0);

  const [contrapartes, VerContrapartes] = useVerTodo(
    (resumen?.por_contraparte || []).filter((f) => !moneda || f.moneda === moneda), 10);
  const [instrumentos, VerInstrumentos] = useVerTodo(
    (resumen?.por_instrumento || []).filter((f) => !moneda || f.moneda === moneda), 10);

  const vacio = estado && !estado.filas;

  return (
    <div>
      <h1 className="page-title">Tradebook</h1>
      <p className="page-sub">
        Operaciones de la mesa
        {estado?.filas
          ? ` · ${nEnt(estado.filas)} operaciones (${fFecha(estado.desde)} a ${fFecha(estado.hasta)})`
          : ''}
      </p>
      <TradebookTabs />

      {error && <div className="panel error">Error: {error}</div>}

      {vacio ? (
        <div className="panel">
          <div className="panel-title">El libro está vacío</div>
          <p className="page-sub">
            Todavía no hay operaciones cargadas. Se cargan en{' '}
            <Link href="/tradebook/carga/" style={{ color: 'var(--brand)' }}>
              Registro y carga</Link>: por archivo, a mano, o derivándolas de las
            posiciones ya cargadas.
          </p>
        </div>
      ) : (
        <>
          <div className="panel">
            <div className="controls spp-controls">
              <div className="field"><label>Desde</label>
                <input className="date-input" type="date" value={desde}
                  onChange={(e) => setDesde(e.target.value)} /></div>
              <div className="field"><label>Hasta</label>
                <input className="date-input" type="date" value={hasta}
                  onChange={(e) => setHasta(e.target.value)} /></div>
              <div className="field"><label>Agrupar por</label>
                <SppSeg items={PERIODOS} value={periodo} onChange={setPeriodo} /></div>
              <div className="field"><label>Lado</label>
                <SppSeg items={LADOS} value={lado} onChange={setLado} /></div>
              <div className="field"><label>Fondo</label>
                <SppSeg items={[['Todos', ''], ...(estado?.fondos || []).map((f) => [`Fondo ${f}`, f])]}
                  value={fondo} onChange={setFondo} /></div>
              {(estado?.monedas || []).length > 1 && (
                <div className="field"><label>Moneda</label>
                  <SppSeg items={(estado.monedas || []).map((m) => [m, m])}
                    value={moneda} onChange={setMoneda} /></div>
              )}
              <div className="field"><label>Libro</label>
                <SppSeg items={LIBROS} value={libro} onChange={(v) => {
                  setLibro(v);
                  // FMS no tiene traders: dejar el filtro puesto al cambiar
                  // de libro dejaria el panel vacio sin decir por que.
                  if (v === 'fms') setTrader('');
                }} /></div>
              {libro !== 'fms' && (estado?.traders || []).length > 0 && (
                <div className="field"><label>Book</label>
                  <SppSeg items={[['Todos', ''], ...(estado.traders || []).map((t) => [t, t])]}
                    value={trader} onChange={setTrader} /></div>
              )}
            </div>
            {/* Decir la moneda aquí no es decorativo: sin ella las cifras de
                abajo son ambiguas, y con varias cargadas serían engañosas. */}
            <p className="page-sub dim" style={{ marginTop: 4 }}>
              Cifras en {moneda || 'todas las monedas'}. Los montos no se
              convierten entre monedas. Las cifras son del{' '}
              {libro === 'fms' ? 'reporte de FMS' : 'registro de los traders'}:
              las dos vías traen las mismas operaciones a distinto nivel de
              detalle, y por eso no se suman.
            </p>
          </div>

          <div className="tb-totales">
            <div className="tb-total">
              <div className="rotulo">Operaciones</div>
              <div className="cifra">{nEnt(total.operaciones || 0)}</div>
              <div className="pie">{nEnt(total.compras || 0)} compras · {nEnt(total.ventas || 0)} ventas</div>
            </div>
            <div className="tb-total">
              <div className="rotulo">Comprado</div>
              <div className="cifra pos">{fMonto(montoDe('compra'), moneda)}</div>
              <div className="pie">en el rango en pantalla</div>
            </div>
            <div className="tb-total">
              <div className="rotulo">Vendido</div>
              <div className="cifra neg">{fMonto(montoDe('venta'), moneda)}</div>
              <div className="pie">en el rango en pantalla</div>
            </div>
            <div className="tb-total">
              <div className="rotulo">Neto</div>
              <div className="cifra">{fMonto(montoDe('compra') - montoDe('venta'), moneda)}</div>
              <div className="pie">comprado menos vendido</div>
            </div>
            <div className="tb-total">
              <div className="rotulo">Contrapartes</div>
              <div className="cifra">{nEnt(total.contrapartes || 0)}</div>
              <div className="pie">{nEnt(total.instrumentos || 0)} instrumentos</div>
            </div>
            {libro !== 'fms' && (
              <div className="tb-total">
                <div className="rotulo">Traders</div>
                <div className="cifra">{nEnt(total.traders || 0)}</div>
                <div className="pie">con operaciones en el rango</div>
              </div>
            )}
          </div>

          <div className="panel">
            <div className="panel-title">Volumen operado · por {periodo}</div>
            {traces.length ? (
              <PlotlyChart
                data={traces}
                layout={{
                  barmode: 'stack',
                  margin: { l: 80, r: 20, t: 10, b: 50 },
                  xaxis: { type: 'category' },
                  yaxis: { title: moneda || '', zeroline: false },
                  legend: { orientation: 'h', y: 1.1 },
                }}
              />
            ) : resumen == null
              ? <div className="loading">Cargando…</div>
              : <p className="page-sub dim">Sin operaciones en este rango.</p>}
          </div>

          <div className="spp-dos">
            <Composicion titulo="Por contraparte" filas={contrapartes}
              Ver={VerContrapartes} etiqueta="contrapartes" moneda={moneda} />
            <Composicion titulo="Por instrumento" filas={instrumentos}
              Ver={VerInstrumentos} etiqueta="instrumentos" moneda={moneda} />
          </div>

          <div className="spp-dos">
            {/* Sin tope y sin 'Otras': los traders son pocos y el sentido de
                esta tabla es justamente poder ver a cada uno. Con el libro de
                FMS no se pinta: esas filas no llevan nombre. */}
            {libro !== 'fms' ? (
              <Composicion titulo="Por trader" moneda={moneda}
                filas={(resumen?.por_trader || []).filter((f) => !moneda || f.moneda === moneda)} />
            ) : (
              <Composicion titulo="Por moneda" moneda={null}
                filas={resumen?.por_moneda || []} conMoneda />
            )}
            <Composicion titulo="Por fondo" moneda={moneda}
              filas={(resumen?.por_fondo || [])
                .filter((f) => !moneda || f.moneda === moneda)
                .map((f) => ({ ...f, clave: `Fondo ${f.clave}` }))} />
          </div>

          {libro !== 'fms' && (
            <div className="spp-dos">
              <Composicion titulo="Por moneda" moneda={null}
                filas={resumen?.por_moneda || []} conMoneda />
            </div>
          )}
        </>
      )}
    </div>
  );
}

// Una tabla de composición: nombre, barra proporcional, monto y operaciones.
// La barra se calcula sobre el mayor de la tabla, no sobre el total, porque
// con veinte filas todas las barras quedarían invisibles menos la primera.
function Composicion({ titulo, filas, Ver, etiqueta, moneda, conMoneda = false }) {
  const lista = filas || [];
  const tope = Math.max(...lista.map((f) => f.monto), 0) || 1;
  return (
    <div className="panel">
      <div className="panel-title">{titulo}</div>
      <div className="table-wrap">
        <table>
          <thead><tr>
            <th>{titulo.replace('Por ', '').replace(/^./, (c) => c.toUpperCase())}</th>
            {conMoneda && <th>Moneda</th>}
            <th style={{ width: '32%' }}>Peso</th>
            <th className="num">Monto</th>
            <th className="num">Operaciones</th>
          </tr></thead>
          <tbody>
            {lista.length ? lista.map((f) => (
              <tr key={`${f.clave}-${f.moneda}`}>
                <td>{f.clave}{f.agrupadas ? ` (${f.agrupadas})` : ''}</td>
                {conMoneda && <td>{f.moneda}</td>}
                <td><span className="tb-barra">
                  <span style={{ width: `${Math.max(2, (f.monto / tope) * 100)}%` }} />
                </span></td>
                <td className="num">{fMonto(f.monto, conMoneda ? null : moneda)}</td>
                <td className="num">{nEnt(f.operaciones)}</td>
              </tr>
            )) : (
              <tr><td colSpan={conMoneda ? 5 : 4} className="dim">Sin datos.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {Ver && <Ver etiqueta={etiqueta} />}
    </div>
  );
}
