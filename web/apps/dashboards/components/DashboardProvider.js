// web/apps/dashboards/components/DashboardProvider.js
// ---------------------------------------------------------------------------
// Global dashboard context: portfolio, period, and source — the shared state
// driven by the header and consumed by every page.
//
// period is one of the quick keys ('1M'|'3M'|'YTD'|'1Y') OR 'Custom'. In Custom
// mode the range comes from explicit customFrom/customTo dates (the header's
// inline date inputs). All of it persists to URL search params (bookmarkable)
// and localStorage (restored on load); the URL is updated with router.replace.
// ---------------------------------------------------------------------------
'use client';

import { createContext, useContext, useEffect, useState, useMemo, useCallback } from 'react';
import { useRouter, usePathname, useSearchParams } from 'next/navigation';
import { apiGet } from '../lib/api';
import { periodToRange, PERIODS, DEFAULT_PERIOD } from '../lib/period';
import { esTableroNuestro } from '../lib/rutas';

const Ctx = createContext(null);
export const useDashboard = () => useContext(Ctx);

const LS_KEY = 'profuturo.dashctx';
const DEFAULTS = { portfolioId: '', period: DEFAULT_PERIOD, source: 'bloomberg', customFrom: '', customTo: '' };

export default function DashboardProvider({ children }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const [portfolios, setPortfolios] = useState([]);
  const [ctx, setCtx] = useState(DEFAULTS);
  const [hydrated, setHydrated] = useState(false);

  // hydrate from URL -> localStorage -> defaults (client only)
  useEffect(() => {
    let stored = {};
    try { stored = JSON.parse(localStorage.getItem(LS_KEY) || '{}'); } catch { /* ignore */ }
    const period = params.get('period') || stored.period || DEFAULTS.period;
    setCtx({
      portfolioId: params.get('portfolio') || stored.portfolioId || DEFAULTS.portfolioId,
      period,
      source: params.get('source') || stored.source || DEFAULTS.source,
      customFrom: params.get('from') || stored.customFrom || DEFAULTS.customFrom,
      customTo: params.get('to') || stored.customTo || DEFAULTS.customTo,
    });
    setHydrated(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // load portfolios once; pick a default portfolio if none chosen yet
  useEffect(() => {
    apiGet('/api/portfolios')
      .then((rows) => {
        setPortfolios(rows);
        setCtx((c) => (c.portfolioId ? c : { ...c, portfolioId: rows.length ? String(rows[0].portfolio_id) : '' }));
      })
      .catch(() => { /* header just shows Loading… */ });
  }, []);

  // sync context -> URL + localStorage (after hydration)
  useEffect(() => {
    if (!hydrated) return;
    try { localStorage.setItem(LS_KEY, JSON.stringify(ctx)); } catch { /* ignore */ }
    // NUESTRO: el monitor SPP no lee cartera, periodo ni fuente, asi que
    // estamparlos en su URL solo produce enlaces y marcadores cargados de
    // parametros que ninguna de sus vistas mira. El localStorage de arriba
    // sigue guardando el contexto para los demas tableros.
    if (esTableroNuestro(pathname)) return;
    const sp = new URLSearchParams();
    if (ctx.portfolioId) sp.set('portfolio', ctx.portfolioId);
    sp.set('period', ctx.period);
    sp.set('source', ctx.source);
    if (ctx.period === 'Custom') {
      if (ctx.customFrom) sp.set('from', ctx.customFrom);
      if (ctx.customTo) sp.set('to', ctx.customTo);
    }
    router.replace(`${pathname}?${sp.toString()}`, { scroll: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx, hydrated, pathname]);

  // setPeriod: a quick key switches back to button mode; 'Custom' seeds the
  // custom dates from the currently-resolved range so the inputs start populated.
  const setPeriod = useCallback((v) => setCtx((c) => {
    if (v === 'Custom') {
      const seed = (c.customFrom && c.customTo)
        ? { from: c.customFrom, to: c.customTo }
        : periodToRange(c.period === 'Custom' ? DEFAULT_PERIOD : c.period);
      return { ...c, period: 'Custom', customFrom: c.customFrom || seed.from, customTo: c.customTo || seed.to };
    }
    return { ...c, period: v };
  }), []);

  const setPortfolioId = useCallback((v) => setCtx((c) => ({ ...c, portfolioId: String(v) })), []);
  const setSource = useCallback((v) => setCtx((c) => ({ ...c, source: v })), []);
  const setCustomFrom = useCallback((v) => setCtx((c) => ({ ...c, period: 'Custom', customFrom: v })), []);
  const setCustomTo = useCallback((v) => setCtx((c) => ({ ...c, period: 'Custom', customTo: v })), []);

  const range = useMemo(() => {
    if (ctx.period === 'Custom') {
      if (ctx.customFrom && ctx.customTo) return { from: ctx.customFrom, to: ctx.customTo };
      return periodToRange(DEFAULT_PERIOD); // fallback while a date is missing
    }
    return periodToRange(ctx.period);
  }, [ctx.period, ctx.customFrom, ctx.customTo]);

  const portfolio = useMemo(
    () => portfolios.find((p) => String(p.portfolio_id) === String(ctx.portfolioId)) || null,
    [portfolios, ctx.portfolioId],
  );

  const value = {
    portfolios, portfolio,
    portfolioId: ctx.portfolioId, period: ctx.period, source: ctx.source,
    customFrom: ctx.customFrom, customTo: ctx.customTo,
    range, periods: PERIODS, hydrated,
    setPortfolioId, setPeriod, setSource, setCustomFrom, setCustomTo,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
