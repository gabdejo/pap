// web/apps/dashboards/components/Sidebar.js
// ---------------------------------------------------------------------------
// Collapsible text-only sidebar. 48px collapsed (active items still show their
// blue left border), expands to 200px on hover or via the toggle (persisted in
// localStorage by Shell). Active page gets a blue left border. Future tools are
// shown at 30% opacity with a "Coming soon" tooltip (not clickable). Nav links
// carry the current query so the dashboard context sticks across pages.
// ---------------------------------------------------------------------------
'use client';

import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
// NUESTRO: nuestros tableros tienen sub-rutas (/spp/libro, /tradebook/carga)
// y la comparacion exacta dejaria el menu apagado dentro de ellas.
import { rutaActiva } from '../lib/rutas';

const ITEMS = [
  { href: '/prices/', label: 'Prices', icon: '↗' },
  { href: '/positioning/', label: 'Positioning', icon: '▦' },
  { href: '/contribution/', label: 'Contribution', icon: '≡' },
  { href: '/comparacion/', label: 'Comparison', icon: '⇄' },
  // NUESTRO: los dos tableros que no existen aguas arriba.
  { href: '/spp/', label: 'Valor Cuota SPP', icon: '◷' },
  { href: '/tradebook/', label: 'Tradebook', icon: '▤' },
];
const SOON = [
  { label: 'Attribution', icon: '⊞' },
  { label: 'Risk', icon: '△' },
];

export default function Sidebar({ expanded, onToggle }) {
  const path = usePathname();
  const params = useSearchParams();
  const qs = params.toString();
  const q = qs ? `?${qs}` : '';
  const isActive = (href) => rutaActiva(path, href);   // NUESTRO: incluye sub-rutas

  return (
    <aside className={`sidebar ${expanded ? 'expanded' : ''}`}>
      {/* Home / menu — top item, divider below separates it from the dashboards */}
      <Link href={`/${q}`} className={`side-item ${isActive('/') ? 'active' : ''}`}>
        <span className="icon" aria-hidden="true">⌂</span>
        <span className="label">Home</span>
      </Link>

      <div className="side-sep" />

      {ITEMS.map((it) => (
        <Link key={it.href} href={`${it.href}${q}`} className={`side-item ${isActive(it.href) ? 'active' : ''}`}>
          <span className="icon" aria-hidden="true">{it.icon}</span>
          <span className="label">{it.label}</span>
        </Link>
      ))}

      <div className="side-sep" />

      {SOON.map((it) => (
        <div key={it.label} className="side-item soon" title="Coming soon" aria-disabled="true">
          <span className="icon" aria-hidden="true">{it.icon}</span>
          <span className="label">{it.label}</span>
        </div>
      ))}

      {/* divider + centered outlined toggle, pinned to the panel bottom */}
      <div className="side-toggle-sep" />
      <button className="side-toggle" onClick={onToggle} aria-label="Toggle sidebar"
        title={expanded ? 'Collapse' : 'Expand'}>
        {/* inline SVG chevron: stroke-width 2 matches the circle's 2px border */}
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points={expanded ? '8,2 4,6 8,10' : '4,2 8,6 4,10'} />
        </svg>
      </button>
    </aside>
  );
}
