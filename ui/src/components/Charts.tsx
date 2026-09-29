/**
 * The deal charts, drawn as in the design: a card's fare-range sparkline, the
 * detail panel's "How this fare compares" scatter and "This fare over time"
 * step line. Prices always start at €0, so a €1 difference doesn't look big.
 */
import type { FareComparison } from "../api";
import { euros, moment, shortDay, when } from "../format";

const DAY_MS = 86_400_000;

function top(values: number[]): number {
  return Math.max(...values, 1) * 1.15;
}

/**
 * Similar fares by departure date, this one marked (a card's "30-day fare range").
 * Its scale starts a little below the lowest fare, so the shape shows in 32 px
 * without making cents look like a drop.
 */
export function Sparkline({ comparison, className = "" }: { comparison: FareComparison; className?: string }) {
  const points = comparison.points;
  const W = 200, H = 32;
  const values = [...points.map((p) => p.price), ...(comparison.median !== null ? [comparison.median] : [])];
  const hi = Math.max(...values) * 1.05, lo = Math.min(...values) * 0.6;
  const y = (price: number) => H - 2 - ((price - lo) / (hi - lo || 1)) * (H - 4);
  const x = (i: number) => (points.length === 1 ? W / 2 : (i / (points.length - 1)) * W);
  const path = points.map((p, i) => `${i ? "L" : "M"} ${x(i).toFixed(1)},${y(p.price).toFixed(1)}`).join(" ");
  const current = points.findIndex((p) => p.is_this);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={`h-8 w-full overflow-visible ${className}`}
         role="img" aria-label={`${points.length} similar fares${comparison.median ? `, usually ${euros(comparison.median)}` : ""}`}>
      {comparison.median !== null && (
        <line x1="0" x2={W} y1={y(comparison.median)} y2={y(comparison.median)} stroke="var(--c-tertiary)"
              strokeOpacity="0.5" strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
      )}
      <path d={path} fill="none" stroke="var(--c-secondary)" strokeWidth="2" strokeLinecap="round"
            strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      {current >= 0 && <circle cx={x(current)} cy={y(points[current].price)} r="3.5" fill="var(--c-secondary)" />}
    </svg>
  );
}

/** Similar fares departing within ±30 days (dots), the usual price (band) and this fare (highlighted). */
export function FareScatter({ comparison, depart }: { comparison: FareComparison; depart: string }) {
  const W = 480, H = 176, LEFT = 8, RIGHT = 8, TOP = 12, BOTTOM = 22;
  const centre = moment(`${depart}T12:00:00`).getTime();
  const span = comparison.window_days * DAY_MS;
  const hi = top([...comparison.points.map((p) => p.price), comparison.median ?? 0]);
  const x = (iso: string) => LEFT + ((moment(`${iso}T12:00:00`).getTime() - (centre - span)) / (2 * span)) * (W - LEFT - RIGHT);
  const y = (price: number) => H - BOTTOM - (price / hi) * (H - TOP - BOTTOM);
  const ticks = [-1, -0.5, 0, 0.5, 1].map((f) => new Date(centre + f * span).toISOString().slice(0, 10));
  const current = comparison.points.find((p) => p.is_this);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
         aria-label={`${comparison.points.length} similar fares${comparison.median ? `, usual price ${euros(comparison.median)}` : ""}`}>
      {[0.25, 0.5, 0.75].map((f) => (
        <line key={f} x1={LEFT} x2={W - RIGHT} y1={TOP + f * (H - TOP - BOTTOM)} y2={TOP + f * (H - TOP - BOTTOM)}
              stroke="var(--c-chart-grid)" strokeWidth="1" />
      ))}
      <line x1={LEFT} x2={W - RIGHT} y1={H - BOTTOM} y2={H - BOTTOM} stroke="var(--c-outline-variant)" strokeWidth="1" />
      {comparison.median !== null && (
        <g>
          <rect x={LEFT} width={W - LEFT - RIGHT} y={y(comparison.median) - 7} height="14"
                fill="var(--c-tertiary)" fillOpacity="0.1" />
          <line x1={LEFT} x2={W - RIGHT} y1={y(comparison.median)} y2={y(comparison.median)} stroke="var(--c-tertiary)"
                strokeOpacity="0.7" strokeDasharray="4 3" />
          <text x={W - RIGHT - 4} y={y(comparison.median) - 10} textAnchor="end" fill="var(--c-tertiary)"
                className="font-mono" fontSize="11">Usual {euros(comparison.median)}</text>
        </g>
      )}
      {comparison.points.filter((p) => !p.is_this).map((p, i) => (
        <circle key={`${p.route}-${p.depart}-${i}`} cx={x(p.depart)} cy={y(p.price)} r="4.5"
                fill="var(--c-outline)" fillOpacity="0.75">
          <title>{`${p.route} · ${shortDay(p.depart)} · ${euros(p.price)}`}</title>
        </circle>
      ))}
      {current && (
        <g>
          <circle cx={x(current.depart)} cy={y(current.price)} r="11" fill="var(--c-secondary)" fillOpacity="0.2" />
          <circle cx={x(current.depart)} cy={y(current.price)} r="6" fill="var(--c-secondary)" />
          <text x={x(current.depart)} y={y(current.price) - 14} textAnchor="middle" fill="var(--c-secondary)"
                className="font-mono" fontSize="12" fontWeight="600">{euros(current.price)}</text>
        </g>
      )}
      {ticks.map((iso, i) => (
        <text key={iso} x={LEFT + (i / 4) * (W - LEFT - RIGHT)} y={H - 6} fontSize="11"
              textAnchor={i === 0 ? "start" : i === 4 ? "end" : "middle"}
              fill={i === 2 ? "var(--c-secondary)" : "var(--c-outline)"} fontWeight={i === 2 ? 600 : 400}>
          {shortDay(iso)}{i === 2 ? " (this)" : ""}
        </text>
      ))}
    </svg>
  );
}

