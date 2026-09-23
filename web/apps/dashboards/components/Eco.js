
// web/apps/dashboards/components/Eco.js
// ---------------------------------------------------------------------------
// The one-line result of an action, in the SPP write views.
//
// Every form had its own echo and they all looked the same whatever had
// happened: "Cargado en modo faltantes" and "La carga falló y no se guardó
// nada" rendered as identical bold gray text. Success and failure must be
// distinguishable at a glance - that is the whole job of this line.
//
// Accepts either a plain string (treated as success, for the callers that
// only ever say good news) or {ok, texto}.
// ---------------------------------------------------------------------------
'use client';

export default function Eco({ eco, style }) {
  if (!eco) return null;
  const { ok, texto } = typeof eco === 'string' ? { ok: true, texto: eco } : eco;
  if (!texto) return null;
  return (
    <p className="page-sub" style={{ marginTop: 10, ...style }}>
      <b className={ok ? 'pos' : 'neg'}>{ok ? '✓' : '✕'}</b>{' '}
      <b>{texto}</b>
    </p>
  );
}
