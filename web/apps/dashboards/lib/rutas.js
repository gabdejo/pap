
// web/apps/dashboards/lib/rutas.js
// ---------------------------------------------------------------------------
// The ONE active-route matcher, shared by Nav and Sidebar so the two
// indicators can never disagree on sub-routes. Prefix match with a segment
// boundary: '/spp/' is active on '/spp/libro', but '/s' never matches '/spp'
// and sibling prefixes never light together.
// ---------------------------------------------------------------------------
export const rutaActiva = (path, href) => {
  const base = href.replace(/\/$/, '');
  // La raiz es el caso limite: su base queda vacia, y un prefijo vacio
  // coincide con TODO - el Home del menu se encendia en cada pagina.
  if (!base) return path === '/' || path === '';
  return path === href || path === base || path.startsWith(`${base}/`);
};

// NUESTRO. Nuestros tableros, los que no existen aguas arriba.
//
// La cromatica adoptada trae dos piezas que dan por hecho que toda pagina es
// un tablero suyo: la pildora de contexto (cartera, fechas, fuente) y la
// sincronizacion de esos filtros con la URL. Ninguna de las dos tiene sentido
// aqui, y la sincronizacion ademas le reescribia la URL a la pagina.
//
// Estan listados en UN sitio para que el tercer modulo no vuelva a requerir
// tocar dos componentes que son de aguas arriba, donde cada parche nuestro
// hay que volver a aplicarlo en la siguiente actualizacion.
const NUESTROS = ['/spp/', '/tradebook/'];
export const esTableroNuestro = (path) =>
  NUESTROS.some((base) => rutaActiva(path, base));