/** This exact fare's price at every search that saw it: first seen, the lowest, now. */
export function PriceSteps({ history }: { history: FareComparison["history"] }) {
  const W = 300, H = 80;
  const hi = top(history.map((h) => h.price));
  const x = (i: number) => (history.length === 1 ? W / 2 : 4 + (i / (history.length - 1)) * (W - 8));
  const y = (price: number) => H - 6 - (price / hi) * (H - 14);
  let path = "";
  history.forEach((h, i) => {
    path += i === 0 ? `M ${x(i)},${y(h.price)}` : ` L ${x(i)},${y(history[i - 1].price)} L ${x(i)},${y(h.price)}`;
  });
  const lowest = history.reduce((lo, h, i) => (h.price < history[lo].price ? i : lo), 0);
  const last = history.length - 1;
  return (
    <div className="flex flex-col gap-space-sm">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-24 w-full overflow-visible" role="img"
           aria-label={`Seen ${history.length} times, from ${euros(history[0].price)} to ${euros(history[last].price)}`}>
        {[0.25, 0.6, 0.95].map((f) => (
          <line key={f} x1="0" x2={W} y1={f * H} y2={f * H} stroke="var(--c-chart-grid)" strokeWidth="1" />
        ))}
        <path d={path} fill="none" stroke="var(--c-primary)" strokeWidth="2.5" />
        {history.map((h, i) => (
          <circle key={`${h.at}-${i}`} cx={x(i)} cy={y(h.price)} r={i === lowest ? 4.5 : 3}
                  fill={i === lowest ? "var(--c-secondary)" : "var(--c-primary)"}>
            <title>{`${when(h.at)}: ${euros(h.price)}`}</title>
          </circle>
        ))}
      </svg>
      <div className="grid grid-cols-3 gap-space-xs text-caption text-outline">
        <div className="flex flex-col">
          <span className="font-mono text-mono-sm text-on-surface">First seen {euros(history[0].price)}</span>
          <span>{when(history[0].at)}</span>
        </div>
        <div className="flex flex-col text-center">
          <span className="font-mono text-mono-sm font-semibold text-secondary">Lowest {euros(history[lowest].price)}</span>
          <span>{when(history[lowest].at)}</span>
        </div>
        <div className="flex flex-col text-right">
          <span className="font-mono text-mono-sm text-on-surface">Now {euros(history[last].price)}</span>
          <span>{when(history[last].at)}</span>
        </div>
      </div>
    </div>
  );
}
