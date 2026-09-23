
// web/apps/dashboards/components/useVerTodo.js
// ---------------------------------------------------------------------------
// Expandir / contraer una lista larga, en vez de encerrarla en una cajita
// con scroll propio.
//
// Los cuadros del tablero no tienen scroll interno: crecen con su contenido
// y quien scrollea es la página. Para que una lista larga no empuje todo lo
// demás fuera de la vista, se muestra un tope y este control dice cuántas
// faltan y las trae.
//
//   const [visibles, VerTodo] = useVerTodo(filas, 12);
//   ...pintar `visibles`...
//   <VerTodo etiqueta="fechas" />
// ---------------------------------------------------------------------------
'use client';

import { useState } from 'react';
import { nEnt } from '../lib/spp';

export default function useVerTodo(items, tope = 12) {
  const [todo, setTodo] = useState(false);
  const lista = items || [];
  const visibles = todo ? lista : lista.slice(0, tope);

  const VerTodo = ({ etiqueta = 'filas', style }) => {
    if (lista.length <= tope) return null;
    return (
      <div className="controls" style={{ marginTop: 10, ...style }}>
        <button className="btn" onClick={() => setTodo((v) => !v)}>
          {todo
            ? `Contraer a ${nEnt(tope)}`
            : `Ver las ${nEnt(lista.length)} ${etiqueta} · faltan ${nEnt(lista.length - tope)}`}
        </button>
      </div>
    );
  };

  return [visibles, VerTodo, todo];
}
