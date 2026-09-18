// web/apps/dashboards/components/ContextPill.js
// ---------------------------------------------------------------------------
// Floating context pill: a centered, sticky bar below the header holding the
// fund selector, the period quick-select, and the source selector. Pure
// presentation over DashboardProvider — all state/persistence (URL + localStorage)
// stays in the provider. The fund segment is hidden on /comparacion/.
// ---------------------------------------------------------------------------
'use client';

import { Fragment, useEffect, useRef, useState } from 'react';
import { usePathname } from 'next/navigation';
import { useDashboard } from './DashboardProvider';
// NUESTRO: que rutas son de nuestros tableros, en un solo sitio.
import { esTableroNuestro } from '../lib/rutas';

const SOURCES = [['bloomberg', 'Bloomberg'], ['fms', 'FMS']];

export default function ContextPill() {
  const d = useDashboard();
  const path = usePathname();
  const [open, setOpen] = useState(null); // 'fund' | 'source' | 'custom' | null
  const [tmpFrom, setTmpFrom] = useState('');
  const [tmpTo, setTmpTo] = useState('');
  const wrapRef = useRef(null);

  // dismiss any dropdown/popover on outside click or Escape
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(null); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(null); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);

  if (!d) return null;
  // NUESTRO: el monitor de valor cuota y el Tradebook no leen cartera, periodo
  // ni fuente - tienen sus propios controles - asi que la pildora entera
  // seria un mando muerto que parece mover la pagina.
  if (esTableroNuestro(path)) return null;
  const showFund = !(path === '/comparacion' || path === '/comparacion/');
  const allPeriods = [...d.periods, 'Custom'];
  const fundLabel = d.portfolio?.display_name || (d.portfolios.length ? '—' : 'Loading…');
  const sourceLabel = (SOURCES.find(([v]) => v === d.source) || [null, d.source])[1];

  const onPeriod = (p) => {
    if (p === 'Custom') {
      setTmpFrom(d.customFrom || d.range.from);
      setTmpTo(d.customTo || d.range.to);
      setOpen('custom');
      return;
    }
    d.setPeriod(p);
    setOpen(null);
  };
  const applyCustom = () => {
    if (tmpFrom) d.setCustomFrom(tmpFrom);
    if (tmpTo) d.setCustomTo(tmpTo);
    setOpen(null);
  };

  return (
    <div className="pill-bar">
      <div className="pill-wrap" ref={wrapRef}>
        <div className="pill">
          {showFund && (
            <>
              <button type="button" className="pill-seg" onClick={() => setOpen(open === 'fund' ? null : 'fund')}>
                {fundLabel} <span className="pill-caret">▾</span>
              </button>
              <span className="pill-div" />
            </>
          )}

          <div className="pill-periods">
            {allPeriods.map((p, i) => (
              <Fragment key={p}>
                {i > 0 && <span className="pill-dot">·</span>}
                <button type="button" className={`pill-period ${d.period === p ? 'on' : ''}`} onClick={() => onPeriod(p)}>{p}</button>
              </Fragment>
            ))}
          </div>

          <span className="pill-div" />
          <button type="button" className="pill-seg" onClick={() => setOpen(open === 'source' ? null : 'source')}>
            {sourceLabel} <span className="pill-caret">▾</span>
          </button>
        </div>

        {open === 'fund' && showFund && (
          <div className="pill-dd pill-dd-left">
            {d.portfolios.length === 0 && <div className="pill-dd-item">Loading…</div>}
            {d.portfolios.map((p) => (
              <button
                key={p.portfolio_id}
                type="button"
                className={`pill-dd-item ${String(p.portfolio_id) === String(d.portfolioId) ? 'on' : ''}`}
                onClick={() => { d.setPortfolioId(p.portfolio_id); setOpen(null); }}
              >
                {p.display_name}
              </button>
            ))}
          </div>
        )}

        {open === 'source' && (
          <div className="pill-dd pill-dd-right">
            {SOURCES.map(([v, lab]) => (
              <button
                key={v}
                type="button"
                className={`pill-dd-item ${d.source === v ? 'on' : ''}`}
                onClick={() => { d.setSource(v); setOpen(null); }}
              >
                {lab}
              </button>
            ))}
          </div>
        )}

        {open === 'custom' && (
          <div className="pill-pop">
            <div className="pill-pop-row">
              <div className="pill-pop-field">
                <label>From</label>
                <input type="date" className="date-input" value={tmpFrom} max={tmpTo || undefined} onChange={(e) => setTmpFrom(e.target.value)} />
              </div>
              <div className="pill-pop-field">
                <label>To</label>
                <input type="date" className="date-input" value={tmpTo} min={tmpFrom || undefined} onChange={(e) => setTmpTo(e.target.value)} />
              </div>
            </div>
            <div className="pill-pop-actions">
              <button type="button" className="btn" onClick={() => setOpen(null)}>Cancel</button>
              <button type="button" className="btn active" onClick={applyCustom}>Apply</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
