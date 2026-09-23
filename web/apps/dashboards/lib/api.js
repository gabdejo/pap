// web/apps/dashboards/lib/api.js
// ---------------------------------------------------------------------------
// Tiny fetch helper. BASE is empty in production (relative /api, same origin as
// FastAPI) and the uvicorn URL in dev (via NEXT_PUBLIC_API_BASE).
// ---------------------------------------------------------------------------
// Exported: lib/spp.js builds download URLs and mutating requests on the
// same origin - the API base must have exactly one owner.
export const BASE = process.env.NEXT_PUBLIC_API_BASE || '';

export async function apiGet(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new Error(`API ${path} -> ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// Format a 0..1 fraction as a percentage string.
export function pct(x, digits = 2) {
  if (x === null || x === undefined || Number.isNaN(x)) return '-';
  return `${(x * 100).toFixed(digits)}%`;
}

// Format a number with thousands separators.
export function num(x, digits = 0) {
  if (x === null || x === undefined || Number.isNaN(x)) return '-';
  return x.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
