// web/apps/dashboards/app/tradebook/layout.js
// ---------------------------------------------------------------------------
// Envoltura de las vistas del Tradebook.
//
// Existe por lo mismo que el layout del monitor de valor cuota: cargar los
// estilos del módulo aquí y no en globals.css, para que base.css se pueda
// reemplazar entero cuando llegue una versión nueva del sistema de diseño sin
// llevarse por delante lo nuestro.
//
// tablero.css trae los widgets compartidos (pestañas, botones segmentados,
// desplegables, la rejilla de dos columnas); tradebook.css, lo propio de este
// módulo.
// ---------------------------------------------------------------------------
import '../estilos/tablero.css';
import './tradebook.css';

export default function TradebookLayout({ children }) {
  return children;
}
