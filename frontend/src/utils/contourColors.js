/**
 * Elevation → colour interpolation (design.md §4).
 * 3-stop gradient: green (#2D6A4F) → brown (#A38367) → red-brown (#A4342E).
 */

const STOPS = [
  [0x2D, 0x6A, 0x4F], // low  — green
  [0xA3, 0x83, 0x67], // mid  — brown
  [0xA4, 0x34, 0x2E], // high — red-brown
];

function lerp(a, b, t) {
  return Math.round(a + (b - a) * t);
}

export function getContourColor(elevation, minElev, maxElev) {
  if (maxElev === minElev) return '#A38367';
  const t = Math.max(0, Math.min(1, (elevation - minElev) / (maxElev - minElev)));

  // Two-segment interpolation
  let from, to, localT;
  if (t < 0.5) {
    from = STOPS[0]; to = STOPS[1]; localT = t * 2;
  } else {
    from = STOPS[1]; to = STOPS[2]; localT = (t - 0.5) * 2;
  }

  const r = lerp(from[0], to[0], localT);
  const g = lerp(from[1], to[1], localT);
  const b = lerp(from[2], to[2], localT);

  return `rgb(${r},${g},${b})`;
}
