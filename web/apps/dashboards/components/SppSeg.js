
// web/apps/dashboards/components/SppSeg.js
// ---------------------------------------------------------------------------
// Generic segmented button group for the SPP tablero (metric, fund, window,
// scale, AFP multi-select). Only knows how to draw a group and report a
// press; what to do with it is the mounting page's business.
// colorOf: brand color per value - off state tints the border, on state
// fills the whole button.
// ---------------------------------------------------------------------------
'use client';

export default function SppSeg({ items, value, onChange, multi = false, colorOf = null }) {
  const isOn = (v) => (multi ? value.includes(v) : value === v);
  return (
    <div className="spp-seg">
      {items.map(([label, v]) => {
        const on = isOn(v);
        const c = colorOf ? colorOf(v) : null;
        const style = c
          ? { borderColor: c, color: on ? '#fff' : c, background: on ? c : 'transparent' }
          : undefined;
        return (
          <button key={String(v)} type="button" style={style}
            className={`spp-seg-btn ${on ? 'on' : ''}`}
            aria-pressed={on} onClick={() => onChange(v)}>
            {label}
          </button>
        );
      })}
    </div>
  );
}
