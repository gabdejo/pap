// web/apps/dashboards/app/spp/carga/IndiceCompuesto.js
// ---------------------------------------------------------------------------
// Un índice compuesto de los fondos —el TARGET o el BENCHMARK— en Registro y
// carga: cuáles hay y cómo se llaman, y el editor de su canasta versionada.
//
// Es UN componente para los dos porque son la misma cosa con distinto papel:
// una canasta de series con pesos, versionada por fecha, que un recálculo
// convierte en una serie de niveles. Lo único que cambia entre ellos es el
// `tipo`, que viaja en la URL de la API (/api/spp/indice/{tipo}/...), y la
// palabra con que la pantalla los nombra, que viene de la configuración y
// no está escrita aquí.
//
// El componente es dueño de su estado. La página que lo monta le pasa la
// configuración y la tarea de fondo compartida, y le avisa con `version`
// cuando cambia el catálogo de series manuales (crear o borrar una), para
// que el selector de FX no se quede con una lista vieja.
// ---------------------------------------------------------------------------
'use client';

import { useEffect, useState } from 'react';

import Bitacora from '../../../components/Bitacora';
import Eco from '../../../components/Eco';
import SppSeg from '../../../components/SppSeg';
import useVerTodo from '../../../components/useVerTodo';
import { apiGet } from '../../../lib/api';
import { apiSend, apiUrl, fFecha, hoyLocal, nEnt, nombreFuente } from '../../../lib/spp';

// Tabla chica clave/valor, la misma que usa el resto de la vista.
function Informe({ filas }) {
  return (
    <table className="spp-informe"><tbody>
      {filas.map(([a, b], i) => (
        <tr key={i}><td>{a}</td><td className="num">{b}</td></tr>
      ))}
    </tbody></table>
  );
}

