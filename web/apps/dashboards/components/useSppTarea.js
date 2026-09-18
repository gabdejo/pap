
// web/apps/dashboards/components/useSppTarea.js
// ---------------------------------------------------------------------------
// The one polling loop over /api/spp/tarea (SBS extraction, historical load,
// Bloomberg download follow the same background-task contract). It used to be
// written per page and had already diverged in interval and error handling.
//
//   iniciar(accion, alTerminar)  kick off UI state and start polling
//   seguir(alTerminar)           poll a task another call already launched
//   ocupado                      derived from tarea.activa
// ---------------------------------------------------------------------------
'use client';

import { useEffect, useRef, useState } from 'react';
import { apiGet } from '../lib/api';

const INTERVALO_MS = 1000;
const MAX_FALLOS = 5;

export default function useSppTarea() {
  const [tarea, setTarea] = useState(null);
  const pollRef = useRef(null);

  // Adopt a task that is ALREADY running when this view mounts. The task
  // slot is one per API process, shared by every tab: without this, moving
  // to another tab (or reloading) while an extraction runs left the new
  // view believing nothing was happening - its write buttons enabled, its
  // bitácora empty - until the operator hit a 409 they could not explain.
  useEffect(() => {
    apiGet('/api/spp/tarea')
      .then((t) => { if (t?.activa) { setTarea(t); seguir(); } })
      .catch(() => {});
    return () => clearInterval(pollRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const seguir = (alTerminar) => {
    clearInterval(pollRef.current);
    // A single failed poll must NOT read as success: the task may still
    // be running server-side. Tolerate a few consecutive blips; only
    // after giving up mark the state as lost - with an error, never as
    // a clean finish.
    let fallos = 0;
    pollRef.current = setInterval(async () => {
      try {
        const t = await apiGet('/api/spp/tarea');
        fallos = 0;
        setTarea(t);
        if (!t.activa) {
          clearInterval(pollRef.current);
          if (alTerminar) alTerminar(t);
        }
      } catch {
        fallos += 1;
        if (fallos >= MAX_FALLOS) {
          clearInterval(pollRef.current);
          setTarea((t) => ({
            ...(t || {}),
            activa: false,
            error: 'Se perdió el seguimiento de la tarea (sin conexión con la API); puede seguir corriendo en el servidor. Recarga la página para reconectar.',
          }));
        }
      }
    }, INTERVALO_MS);
  };

  const iniciar = (accion, alTerminar) => {
    setTarea({ activa: true, accion, bitacora: [] });
    seguir(alTerminar);
  };

  return { tarea, ocupado: !!tarea?.activa, iniciar, seguir };
}
