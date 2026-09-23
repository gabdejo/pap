// web/apps/dashboards/app/layout.js
// ---------------------------------------------------------------------------
// Root layout: self-hosted fonts (Inter + IBM Plex Mono via next/font), the
// global DashboardProvider (portfolio/period/source context), and the app Shell
// (header + sidebar + main). The provider uses useSearchParams, which must sit
// inside a Suspense boundary for static export.
// ---------------------------------------------------------------------------
import './globals.css';
// NUESTRO: lo nuestro de alcance global va en una hoja aparte y se carga
// despues de globals.css, que se queda verbatim como viene de aguas arriba.
import './estilos/ajustes.css';
import { Suspense } from 'react';
import { Inter, IBM_Plex_Mono } from 'next/font/google';
import DashboardProvider from '../components/DashboardProvider';
import Shell from '../components/Shell';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter', display: 'swap' });
const plex = IBM_Plex_Mono({ subsets: ['latin'], weight: ['400', '500', '600'], variable: '--font-plex', display: 'swap' });

export const metadata = {
  title: 'Profuturo Analytics',
  description: 'Investment analytics dashboards',
};

// Set data-theme before paint (from localStorage, default dark) to avoid a flash.
const THEME_INIT = `(function(){try{var t=localStorage.getItem('theme')||'dark';document.documentElement.setAttribute('data-theme',t);}catch(e){document.documentElement.setAttribute('data-theme','dark');}})();`;

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={`${inter.variable} ${plex.variable}`}>
      <body>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
        <Suspense fallback={null}>
          <DashboardProvider>
            <Shell>{children}</Shell>
          </DashboardProvider>
        </Suspense>
      </body>
    </html>
  );
}
