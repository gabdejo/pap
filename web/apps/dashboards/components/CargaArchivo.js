// web/apps/dashboards/components/CargaArchivo.js
// ---------------------------------------------------------------------------
// NUESTRO. La carga de un archivo, igual en todos los sitios que la ofrecen.
//
// Dos paneles, actuar a la izquierda y ver a la derecha:
//
//   IZQUIERDA  título, qué se espera, y una cabecera siempre en el mismo
//              orden: [formato] [↓ Plantilla] [archivo]. Debajo, los
//              campos propios del sitio (extra), los botones (acciones) y el
//              eco de lo que pasó.
//   DERECHA    la vista previa de lo que trae el archivo, con un estado vacío
//              que dice qué va a aparecer ahí. Nada de lo que se ve a la
//              derecha ha tocado la base todavía; el panel lo dice.
//
// Este componente NO sabe qué hay dentro del archivo ni cómo se carga: cada
// sitio tiene su endpoint, su informe y su flujo (revisar y cargar; o revisar
// y elegir entre completar y corregir). Lo que unifica es dónde vive cada
// cosa, para que el operador que aprendió a cargar valor cuota sepa cargar
// el Tradebook sin volver a mirar. Por eso el <input type=file> se expone con
// forwardRef: el sitio sigue leyendo el archivo como lo leía.
// ---------------------------------------------------------------------------
'use client';

import { forwardRef } from 'react';

import Eco from './Eco';
import FormatoArchivo from './FormatoArchivo';
import { apiUrl } from '../lib/spp';

const CargaArchivo = forwardRef(function CargaArchivo({
  titulo,
  descripcion,
  formato,
  plantilla = null,
  accept = '.xlsx,.xls,.csv',
  nombre = null,
  onChange,
  extra = null,
  acciones = null,
  eco = '',
  pie = null,
  previaTitulo = 'Vista previa del archivo',
  previaEstado = null,
  previa = null,
  previaVacia = 'Elige un archivo y aquí verás lo que trae, antes de que nada toque la base.',
}, ref) {
  return (
    <div className="spp-dos">
      <div className="panel">
        <div className="panel-title">{titulo}</div>
        {descripcion && <p className="page-sub">{descripcion}</p>}

        {/* La cabecera fija. El icono va PRIMERO: es la pregunta que uno se
            hace antes de elegir el archivo, no después. */}
        <div className="controls carga-cabeza">
          <FormatoArchivo clave={formato} />
          {plantilla && (
            <a className="btn" href={apiUrl(plantilla)}>↓ Plantilla · XLSX</a>
          )}
          <input ref={ref} type="file" accept={accept} className="date-input"
            aria-label="Archivo a cargar" onChange={onChange} />
          {/* Sin selector de hoja: el lector toma la primera. Un cuadro
              «Hoja (opcional)» era una pregunta más para el operador y casi
              nunca tenia respuesta distinta de la primera. */}
          {nombre && <span className="page-sub dim" style={{ margin: 0 }}>{nombre}</span>}
        </div>

        {extra}
        {acciones && <div className="controls" style={{ marginTop: 12 }}>{acciones}</div>}
        <Eco eco={eco} />
        {pie && <div className="controls" style={{ marginTop: 12 }}>{pie}</div>}
      </div>

      <div className="panel">
        <div className="panel-title">{previaTitulo}</div>
        {previa ? (
          <>
            {previaEstado && <p className="page-sub">{previaEstado}</p>}
            {previa}
          </>
        ) : (
          <p className="page-sub dim">{previaVacia}</p>
        )}
      </div>
    </div>
  );
});

export default CargaArchivo;
