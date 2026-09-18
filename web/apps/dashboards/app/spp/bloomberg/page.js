
// web/apps/dashboards/app/spp/bloomberg/page.js
// ---------------------------------------------------------------------------
// SPP tablero · Bloomberg: the manual series registry ported from the
// monitor. Register/update a series, bulk-register from a file, delete
// (double-confirmed when it has data), trigger the download (background
// task followed by polling /api/spp/tarea), template and export.
//
// Every action answers even without a terminal on this machine: download
// routes return 503 with the reason, which is shown as-is.
// ---------------------------------------------------------------------------
'use client';

import { useEffect, useRef, useState } from 'react';
import { apiGet } from '../../../lib/api';
import { apiSend, apiUrl, fFecha, nEnt } from '../../../lib/spp';
import Bitacora from '../../../components/Bitacora';
import Eco from '../../../components/Eco';
import CargaArchivo from '../../../components/CargaArchivo';
import SppTabs from '../../../components/SppTabs';
import useSppTarea from '../../../components/useSppTarea';

const INTERVALOS = ['diario', 'semanal', 'mensual', 'trimestral', 'semestral', 'anual'];

export default function SppBloombergPage() {
  const [estado, setEstado] = useState(null);
  const [error, setError] = useState(null);
  const [eco, setEco] = useState('');
  const [form, setForm] = useState({ ticker: '', campo: '', intervalo: 'diario', fecha_inicio: '', descripcion: '' });
  const fileRef = useRef(null);
  const [informe, setInforme] = useState(null);
  // La carga por archivo vive en su propia fila: su eco tambien.
  const [ecoArch, setEcoArch] = useState('');

  // Shared background-task poller (same hook as the carga tab). `ocupado`
  // gates every write: the download thread is inserting into these same
  // tables, so registering or deleting a series mid-download is a race the
  // backend does not stop.
  const { tarea, ocupado, iniciar } = useSppTarea();

  const cargar = () => apiGet('/api/spp/bloomberg').then(setEstado).catch((e) => setError(e.message));
  useEffect(() => { cargar(); }, []);

  const agregar = async () => {
    setEco('');
    const r = await apiSend('/api/spp/bloomberg/serie', 'POST', {
      ticker: form.ticker, campo: form.campo, intervalo: form.intervalo,
      fecha_inicio: form.fecha_inicio || null, descripcion: form.descripcion || null,
    });
    if (r.ok) {
      setEco({ ok: true, texto: `Serie «${r.data.serie.ticker} ${r.data.serie.campo}» registrada (${r.data.serie.intervalo}).` });
      setForm({ ticker: '', campo: '', intervalo: 'diario', fecha_inicio: '', descripcion: '' });
      cargar();
    } else setEco({ ok: false, texto: r.data.motivo || `Error ${r.status}` });
  };

  const borrar = async (s) => {
    const tiene = Number(s.filas) > 0;
    const msg = tiene
      ? `${s.ticker} ${s.campo} tiene ${nEnt(Number(s.filas))} dato(s) cargados. ¿Borrar la serie Y sus datos?`
      : `¿Borrar la serie ${s.ticker} ${s.campo}?`;
    if (!window.confirm(msg)) return;
    const r = await apiSend(`/api/spp/bloomberg/serie/${s.serie_id}${tiene ? '?datos=1' : ''}`, 'DELETE');
    // Name the series the way the operator sees it in the table; serie_id is
    // an internal number that appears in no column.
    setEco(r.ok
      ? { ok: true, texto: `Serie «${s.ticker} ${s.campo}» borrada${tiene ? ' con sus datos' : ''}.` }
      : { ok: false, texto: r.data.motivo || `Error ${r.status}` });
    cargar();
  };

  const extraer = async (corregir) => {
    // Correcting re-requests the whole window and replaces stored points:
    // the same confirmation the other overwriting actions ask for.
    if (corregir && !window.confirm(
      'Bajar y corregir: vuelve a pedir la ventana completa de cada serie y '
      + 'REEMPLAZA los datos ya cargados. ¿Continuar?')) return;
    setEco('');
    const r = await apiSend('/api/spp/bloomberg/extraer', 'POST', { corregir });
    if (r.ok) iniciar('descarga de Bloomberg', () => cargar());
    else setEco({ ok: false, texto: r.data.motivo || `Error ${r.status}` });
  };

  const subirArchivo = async (soloRevisar) => {
    const f = fileRef.current?.files?.[0];
    if (!f) { setEcoArch({ ok: false, texto: 'Elige un archivo primero.' }); return; }
    // Clear the previous attempt first: an error used to leave the old report
    // on screen, reading as if it described the file just chosen.
    setEcoArch(''); setInforme(null);
    const fd = new FormData();
    fd.append('archivo', f);
    fd.append('revisar', soloRevisar ? '1' : '');
    const r = await apiSend('/api/spp/bloomberg/archivo', 'POST', fd, true);
    if (r.ok) {
      setInforme({ ...r.data.informe, revisado: r.data.revisado });
      if (!r.data.revisado) cargar();
    } else setEcoArch({ ok: false, texto: r.data.motivo || `Error ${r.status}` });
  };

  const detalle = estado?.detalle || [];

  return (
    <div>
      <h1 className="page-title">Valor Cuota SPP</h1>
      <p className="page-sub">
        Series de Bloomberg · {estado ? `${estado.series} serie(s), ${nEnt(estado.datos)} datos` : '—'}
      </p>
      <SppTabs />

      {error && <div className="panel error">Error: {error}</div>}
      {estado && !estado.disponible && (
        <div className="panel"><span className="flag-warn">Sin terminal en esta máquina</span>
          <p className="page-sub" style={{ whiteSpace: 'pre-wrap' }}>{estado.motivo}</p>
          <p className="page-sub">El registro se puede editar igual; la descarga corre en la máquina con terminal.</p>
        </div>
      )}

      <div className="panel">
        <div className="panel-title">Agregar o corregir una serie</div>
        <p className="page-sub">Idempotente por ticker + campo + intervalo: volver a enviarla actualiza
          en lugar de duplicar, y lo que no se manda no se borra.</p>
        <div className="controls spp-controls">
          <div className="field"><label htmlFor="bb-ticker">Ticker</label>
            <input id="bb-ticker" className="date-input" placeholder="SPX Index" value={form.ticker}
              onChange={(e) => setForm({ ...form, ticker: e.target.value })} /></div>
          <div className="field"><label htmlFor="bb-campo">Campo</label>
            <input id="bb-campo" className="date-input" placeholder="PX_LAST" value={form.campo}
              onChange={(e) => setForm({ ...form, campo: e.target.value })} /></div>
          <div className="field"><label htmlFor="bb-intervalo">Intervalo</label>
            <select id="bb-intervalo" className="select" value={form.intervalo}
              onChange={(e) => setForm({ ...form, intervalo: e.target.value })}>
              {INTERVALOS.map((i) => <option key={i} value={i}>{i}</option>)}
            </select></div>
          <div className="field"><label htmlFor="bb-desde">Desde</label>
            <input id="bb-desde" className="date-input" type="date" value={form.fecha_inicio}
              onChange={(e) => setForm({ ...form, fecha_inicio: e.target.value })} /></div>
          <div className="field" style={{ flex: '1 1 220px' }}><label htmlFor="bb-desc">Descripción</label>
            <input id="bb-desc" className="date-input" placeholder="S&P 500" value={form.descripcion}
              onChange={(e) => setForm({ ...form, descripcion: e.target.value })} /></div>
          <div className="field"><label aria-hidden="true">&nbsp;</label>
            <button className="btn principal" disabled={ocupado}
              title={ocupado ? 'Espera a que termine la descarga' : undefined}
              onClick={agregar}>Registrar serie</button></div>
        </div>

        <Eco eco={eco} />
      </div>

      <CargaArchivo
        ref={fileRef}
        titulo="Registrar series por archivo"
        descripcion={<>Una fila por serie. Solo el <b>ticker</b> es obligatorio; campo e
          intervalo toman PX_LAST y diario si faltan. El archivo declara QUÉ descargar,
          no los datos: los precios los trae Bloomberg después.</>}
        formato="bloomberg"
        plantilla="/api/spp/bloomberg/plantilla"
        onChange={() => { setInforme(null); setEcoArch(''); }}
        acciones={<>
          <button className="btn" onClick={() => subirArchivo(true)}>Revisar</button>
          <button className="btn principal" disabled={ocupado}
            title={ocupado ? 'Espera a que termine la descarga' : undefined}
            onClick={() => subirArchivo(false)}>Registrar las series</button>
        </>}
        eco={ecoArch}
        pie={<a className="btn" href={apiUrl('/api/spp/bloomberg/exportar')}>↓ Bajar lo cargado · XLSX</a>}
        previaEstado={informe && (informe.revisado
          ? `${informe.archivo} · ${nEnt(informe.total)} serie(s) leídas. Nada se ha registrado todavía.`
          : `${informe.archivo} · ${nEnt(informe.nuevas)} nuevas, ${nEnt(informe.actualizadas)} actualizadas.`)}
        previa={informe && (<>
          <table className="spp-informe"><tbody>
            {informe.hoja && <tr><td>Hoja</td><td className="num">{informe.hoja}</td></tr>}
            <tr><td>Series legibles</td><td className="num">{nEnt(informe.total)}</td></tr>
            <tr><td>Campos</td><td className="num">{(informe.campos || []).join(', ') || '—'}</td></tr>
            <tr><td>Intervalos</td><td className="num">{(informe.intervalos || []).join(', ') || '—'}</td></tr>
            <tr><td>Omitidas</td><td className="num">{nEnt(informe.total_omitidas || 0)}</td></tr>
          </tbody></table>
          {(informe.avisos || []).map((a, i) => (
            <p key={i} className="page-sub dim" style={{ marginTop: 6 }}>{a}</p>
          ))}
          {informe.total_omitidas > 0 && (
            <details className="spp-desplegable" open>
              <summary>Filas omitidas · {nEnt(informe.total_omitidas)}</summary>
              <div className="table-wrap">
                <table>
                  <thead><tr><th className="num">Línea</th><th>Motivo</th></tr></thead>
                  <tbody>{(informe.omitidas || []).map((o) => (
                    <tr key={o.linea}><td className="num">{o.linea}</td><td className="neg">{o.motivo}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            </details>
          )}
          {(informe.series || []).length > 0 && (
            <details className="spp-desplegable" open>
              <summary>Muestra de lo leído · {nEnt(Math.min(10, informe.series.length))} de {nEnt(informe.series.length)}</summary>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Ticker</th><th>Campo</th><th>Intervalo</th><th>Desde</th><th>Descripción</th><th>Moneda</th></tr></thead>
                  <tbody>{informe.series.slice(0, 10).map((x, i) => (
                    <tr key={i}>
                      <td>{x.ticker}</td><td>{x.campo}</td><td>{x.intervalo}</td>
                      <td>{x.fecha_inicio ? fFecha(x.fecha_inicio) : '—'}</td>
                      <td>{x.descripcion || '—'}</td><td>{x.moneda || '—'}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </details>
          )}
        </>)}
      />

      <div className="panel">
        <div className="controls" style={{ justifyContent: 'space-between' }}>
          <div className="panel-title">Descarga</div>
          <div className="controls">
            <button className="btn principal" onClick={() => extraer(false)}
              disabled={ocupado || (estado && !estado.disponible)}>Bajar lo que falta</button>
            <button className="btn peligro" onClick={() => extraer(true)}
              disabled={ocupado || (estado && !estado.disponible)}>Bajar y corregir</button>
          </div>
        </div>
        <p className="page-sub">Cada serie se pide desde el día siguiente a su último dato; las que ya
          están al día no consumen cuota. Las peticiones se agrupan por ventana, campo e intervalo.</p>
        {tarea && <Bitacora tarea={tarea} />}
      </div>

      <div className="panel">
        <div className="panel-title">Series registradas</div>
        <div className="table-wrap">
          <table>
            <thead><tr>
              <th>Ticker</th><th>Campo</th><th>Intervalo</th><th>Descripción</th>
              <th className="num">Puntos</th><th>Cobertura</th><th>Último resultado</th><th>Activa</th><th></th>
            </tr></thead>
            <tbody>
              {detalle.length ? detalle.map((s) => (
                <tr key={s.serie_id}>
                  <td className="mono">{s.ticker}</td>
                  <td className="mono">{s.campo}</td>
                  <td>{s.intervalo}</td>
                  <td>{s.descripcion || <span className="dim">—</span>}</td>
                  <td className="num">{nEnt(Number(s.filas) || 0)}</td>
                  <td>{s.desde ? `${fFecha(s.desde)} a ${fFecha(s.hasta)}` : <span className="dim">sin datos</span>}</td>
                  <td>{s.ultimo_resultado
                    ? <span className={s.ultimo_resultado === 'ok' ? 'pos' : (s.ultimo_resultado === 'error' ? 'neg' : 'dim')}>
                        {s.ultimo_resultado}</span>
                    : <span className="dim">—</span>}</td>
                  <td>{s.activa ? 'sí' : <span className="dim">no</span>}</td>
                  <td><button className="btn peligro" disabled={ocupado}
                    title={ocupado ? 'Espera a que termine la descarga' : undefined}
                    onClick={() => borrar(s)}>Borrar</button></td>
                </tr>
              )) : <tr><td colSpan={9} className="dim">Ninguna serie registrada todavía.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
