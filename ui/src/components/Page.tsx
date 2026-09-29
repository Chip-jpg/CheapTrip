/** Pieces the designed screens share: the page header, tabs, stat tiles, status pills and a trend line. */
import type { ReactNode } from "react";
import { euros } from "../format";
import { Icon } from "./Icon";

/** "● RADAR · DESTINATIONS", the title with a count, a line of explanation, and actions on the right. */
export function PageHeader({ label, title, count, children, actions }: {
  label: string; title: string; count?: string; children?: ReactNode; actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-space-md">
      <div className="flex min-w-0 max-w-3xl flex-col gap-space-xs">
        <span className="flex items-center gap-space-xs font-mono text-mono-sm tracking-[0.15em] text-primary uppercase">
          <span className="h-1.5 w-1.5 rounded-full bg-primary" />{label}
        </span>
        <div className="flex items-center gap-space-sm">
          <h1 className="text-headline-lg text-on-surface">{title}</h1>
          {count && <span className="rounded bg-surface-container-highest px-2 py-0.5 font-mono text-mono-sm font-semibold text-primary">{count}</span>}
        </div>
        {children && <p className="text-body text-on-surface-variant">{children}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-space-sm">{actions}</div>}
    </header>
  );
}

/** A summary tile with a label, a status word, a big value and a bar underneath. */
export function StatTile({ label, status, statusTone = "text-secondary", value, detail, bar, barTone = "bg-secondary" }: {
  label: string; status?: string; statusTone?: string; value: ReactNode; detail?: ReactNode; bar?: number; barTone?: string;
}) {
  return (
    <div className="card flex flex-col gap-space-sm p-space-md">
      <div className="flex items-center justify-between gap-space-xs">
        <span className="label-caps">{label}</span>
        {status && (
          <span className={`flex items-center gap-1 font-mono text-mono-sm ${statusTone}`}>
            <span className="h-1.5 w-1.5 rounded-full bg-current" />{status}
          </span>
        )}
      </div>
      <div className="flex min-w-0 items-baseline justify-between gap-space-sm">
        <span className="truncate font-mono text-mono-lg text-on-surface">{value}</span>
        {detail && <span className="truncate text-right text-caption text-outline">{detail}</span>}
      </div>
      {bar !== undefined && (
        <div className="h-1 overflow-hidden rounded-full bg-surface-container-lowest">
          <div className={`h-full rounded-full ${barTone}`} style={{ width: `${Math.max(0, Math.min(100, bar * 100))}%` }} />
        </div>
      )}
    </div>
  );
}

export interface TabItem<T extends string> {
  value: T;
  label: string;
  icon: string;
  count?: number;
}

/** Tabs as the design's inset segmented control. */
export function Tabs<T extends string>({ tabs, value, onChange, label }: {
  tabs: TabItem<T>[]; value: T; onChange: (value: T) => void; label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex gap-0.5 rounded-lg bg-surface-container-lowest p-0.5">
      {tabs.map((tab) => {
        const on = tab.value === value;
        return (
          <button key={tab.value} role="tab" aria-selected={on} onClick={() => onChange(tab.value)}
                  className={`flex items-center gap-1.5 rounded px-space-md py-1.5 text-body ${on
                    ? "border border-stroke bg-surface-container-high font-semibold text-primary shadow-sm"
                    : "text-on-surface-variant hover:text-on-surface"}`}>
            <Icon name={tab.icon} size={16} filled={on} />
            {tab.label}{tab.count !== undefined && ` (${tab.count})`}
          </button>
        );
      })}
    </div>
  );
}

const PILL_TONES = {
  ok: "bg-secondary-container/20 text-secondary",
  warn: "bg-tertiary-container/20 text-tertiary",
  bad: "bg-error-container/40 text-error",
  off: "bg-surface-container-highest text-on-surface-variant",
  info: "bg-primary-container/20 text-primary",
} as const;

/** A status in words, with an icon (never colour alone). */
export function Pill({ tone, icon, children }: { tone: keyof typeof PILL_TONES; icon?: string; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-caption font-semibold whitespace-nowrap ${PILL_TONES[tone]}`}>
      {icon ? <Icon name={icon} size={13} /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}{children}
    </span>
  );
}

/** The cheapest fare each day as a small line (Destinations' 30-day trend). */
export function TrendLine({ points, className = "h-8 w-28" }: { points: { day: string; price: number }[]; className?: string }) {
  if (points.length < 2) {
    return <span className="font-mono text-mono-sm text-outline">{points.length ? "1 day so far" : "–"}</span>;
  }
  const W = 120, H = 32;
  const prices = points.map((p) => p.price);
  const hi = Math.max(...prices) * 1.05, lo = Math.min(...prices) * 0.6;
  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (price: number) => H - 2 - ((price - lo) / (hi - lo || 1)) * (H - 4);
  const falling = prices[prices.length - 1] < prices[0];
  const color = falling ? "var(--c-secondary)" : "var(--c-outline)";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={`overflow-visible ${className}`} role="img"
         aria-label={`From ${euros(prices[0])} to ${euros(prices[prices.length - 1])} over ${points.length} days`}>
      <path d={points.map((p, i) => `${i ? "L" : "M"} ${x(i).toFixed(1)},${y(p.price).toFixed(1)}`).join(" ")}
            fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <circle cx={x(points.length - 1)} cy={y(prices[prices.length - 1])} r="2.5" fill={color} />
    </svg>
  );
}
