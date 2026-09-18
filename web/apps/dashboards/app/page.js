// web/apps/dashboards/app/page.js
// ---------------------------------------------------------------------------
// Home / menu — the default landing route. Two stacked sections:
//   1. Hero bento row: a brand "Platform Overview" card (live AUM / period
//      return / duration + a cumulative-return area chart) and a Quick Access
//      list of the dashboards.
//   2. Four module-preview cards, each a live snapshot linking into its module.
// Live figures come from the same endpoints the module pages already use
// (holdings, contribution, prices). Positioning L1 + duration mirror the
// modelled Positioning matrix (synthetic). Comparison has no persisted
// selection, so it shows the invitation state.
// ---------------------------------------------------------------------------
'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { apiGet, num, pct } from '../lib/api';
import { useDashboard } from '../components/DashboardProvider';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const fmtDate = (iso) => { const [y, m, d] = (iso || '').split('-'); return y ? `${d} ${MONTHS[+m - 1]} ${y}` : (iso || '—'); };
const bps = (x) => `${x >= 0 ? '+' : ''}${Math.round(x * 10000)} bps`;
const sgnPct = (x) => (x == null ? '—' : `${x >= 0 ? '+' : ''}${pct(x)}`);

// static level-1 positioning snapshot (matches the modelled Positioning matrix)
const POS_L1 = [
  { k: 'RF — Fixed Income', v: 32.1 },
  { k: 'RV — Equities', v: 54.2 },
  { k: 'Alts', v: 8.2 },
  { k: 'Cash', v: 4.8 },
];

// deterministic synthetic YTD cumulative-return line for the hero chart
const HERO = Array.from({ length: 60 }, (_, i) => {
  const t = i / 59;
  return 3.24 * t + Math.sin(i * 0.5) * 0.6 + Math.sin(i * 0.13) * 1.4;
});

function Skel({ w = 80, h = 14 }) {
  return <span className="skel" style={{ width: w, height: h }} />;
}

// Hero area chart — a sparkline elevated to chart size (line + faint fill).
function HeroArea({ data, w = 640, h = 100 }) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = (max - min) || 1;
  const n = data.length;
  const px = (i) => ((i / (n - 1)) * w).toFixed(1);
  const py = (v) => (h - 6 - ((v - min) / span) * (h - 12)).toFixed(1);
  const pts = data.map((v, i) => `${px(i)},${py(v)}`);
  const line = `M${pts.join(' L')}`;
  const area = `${line} L${px(n - 1)},${h} L${px(0)},${h} Z`;
  return (
    <svg className="hero-chart" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={area} fill="rgba(255,255,255,0.08)" stroke="none" />
      <path d={line} fill="none" stroke="rgba(255,255,255,0.9)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

// 3-point price sparkline (dots) for the Price Viewer card.
function DotSpark({ data }) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = (max - min) || 1;
  const n = data.length;
  return (
    <svg width="44" height="16" viewBox="0 0 44 16" aria-hidden="true">
      {data.map((v, i) => (
        <circle key={i} cx={4 + (i / (n - 1)) * 36} cy={14 - ((v - min) / span) * 12} r="2.2" fill="var(--positive)" />
      ))}
    </svg>
  );
}

const MODULES = [
  { key: 'prices', name: 'Price Viewer', href: '/prices/', status: 'Live · Bloomberg + SBS', live: true },
  { key: 'positioning', name: 'Positioning', href: '/positioning/', status: 'Q2 2026 · PRO 1 / 2 / 3', live: true },
  { key: 'contribution', name: 'Contribution', href: '/contribution/', status: 'YTD · Fondo 1', live: true },
  { key: 'comparison', name: 'Comparison', href: '/comparacion/', status: 'Select securities to begin', live: false },
  // NUESTRO: los dos tableros que no existen aguas arriba.
  { key: 'spp', name: 'Valor Cuota SPP', href: '/spp/', status: 'Diario · fuente SBS', live: true },
  { key: 'tradebook', name: 'Tradebook', href: '/tradebook/', status: 'Operaciones de la mesa', live: true },
];

