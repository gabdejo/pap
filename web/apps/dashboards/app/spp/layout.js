// web/apps/dashboards/app/spp/layout.js
// ---------------------------------------------------------------------------
// Envoltura de las vistas del tablero Valor Cuota SPP.
//
// Existe por una sola razón: cargar spp.css aquí y no en globals.css. Los
// estilos del tablero solo hacen falta bajo /spp/, y tenerlos mezclados con el
// sistema de diseño base obligaba a rescatarlos a mano cada vez que llegaba una
// versión nueva de la interfaz. Next los inyecta igual para todas las rutas,
// pero la SEPARACIÓN es la que importa: base.css se reemplaza entero y esto
// sobrevive.
//
// Son dos hojas: la de widgets, que este tablero comparte con el Tradebook,
// y la suya propia.
// ---------------------------------------------------------------------------
import '../estilos/tablero.css';
import './spp.css';

export default function SppLayout({ children }) {
  return children;
}
