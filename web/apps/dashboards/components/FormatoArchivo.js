// web/apps/dashboards/components/FormatoArchivo.js
// ---------------------------------------------------------------------------
// NUESTRO. El icono de ayuda que va junto a cada campo de subir archivo, y la
// ventana que abre con el formato que ese sitio espera.
//
// Muestra el CUADRO y nada más: los encabezados y una fila de ejemplo. Eso
// es el formato; describir además cada columna con un párrafo repetía con
// palabras lo que la fila ya enseña, y la ventana pedía scroll para decir
// menos.
//
// Las columnas vienen de /api/formatos/{clave}, que las deriva de las MISMAS
// constantes que usa el lector para parsear. Escribirlas aquí a mano habría
// sido más corto y habría empezado a mentir el día que alguien agregue un
// alias de columna.
//
// Se pide al abrir, no al montar: son cuatro formularios en pantalla y casi
// nadie abre los cuatro.
// ---------------------------------------------------------------------------
'use client';

import { useEffect, useRef, useState } from 'react';

import { apiGet } from '../lib/api';
import { apiUrl } from '../lib/spp';

export default function FormatoArchivo({ clave, etiqueta = 'Ver el formato' }) {
  const [abierto, setAbierto] = useState(false);
  const [datos, setDatos] = useState(null);
  const [error, setError] = useState(null);
  const caja = useRef(null);

  useEffect(() => {
    if (!abierto || datos) return;
    apiGet(`/api/formatos/${clave}`).then(setDatos)
      .catch((e) => setError(e.message));
  }, [abierto, clave, datos]);

  // Escape y clic fuera cierran. Una ventana modal que solo se cierra con su
  // propia aspa es la que acaba dejando al operador atrapado.
  useEffect(() => {
    if (!abierto) return;
    const alTeclear = (e) => { if (e.key === 'Escape') setAbierto(false); };
    const alPulsar = (e) => {
      if (caja.current && !caja.current.contains(e.target)) setAbierto(false);
    };
    document.addEventListener('keydown', alTeclear);
    document.addEventListener('mousedown', alPulsar);
    return () => {
      document.removeEventListener('keydown', alTeclear);
      document.removeEventListener('mousedown', alPulsar);
    };
  }, [abierto]);

  return (
    <>
      <button type="button" className="fmt-icono" title={etiqueta}
        aria-label={etiqueta} aria-haspopup="dialog" aria-expanded={abierto}
        onClick={() => setAbierto((v) => !v)}>
        {/* Una tabla dibujada: dice "así se ve el archivo" sin una palabra,
            y no se confunde con el ? de la ayuda general. */}
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none"
          stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
          <rect x="1.7" y="2.7" width="12.6" height="10.6" rx="1.4" />
          <path d="M1.7 6.2h12.6M6.2 6.2v7.1M10.3 6.2v7.1" />
        </svg>
      </button>

      {abierto && (
        <div className="fmt-fondo" role="dialog" aria-modal="true"
          aria-label={datos?.titulo || etiqueta}>
          <div className="fmt-ventana" ref={caja}>
            <div className="fmt-cabecera">
              <div className="fmt-titulo">{datos?.titulo || 'Formato del archivo'}</div>
              <button type="button" className="fmt-cerrar" aria-label="Cerrar"
                onClick={() => setAbierto(false)}>×</button>
            </div>

            {error && <p className="fmt-resumen neg">No se pudo leer el formato: {error}</p>}
            {!datos && !error && <div className="loading">Cargando…</div>}

            {datos && (
              <div className="fmt-cuerpo">
                {datos.columnas.length ? (
                  <>
                    {/* El cuadro ES la respuesta. Una fila de ejemplo con los
                        encabezados encima dice el formato entero de un
                        vistazo, y la lista de definiciones que habia debajo
                        repetia con parrafos lo que la fila ya mostraba. */}
                    <div className="table-wrap fmt-ejemplo">
                      <table>
                        <thead><tr>
                          {datos.columnas.map((c) => (
                            <th key={c.nombre}>
                              {c.nombre}{c.obligatoria && <span className="fmt-req">*</span>}
                            </th>
                          ))}
                        </tr></thead>
                        <tbody><tr>
                          {datos.columnas.map((c) => (
                            <td key={c.nombre}>{c.ejemplo || <span className="dim">—</span>}</td>
                          ))}
                        </tr></tbody>
                      </table>
                    </div>
                    <p className="fmt-pie">
                      <span className="fmt-req">*</span> obligatoria. El orden de
                      las columnas da igual y las que no se reconozcan se ignoran.
                    </p>
                  </>
                ) : (
                  /* Sin cuadro que ensenar - es una hoja ajena, la de la SBS -
                     hay que decirlo con palabras o el icono abriria en vacio. */
                  <ul className="fmt-notas">
                    {(datos.notas || []).map((n) => <li key={n}>{n}</li>)}
                  </ul>
                )}

                {datos.plantilla && (
                  <a className="btn principal" href={apiUrl(datos.plantilla)}
                    style={{ marginTop: 4, display: 'inline-block' }}>
                    ↓ Bajar la plantilla · XLSX
                  </a>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