export default function Home() {
  const { portfolioId, range, source } = useDashboard();
  const [aum, setAum] = useState(undefined);       // undefined = loading, null = failed
  const [contrib, setContrib] = useState(undefined);
  const [px, setPx] = useState(undefined);
  const asOf = fmtDate(range?.to);

  // AUM + contribution (same calls the positioning / contribution pages make)
  useEffect(() => {
    if (!portfolioId) return undefined;
    let alive = true;
    apiGet(`/api/portfolios/${portfolioId}/holdings?date=${range.to}`)
      .then((d) => alive && setAum(d?.total_market_value ?? null))
      .catch(() => alive && setAum(null));
    apiGet(`/api/portfolios/${portfolioId}/contribution?from=${range.from}&to=${range.to}&source=${source}`)
      .then((d) => alive && setContrib(d))
      .catch(() => alive && setContrib(null));
    return () => { alive = false; };
  }, [portfolioId, range.from, range.to, source]);

  // Price Viewer snapshot: default (first) security + its primary price series
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const secs = await apiGet('/api/securities?limit=1');
        const s = secs?.[0];
        if (!s) { if (alive) setPx(null); return; }
        const data = await apiGet(`/api/prices?entity_id=${s.entity_id}&from=${range.from}&to=${range.to}`);
        const series = data?.series?.find((x) => x.source === 'bloomberg') || data?.series?.[0];
        const points = (series?.points || []).filter((p) => p.price != null);
        if (!points.length) { if (alive) setPx(null); return; }
        const first = points[0].price;
        const last = points[points.length - 1].price;
        if (alive) setPx({ name: s.ticker || s.display_name, price: last, ret: first ? last / first - 1 : 0, spark: points.slice(-3).map((p) => p.price) });
      } catch { if (alive) setPx(null); }
    })();
    return () => { alive = false; };
  }, [range.from, range.to]);

  // contribution card: top 2 contributors + top detractor
  const holdings = contrib?.holdings || [];
  const sorted = [...holdings].sort((a, b) => b.contribution - a.contribution);
  const cTop = sorted.slice(0, 2);
  const cBot = sorted.length ? sorted[sorted.length - 1] : null;
  const cRows = [...cTop, cBot].filter(Boolean);
  const cMax = cRows.length ? Math.max(...cRows.map((h) => Math.abs(h.contribution))) || 1 : 1;

  return (
    <div>
      <div className="page-brand-block">
        <div className="page-brand-name">Profuturo Analytics</div>
        <div className="page-dashboard-title">Overview</div>
      </div>

      {/* ===== Section 1 — hero bento row ===== */}
      <div className="home-hero">
        {/* LEFT — platform overview (brand accent) */}
        <div className="hero-brand-card">
          <div className="hero-stats">
            <div className="hero-stat">
              <div className="hero-stat-lbl">AUM Total</div>
              <div className="hero-stat-val">{aum === undefined ? <Skel w={96} h={20} /> : aum === null ? '—' : `S/ ${num(aum, 0)}`}</div>
              <div className="hero-stat-meta">Fondo 1</div>
            </div>
            <div className="hero-stat-div" />
            <div className="hero-stat">
              <div className="hero-stat-lbl">Period Return</div>
              <div className="hero-stat-val">{contrib === undefined ? <Skel w={80} h={20} /> : contrib === null ? '—' : bps(contrib.portfolio_return ?? 0)}</div>
              <div className="hero-stat-meta">YTD</div>
            </div>
            <div className="hero-stat-div" />
            <div className="hero-stat">
              <div className="hero-stat-lbl">Duration</div>
              <div className="hero-stat-val">5.84yr</div>
              <div className="hero-stat-meta">PRO1</div>
            </div>
          </div>

          <div className="hero-rule" />

          <HeroArea data={HERO} />

          <div className="hero-foot">
            <span className="hero-updated">Last updated: {asOf}</span>
          </div>
          <div className="hero-arc" aria-hidden="true" />
        </div>

        {/* RIGHT — quick access */}
        <div className="qa-card">
          <div className="qa-title">Quick Access</div>
          {MODULES.filter((m) => m.live).map((m) => (
            <Link key={m.key} href={m.href} className="qa-row">
              <span>{m.name}</span>
              <span className="qa-arrow">→</span>
            </Link>
          ))}
          <div className="qa-div" />
          <div className="qa-soon-label">Coming Soon</div>
          <div className="qa-soon-row"><span>Attribution</span><span className="qa-planned">(planned)</span></div>
          <div className="qa-soon-row"><span>Risk</span><span className="qa-planned">(planned)</span></div>
        </div>
      </div>

      {/* ===== Section 2 — module preview cards ===== */}
      <div className="mod-grid">
        {MODULES.map((m) => (
          <Link key={m.key} href={m.href} className="mod-card">
            <div className="mod-head">
              <span className="mod-name">{m.name}</span>
              <span className="mod-open">Open →</span>
            </div>
            <div className="mod-div" />

            <div className="mod-body">
              {m.key === 'prices' && (
                px === undefined ? <><Skel w={90} /><Skel w={70} h={20} /><Skel w={60} /></>
                  : px === null ? <span className="mod-dash">—</span>
                    : (
                      <>
                        <div className="mod-sec-name">{px.name}</div>
                        <div className="mod-sec-price">{num(px.price, 2)}</div>
                        <div className="mod-sec-row">
                          <span className={px.ret >= 0 ? 'pos' : 'neg'}>{sgnPct(px.ret)}</span>
                          <DotSpark data={px.spark} />
                        </div>
                      </>
                    )
              )}

              {m.key === 'positioning' && (
                <div className="mod-pos">
                  {POS_L1.map((r) => (
                    <div key={r.k} className="mod-pos-row"><span>{r.k}</span><span className="mod-mono">{r.v.toFixed(1)}%</span></div>
                  ))}
                </div>
              )}

              {m.key === 'contribution' && (
                contrib === undefined ? <><Skel w="100%" /><Skel w="100%" /><Skel w="100%" /></>
                  : contrib === null ? <span className="mod-dash">—</span>
                    : (
                      <div className="mod-contrib">
                        {cRows.map((h, i) => {
                          const pos = h.contribution >= 0;
                          return (
                            <div key={i} className="mod-c">
                              <div className="mod-c-top">
                                <span className="mod-c-name" style={{ color: pos ? 'var(--positive)' : 'var(--negative)' }}>{pos ? '↑' : '↓'} {h.display_name}</span>
                                <span className="mod-mono" style={{ color: pos ? 'var(--positive)' : 'var(--negative)' }}>{bps(h.contribution)}</span>
                              </div>
                              <div className="mod-c-bar"><span style={{ width: `${(Math.abs(h.contribution) / cMax) * 100}%`, background: pos ? 'var(--positive)' : 'var(--negative)' }} /></div>
                            </div>
                          );
                        })}
                        <div className="mod-c-total">Total: <b className={contrib.portfolio_return >= 0 ? 'pos' : 'neg'}>{bps(contrib.portfolio_return ?? 0)}</b></div>
                      </div>
                    )
              )}

              {m.key === 'comparison' && (
                <div className="mod-cmp">
                  <div className="mod-cmp-plus">+</div>
                  <div className="mod-cmp-msg">Select securities to compare</div>
                </div>
              )}
            </div>

            <div className="mod-foot">
              <span className={`mod-dot ${m.live ? 'live' : 'stale'}`} />
              <span className="mod-status">{m.status}</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
