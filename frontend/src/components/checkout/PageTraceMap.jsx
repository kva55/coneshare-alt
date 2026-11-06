/**
 * One page of the session report, drawn on a blank page of the right shape: heat = time the cursor
 * spent in each cell, line = the averaged path, orange strokes = diagonal movements. The document itself
 * is deliberately not drawn here, so the report cannot be used to read it.
 */
export function PageTraceMap({ page, grid, show }) {
  const ratio = page.width && page.height ? page.height / page.width : 1.4;
  const H = Math.round(1000 * ratio);
  const cw = 1000 / grid.cols;
  const ch = H / grid.rows;
  const path = page.path.map(([x, y]) => `${x},${(y / 1000) * H}`).join(' ');
  return (
    <svg viewBox={`0 0 1000 ${H}`} role="img" data-testid={`trace-map-${page.page}`}
         aria-label={`Page ${page.page}: cursor heat map`}
         className="w-full rounded-md border bg-white shadow-sm dark:bg-gray-100">
      {show.heat && page.heat.map((row, r) => row.map((v, c) => v > 0 && (
        <rect key={`${r}-${c}`} x={c * cw} y={r * ch} width={cw} height={ch} fill="#4f46e5"
              opacity={Math.max(0.08, v / (page.heat_max || 1)) * 0.85} />
      )))}
      {show.diagonal && page.diagonal.map(([x1, y1, x2, y2], i) => (
        <line key={i} x1={x1} y1={(y1 / 1000) * H} x2={x2} y2={(y2 / 1000) * H} stroke="#ea580c" strokeWidth="5" strokeLinecap="round" opacity="0.8" />
      ))}
      {show.path && page.path.length > 1 && (
        <polyline points={path} fill="none" stroke="#059669" strokeWidth="6" strokeLinejoin="round" strokeDasharray="14 8" />
      )}
      {show.path && page.path.length > 0 && (
        <circle cx={page.path[0][0]} cy={(page.path[0][1] / 1000) * H} r="14" fill="#059669"><title>Start of the averaged path</title></circle>
      )}
    </svg>
  );
}
