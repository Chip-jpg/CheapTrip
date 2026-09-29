/** Two small SVG charts for the deal detail (Round 8 replaces their look). */
import type { FareComparison } from "../api";
import { euros } from "../format";

const W = 560, H = 180, PAD = 28;

/** Prices from €0 up, so a €1 difference doesn't look like a big one. */
function scale(values: number[]): (v: number) => number {
  const hi = Math.max(...values) * 1.1 || 1;
  return (v) => H - PAD - (v / hi) * (H - 2 * PAD);
}

/** Similar fares departing within the window: this one highlighted, the median as a line. */
export function ComparisonChart({ comparison }: { comparison: FareComparison }) {
  const { points, median } = comparison;
  if (points.length === 0) return <p className="muted">Not enough similar fares yet to compare.</p>;
  const y = scale([...points.map((p) => p.price), ...(median !== null ? [median] : [])]);
  const x = (i: number) => PAD + (points.length === 1 ? (W - 2 * PAD) / 2 : (i / (points.length - 1)) * (W - 2 * PAD));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img"
         aria-label={`${points.length} similar fares, median ${euros(median)}`}>
      {median !== null && (
        <g>
          <line x1={PAD} x2={W - PAD} y1={y(median)} y2={y(median)} className="median" />
          <text x={W - PAD} y={y(median) - 4} textAnchor="end" className="label">usual {euros(median)}</text>
        </g>
      )}
      {points.map((p, i) => (
        <circle key={`${p.route}-${p.depart}-${i}`} cx={x(i)} cy={y(p.price)} r={p.is_this ? 6 : 3.5}
                className={p.is_this ? "this" : "other"}>
          <title>{`${p.route} ${p.depart}: ${euros(p.price)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

/** This exact fare's price each time it was seen. */
export function HistoryChart({ history }: { history: FareComparison["history"] }) {
  if (history.length < 2) return <p className="muted">Seen {history.length === 1 ? "once" : "not yet"}: no price changes to show.</p>;
  const y = scale(history.map((h) => h.price));
  const x = (i: number) => PAD + (i / (history.length - 1)) * (W - 2 * PAD);
  const path = history.map((h, i) => `${i ? "L" : "M"}${x(i)},${y(h.price)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label={`Price seen ${history.length} times`}>
      <path d={path} className="line" />
      {history.map((h, i) => (
        <circle key={h.at} cx={x(i)} cy={y(h.price)} r={3.5} className="other"><title>{`${h.at}: ${euros(h.price)}`}</title></circle>
      ))}
    </svg>
  );
}
