// web/apps/dashboards/app/spp/libro/page.js
// ---------------------------------------------------------------------------
// SPP tablero · Libro: the stored series, day by day.
//
// Three books, one on screen at a time: the AFPs' valor cuota (filtered by
// metric / fund / row count, with a CSV of exactly what is shown), and the
// two composite indices - the TARGET and the BENCHMARK. They are separate
// tables on purpose: side by side they would be one wide grid where nothing
// reads well.
//
// An index table is laid over the SAME date grid as the book, because the
// question it answers is "which days is the index missing": listing only the
// days it has would hide exactly what one is looking for.
//
// The index books are not written here: the list comes from the config
// (tipos_indice), so adding a third index would not touch this file.
// ---------------------------------------------------------------------------
'use client';

import { useEffect, useState } from 'react';
import { apiGet } from '../../../lib/api';
import { apiUrl, fFecha, fmtMetrica, metricasDe, nEnt, nombreMetrica } from '../../../lib/spp';
import SppSeg from '../../../components/SppSeg';
import SppTabs from '../../../components/SppTabs';

const LIMITES = [['60', '60'], ['250', '250'], ['1000', '1000'], ['Todas', 'todas']];
const RESPALDO_INDICES = [['Target', 'target'], ['Benchmark', 'benchmark']];

export default function SppLibroPage() {
  const [cfg, setCfg] = useState(null);
  const [libro, setLibro] = useState('vc');
  const [metrica, setMetrica] = useState('valor_cuota');
  const [fondo, setFondo] = useState('todos');
  const [limite, setLimite] = useState('60');
  const [data, setData] = useState(null);
  const [indice, setIndice] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    apiGet('/api/spp/config').then(setCfg).catch((e) => setError(e.message));
  }, []);

  const indices = cfg?.tipos_indice?.length
    ? cfg.tipos_indice.map((t) => [t.etiqueta, t.clave])
    : RESPALDO_INDICES;
  const LIBROS = [['Valor cuota', 'vc'], ...indices];
  const esIndice = libro !== 'vc';
  const etiquetaIndice = (indices.find(([, v]) => v === libro) || [libro])[0];
  const nombreIndice = etiquetaIndice.toLowerCase();

  useEffect(() => {
    setLoading(true); setError(null);
    // Stale-response guard: "Todas" over the whole book takes seconds while
    // "60" comes back instantly, so a late reply for a previous selection
    // could land on top of the current one - thousands of rows under a
    // segment that says 60, with nothing to signal the mismatch.
    let vigente = true;
    const ruta = esIndice
      ? `/api/spp/indice/${libro}/tabla?${new URLSearchParams({ limite })}`
      : `/api/spp/tabla?${new URLSearchParams({ limite, metrica, fondo })}`;
    apiGet(ruta)
      .then((d) => { if (vigente) (esIndice ? setIndice : setData)(d); })
      .catch((e) => { if (vigente) setError(e.message); })
      .finally(() => { if (vigente) setLoading(false); });
    return () => { vigente = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libro, metrica, fondo, limite]);

  const actual = esIndice ? indice : data;
  const columnas = actual?.columnas || [];
  const filas = actual?.filas || [];

  const urlDescarga = esIndice
    ? apiUrl(`/api/spp/indice/${libro}/exportar`)
    : apiUrl(`/api/spp/exportar?${new URLSearchParams({ metrica, fondo, limite })}`);

  const cab = (c) => {
    if (esIndice) return `Fondo ${c.fondo}`;
    const base = `${c.afp} F${c.fondo}`;
    return metrica === 'todas' ? `${base} · ${nombreMetrica(cfg, c.metrica)}` : base;
  };

  const resumen = () => {
    if (!filas.length) return '—';
    if (!esIndice) return `${nEnt(filas.length)} filas en pantalla`;
    const faltan = indice?.faltantes || 0;
    return faltan
      ? `${nEnt(filas.length)} fechas · ${nEnt(faltan)} sin ${nombreIndice} completo`
      : `${nEnt(filas.length)} fechas · sin huecos`;
  };

  return (
    <div>
      <h1 className="page-title">Valor Cuota SPP</h1>
      <p className="page-sub">
        Libro {esIndice ? `del ${nombreIndice}` : 'de valores cuota'} · {resumen()}
      </p>
      <SppTabs />

      <div className="panel">
        <div className="controls spp-controls">
          <div className="field"><label>Libro</label>
            <SppSeg items={LIBROS} value={libro} onChange={setLibro} /></div>
          {!esIndice && (
            <>
              <div className="field"><label>Métrica</label>
                <SppSeg items={[...metricasDe(cfg), ['Todas', 'todas']]}
                  value={metrica} onChange={setMetrica} /></div>
              <div className="field"><label>Tipo de fondo</label>
                <SppSeg items={[...(cfg?.fondos || []).map((f) => [`Fondo ${f}`, String(f)]),
                  ['Todos', 'todos']]}
                  value={fondo} onChange={setFondo} /></div>
            </>
          )}
          <div className="field"><label>Filas</label>
            <SppSeg items={LIMITES} value={limite} onChange={setLimite} /></div>
          <div className="field"><label aria-hidden="true">&nbsp;</label>
            <a className="btn" href={urlDescarga}>
              {esIndice ? `↓ Bajar el ${nombreIndice} · XLSX` : '↓ Bajar lo que veo · CSV'}</a></div>
        </div>
        {esIndice && (
          <p className="page-sub dim" style={{ marginTop: 10, marginBottom: 0 }}>
            Las fechas son las del libro de valor cuota: así un día sin {nombreIndice}
            se ve como un hueco en vez de desaparecer de la lista. El {nombreIndice} lo
            produce el recálculo de la canasta, en <b>Registro y carga → {etiquetaIndice}</b>.
          </p>
        )}
      </div>

      {error && <div className="panel error">Error: {error}</div>}

      <div className="panel">
        {loading ? <div className="loading">Cargando…</div> : (
          <div className="table-wrap spp-vent">
            <table>
              <thead>
                <tr><th>Fecha</th>{columnas.map((c) => <th key={c.col} className="num">{cab(c)}</th>)}</tr>
              </thead>
              <tbody>
                {filas.map((f) => (
                  <tr key={f.fecha}>
                    <td>{fFecha(f.fecha)}</td>
                    {f.valores.map((v, i) => (
                      <td key={columnas[i]?.col || i} className={`num ${v == null ? 'dim' : ''}`}>
                        {v == null ? '—'
                          : esIndice
                            // Niveles de índice: nunca abreviados. fmtMetrica en
                            // modo compacto convertiría 1.500 puntos en "1,50 M".
                            ? Number(v).toLocaleString('es-PE',
                              { minimumFractionDigits: 2, maximumFractionDigits: 4 })
                            : fmtMetrica(v, columnas[i]?.metrica || metrica, true)}
                      </td>
                    ))}
                  </tr>
                ))}
                {!filas.length && (
                  <tr><td colSpan={columnas.length + 1} className="dim">
                    {esIndice
                      ? 'No hay fechas en el libro todavía.'
                      : 'El libro está vacío.'}</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