export default function IndiceCompuesto({
  tipo, etiqueta, cfg, setCfg, ocupado, tarea, iniciar, alTerminarTarea, version = 0,
}) {
  const base = `/api/spp/indice/${tipo}`;
  // «el target», «el benchmark»: para las frases.
  const nombreTipo = etiqueta.toLowerCase();
  const fondosDelTipo = cfg?.fondos_indice?.[tipo] || [];

  // ---- serie calculada (solo lectura) ----
  const [bEstado, setBEstado] = useState(null);
  const cargarEstado = () =>
    apiGet(base).then((j) => setBEstado(j.estado)).catch(() => {});

  // ---- definicion (uno por fondo, con nombre) ----
  const [defs, setDefs] = useState([]);
  const [dNombre, setDNombre] = useState('');
  const [dDesc, setDDesc] = useState('');
  const [dFondo, setDFondo] = useState(1);
  const [dEco, setDEco] = useState('');

  const cargarDefiniciones = () =>
    apiGet(`${base}/definiciones`).then((j) => setDefs(j.indices || [])).catch(() => {});
  const defDe = (f) => defs.find((d) => d.fondo === Number(f)) || null;
  const nombreDe = (f) => defDe(f)?.nombre || `Fondo ${f}`;

  const guardarDefinicion = async () => {
    setDEco('');
    const r = await apiSend(`${base}/definicion`, 'POST',
      { fondo: dFondo, nombre: dNombre, descripcion: dDesc });
    if (!r.ok) { setDEco({ ok: false, texto: r.data.motivo || `Error ${r.status}` }); return; }
    setDEco({ ok: true, texto: `${etiqueta} del Fondo ${dFondo}: «${r.data.resultado.nombre}».` });
    setDNombre(''); setDDesc('');
    await cargarDefiniciones();
    // Habilitar un fondo nuevo cambia la lista de fondos con este índice.
    apiGet('/api/spp/config').then(setCfg).catch(() => {});
  };

  // ---- composicion (canasta versionada) ----
  const [cFondo, setCFondo] = useState(1);
  const [cFecha, setCFecha] = useState('');
  const [cComponentes, setCComponentes] = useState([]);
  const [cBusqueda, setCBusqueda] = useState('');
  const [cResultados, setCResultados] = useState(null);
  const [cCatalogoFx, setCCatalogoFx] = useState(null);
  const [composiciones, setComposiciones] = useState([]);
  const [cEco, setCEco] = useState('');

  const cargarComposiciones = () =>
    apiGet(`${base}/composicion`).then((j) => setComposiciones(j.composiciones || [])).catch(() => {});
  // Catálogo chico siempre cargado para los selectores de FX.
  const cargarCatalogoFx = () =>
    apiGet(`${base}/series-disponibles`).then(setCCatalogoFx).catch(() => {});

  useEffect(() => {
    if (!cfg) return;
    setCFecha(hoyLocal());
    cargarComposiciones();
    cargarDefiniciones();
    cargarCatalogoFx();
    cargarEstado();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg, tipo]);

  // El catálogo de FX cambia cuando la página crea o borra una serie manual.
  useEffect(() => {
    if (version) cargarCatalogoFx();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);

  // Hasta 100 resultados (50 por base): se muestran los primeros y el resto
  // llega con el botón, en vez de encerrarlos en una caja con scroll.
  const cHallazgos = ['bloomberg', 'manual'].flatMap((fu) =>
    (cResultados?.[fu] || []).map((s) => ({ ...s, fu })));
  const [cVisibles, VerMasSeries] = useVerTodo(cHallazgos, 8);

  const buscarSeries = async () => {
    try {
      setCResultados(await apiGet(`${base}/series-disponibles?q=${encodeURIComponent(cBusqueda)}`));
    } catch { setCResultados(null); }
  };

  const agregarComponente = (fuente, s) => {
    if (cComponentes.some((c) => c.fuente === fuente && c.ref_id === s.ref_id)) return;
    setCComponentes([...cComponentes,
      { etiqueta: s.etiqueta, fuente, ref_id: s.ref_id, peso: '', fx: '' }]);
  };

  const sumaPesos = cComponentes.reduce((a, c) => a + (Number(c.peso) || 0), 0);
  const sumaOk = Math.abs(sumaPesos - 1) < 1e-6 || Math.abs(sumaPesos - 100) < 1e-4;

  const guardarComposicion = async () => {
    setCEco('');
    const componentes = cComponentes.map((c) => {
      const fx = c.fx ? JSON.parse(c.fx) : null;
      return {
        etiqueta: c.etiqueta, fuente: c.fuente, ref_id: c.ref_id,
        peso: Number(c.peso),
        ...(fx ? { fx_fuente: fx.fuente, fx_ref_id: fx.ref_id } : {}),
      };
    });
    const r = await apiSend(`${base}/composicion`, 'POST',
      { fondo: cFondo, vigente_desde: cFecha, componentes });
    if (!r.ok) { setCEco({ ok: false, texto: r.data.motivo || `Error ${r.status}` }); return; }
    setCEco({ ok: true, texto: `Composición del Fondo ${cFondo} guardada, vigente desde ${fFecha(cFecha)}. Recalcula para regenerar la serie.` });
    cargarComposiciones();
  };

  const borrarComposicion = async (g) => {
    if (!window.confirm(
      `Borrar la composición del Fondo ${g.fondo} vigente desde ${fFecha(g.vigente_desde)}. El siguiente recálculo ya no la usará. ¿Continuar?`)) return;
    const r = await apiSend(
      `${base}/composicion?fondo=${g.fondo}&vigente_desde=${g.vigente_desde}`, 'DELETE');
    setCEco(r.ok
      ? { ok: true, texto: 'Composición borrada.' }
      : { ok: false, texto: r.data.motivo || `Error ${r.status}` });
    cargarComposiciones();
  };

  const editarComposicion = (g) => {
    setCFondo(g.fondo);
    setCFecha(g.vigente_desde);
    setCComponentes(g.componentes.map((c) => ({
      etiqueta: c.etiqueta, fuente: c.fuente, ref_id: c.ref_id,
      peso: String(c.peso),
      fx: c.fx_ref_id ? JSON.stringify({ fuente: c.fx_fuente, ref_id: c.fx_ref_id }) : '',
    })));
    setCEco({ ok: true, texto: `Editando la composición vigente desde ${fFecha(g.vigente_desde)}; guardar la reemplaza en esa fecha.` });
  };

  const recalcular = async () => {
    if (!window.confirm(
      `Recalcular el ${nombreTipo} del Fondo ${cFondo} desde sus composiciones REEMPLAZA la serie completa almacenada (base 100 en el primer rebalanceo). ¿Continuar?`)) return;
    const r = await apiSend(`${base}/recalcular`, 'POST', { fondo: cFondo });
    if (!r.ok) { setCEco({ ok: false, texto: r.data.motivo || `Error ${r.status}` }); return; }
    // El resultado hay que DECIRLO aquí: antes un recálculo fallido se veía
    // igual que uno exitoso - el botón se apagaba y volvía.
    setCEco({ ok: true, texto: `Recalculando el ${nombreTipo} F${cFondo}…` });
    iniciar(`recalculo del ${nombreTipo} F${cFondo}`, alTerminarTarea((t) => {
      cargarEstado();
      setCEco(t.error
        ? { ok: false, texto: `El recálculo falló y la serie anterior sigue intacta: ${t.error}` }
        : { ok: true, texto: `${etiqueta} F${cFondo} recalculado desde sus composiciones.` });
    }));
  };

  // Los componentes salen de las DOS bases: el registro Bloomberg y las
  // series manuales (el backend todavía acepta 'fact' para composiciones
  // guardadas antes del rediseño).
  const opcionesFx = ['bloomberg', 'manual'].flatMap((fu) =>
    (cCatalogoFx?.[fu] || []).map((s) => ({
      v: JSON.stringify({ fuente: fu, ref_id: s.ref_id }),
      t: `${s.etiqueta} (${nombreFuente(fu)})`,
    })));

  return (
    <>
      {/* ===== a · cuáles hay y cómo se llaman ===== */}
      <div className="panel">
        <div className="panel-title">{etiqueta}s declarados</div>
        <p className="page-sub">Cada tipo de fondo tiene <b>un</b> {nombreTipo}, con su propio
          nombre. Declarar uno para un fondo que no lo tenía es lo que se lo habilita:
          su serie queda registrada al guardarlo.</p>

        <div className="table-wrap">
          <table>
            <thead><tr><th>Fondo</th><th>{etiqueta}</th><th>Descripción</th>
              <th className="num">Rebalanceos</th><th className="num">Días calculados</th></tr></thead>
            <tbody>
              {defs.length ? defs.map((d) => (
                <tr key={d.fondo}>
                  <td>Fondo {d.fondo}</td>
                  <td className={d.declarado ? '' : 'dim'}>{d.nombre}</td>
                  <td className="dim">{d.descripcion || '—'}</td>
                  <td className="num">{nEnt(d.rebalanceos)}</td>
                  <td className={`num ${d.puntos ? '' : 'dim'}`}>
                    {d.puntos ? nEnt(d.puntos) : 'sin calcular'}</td>
                </tr>
              )) : <tr><td colSpan={5} className="dim">Cargando…</td></tr>}
            </tbody>
          </table>
        </div>

        <div className="controls spp-controls" style={{ marginTop: 12 }}>
          <div className="field"><label htmlFor={`d-fondo-${tipo}`}>Tipo de fondo</label>
            <select id={`d-fondo-${tipo}`} className="select" value={dFondo}
              onChange={(e) => setDFondo(Number(e.target.value))}>
              {(cfg?.fondos || []).map((f) => (
                <option key={f} value={f}>Fondo {f}</option>
              ))}
            </select></div>
          <div className="field" style={{ flex: '1 1 260px' }}>
            <label htmlFor={`d-nombre-${tipo}`}>Nombre del {nombreTipo}</label>
            <input id={`d-nombre-${tipo}`} className="date-input"
              placeholder="p. ej. Renta mixta global 60/40"
              value={dNombre} onChange={(e) => setDNombre(e.target.value)} /></div>
          <div className="field" style={{ flex: '1 1 260px' }}>
            <label htmlFor={`d-desc-${tipo}`}>Descripción</label>
            <input id={`d-desc-${tipo}`} className="date-input" placeholder="opcional"
              value={dDesc} onChange={(e) => setDDesc(e.target.value)} /></div>
          <div className="field"><label aria-hidden="true">&nbsp;</label>
            <button className="btn principal" disabled={!dNombre.trim() || ocupado}
              onClick={guardarDefinicion}>
              {defDe(dFondo)?.declarado ? 'Renombrar' : `Crear ${nombreTipo}`}</button></div>
        </div>
        <Eco eco={dEco} />
      </div>

      {/* ===== b · composición de la canasta ===== */}
      <div className="spp-dos">
        <div className="panel">
          <div className="panel-title">Composición de «{nombreDe(cFondo)}»</div>
          <p className="page-sub">El {nombreTipo} de cada fondo se <b>construye</b> desde las dos
            bases de componentes (Bloomberg y series manuales): una canasta de series con pesos,
            <b> versionada por fecha</b>: cambiar tickers o pesos crea una composición nueva desde
            su fecha de vigencia, sin tocar la historia. Entre rebalanceos los pesos <b>derivan </b>
            con los precios (buy-and-hold). Recalcular regenera la serie completa (base 100 en el
            primer rebalanceo). Los niveles nunca se cargan a mano.</p>
          <p className="page-sub dim">Los componentes salen de las dos bases: los de Bloomberg se
            administran en la pestaña <b>Series Bloomberg</b>; los demás, en el área
            <b> Series manuales</b> de aquí al lado.</p>

          <div className="controls spp-controls">
            <div className="field"><label>Tipo de fondo</label>
              <SppSeg items={fondosDelTipo.map((f) => [`Fondo ${f}`, f])}
                value={cFondo} onChange={setCFondo} /></div>
            <div className="field"><label htmlFor={`c-fecha-${tipo}`}>Vigente desde</label>
              <input id={`c-fecha-${tipo}`} className="date-input" type="date" value={cFecha}
                onChange={(e) => setCFecha(e.target.value)} /></div>
          </div>

          <div className="controls" style={{ marginTop: 10 }}>
            <input className="date-input" placeholder="Buscar serie (ticker, nombre)…"
              aria-label="Buscar serie en las dos bases"
              value={cBusqueda} onChange={(e) => setCBusqueda(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && buscarSeries()} />
            <button className="btn" onClick={buscarSeries}>Buscar</button>
          </div>
          {cResultados && (
            <div className="table-wrap" style={{ marginTop: 8 }}>
              <table><tbody>
                {cVisibles.map((s) => (
                  <tr key={`${s.fu}-${s.ref_id}`}>
                    <td className="mono">{s.etiqueta}</td>
                    <td className="dim">{nombreFuente(s.fu)} · {s.detalle}</td>
                    <td><button className="btn"
                      onClick={() => agregarComponente(s.fu, s)}>+ Agregar</button></td>
                  </tr>
                ))}
                {!cHallazgos.length && (
                  <tr><td className="dim">Sin resultados en las dos bases (Bloomberg y manuales).</td></tr>
                )}
              </tbody></table>
            </div>
          )}
          {cResultados && <VerMasSeries etiqueta="series encontradas" />}

          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table>
              <thead><tr><th>Componente</th><th>Fuente</th><th className="num">Peso</th>
                <th>FX (opcional)</th><th></th></tr></thead>
              <tbody>
                {cComponentes.length ? cComponentes.map((c, i) => (
                  <tr key={`${c.fuente}-${c.ref_id}`}>
                    <td className="mono">{c.etiqueta}</td>
                    <td className="dim">{nombreFuente(c.fuente)}</td>
                    <td className="num">
                      <input className="date-input" type="number" min="0" step="0.01"
                        style={{ width: 90 }} value={c.peso}
                        onChange={(e) => setCComponentes(
                          cComponentes.map((x, j) => (j === i ? { ...x, peso: e.target.value } : x)))} />
                    </td>
                    <td>
                      <select className="select" value={c.fx}
                        onChange={(e) => setCComponentes(
                          cComponentes.map((x, j) => (j === i ? { ...x, fx: e.target.value } : x)))}>
                        <option value="">— sin conversión —</option>
                        {opcionesFx.map((o) => <option key={o.v} value={o.v}>{o.t}</option>)}
                      </select>
                    </td>
                    <td><button className="btn peligro"
                      onClick={() => setCComponentes(cComponentes.filter((_, j) => j !== i))}>Quitar</button></td>
                  </tr>
                )) : <tr><td colSpan={5} className="dim">Busca series y agrégalas a la canasta.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="page-sub">Suma de pesos: <b className={sumaOk ? 'pos' : 'neg'}>
            {sumaPesos.toLocaleString('es-PE', { maximumFractionDigits: 4 })}</b> (debe ser 1 o 100)</p>

          <div className="controls">
            <button className="btn principal" disabled={!cComponentes.length || !sumaOk || !cFecha || ocupado}
              onClick={guardarComposicion}>Guardar composición</button>
            <button className="btn" disabled={ocupado} onClick={recalcular}>
              Recalcular «{nombreDe(cFondo)}»</button>
          </div>
          <Eco eco={cEco} />
          {/* El recálculo corre en la misma tarea de fondo; su rastro se ve aquí. */}
          {tarea && <Bitacora tarea={tarea} />}
        </div>

        <div className="panel">
          <div className="panel-title">Serie calculada</div>
          <p className="page-sub">
            {bEstado?.filas
              ? `${nEnt(bEstado.filas)} fechas · ${fFecha(bEstado.desde)} a ${fFecha(bEstado.hasta)}`
              : 'Todavía no hay serie calculada: declara una composición y recalcula.'}
          </p>
          {bEstado?.series?.length > 0 && (
            <Informe filas={bEstado.series.map((x) => [
              nombreDe(x.fondo),
              x.puntos ? `${nEnt(x.puntos)} fechas · último ${x.valor} el ${fFecha(x.fecha)}` : 'sin datos',
            ])} />
          )}
          {bEstado?.filas > 0 && (
            <div className="controls" style={{ marginBottom: 12 }}>
              <a className="btn" href={apiUrl(`${base}/exportar`)}>↓ Bajar la serie · XLSX</a>
            </div>
          )}

          <div className="panel-title">Historial de composiciones</div>
          <p className="page-sub">Cada fila es un rebalanceo vigente desde su fecha. Editar una
            composición la carga en el editor; guardarla reemplaza <b>solo esa fecha</b>.</p>
          {composiciones.length ? composiciones.map((g) => (
            <div key={`${g.fondo}-${g.vigente_desde}`} style={{ marginBottom: 12 }}>
              <div className="panel-title" style={{ fontSize: 13 }}>
                {nombreDe(g.fondo)} · desde {fFecha(g.vigente_desde)}</div>
              <Informe filas={g.componentes.map((c) => [
                `${c.etiqueta}${c.fx_ref_id ? ' (con FX)' : ''}`,
                `${(c.peso * 100).toLocaleString('es-PE', { maximumFractionDigits: 2 })} %`,
              ])} />
              <div className="controls" style={{ marginTop: 6 }}>
                <button className="btn" onClick={() => editarComposicion(g)}>Editar</button>
                <button className="btn peligro" onClick={() => borrarComposicion(g)}>Borrar</button>
              </div>
            </div>
          )) : <p className="page-sub dim">Ninguna composición declarada todavía.</p>}
        </div>
      </div>
    </>
  );
}
