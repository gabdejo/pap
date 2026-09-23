// web/apps/dashboards/components/TradebookTabs.js
// ---------------------------------------------------------------------------
// Barra de sub-pestañas del Tradebook. Mismo papel que SppTabs: una sola
// entrada en la barra lateral (/tradebook/) se abre en las vistas del módulo,
// cada una su propia ruta exportada estáticamente.
//
// Son dos componentes y no uno con la lista por prop porque son dos módulos
// distintos: que hoy se parezcan no significa que sus pestañas cambien juntas,
// y compartirlos obligaría a tocar el Tradebook para añadir una vista al
// monitor de valor cuota.
// ---------------------------------------------------------------------------
'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const TABS = [
  { href: '/tradebook/', label: 'Panel' },
  { href: '/tradebook/carga/', label: 'Registro y carga' },
];

export default function TradebookTabs() {
  const path = usePathname();
  const isActive = (href) => path === href || path === href.replace(/\/$/, '');
  return (
    <div className="spp-tabs">
      {TABS.map((t) => (
        <Link key={t.href} href={t.href}
          className={`spp-tab ${isActive(t.href) ? 'active' : ''}`}>
          {t.label}
        </Link>
      ))}
    </div>
  );
}
