/** Settings controls in the design's style: sections, labelled rows, switches, steppers, airport chips, key fields. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Airport, type Check, type SecretKey } from "../api";
import { Icon } from "./Icon";

export function Section({ title, icon, description, children, right }: {
  title: string; icon: string; description?: ReactNode; children: ReactNode; right?: ReactNode;
}) {
  return (
    <section className="card flex flex-col gap-space-md p-space-lg" aria-label={title}>
      <div className="flex items-start justify-between gap-space-md">
        <div className="flex items-start gap-space-md">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-container-highest text-primary">
            <Icon name={icon} size={20} />
          </div>
          <div className="flex flex-col gap-0.5">
            <h2 className="text-headline-sm text-on-surface">{title}</h2>
            {description && <p className="text-body text-on-surface-variant">{description}</p>}
          </div>
        </div>
        {right}
      </div>
      <div className="flex flex-col divide-y divide-stroke">{children}</div>
    </section>
  );
}

/** One setting: its name and a line of help on the left, the control on the right. */
export function Row({ label, help, children, htmlFor }: { label: string; help?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="flex items-center justify-between gap-space-xl py-space-md first:pt-0 last:pb-0">
      <div className="flex min-w-0 flex-col gap-0.5">
        <label htmlFor={htmlFor} className="text-body font-semibold text-on-surface">{label}</label>
        {help && <span className="text-caption text-on-surface-variant">{help}</span>}
      </div>
      <div className="flex shrink-0 items-center gap-space-sm">{children}</div>
    </div>
  );
}

export function Switch({ on, onChange, label, disabled }: { on: boolean; onChange: (on: boolean) => void; label: string; disabled?: boolean }) {
  return <button role="switch" aria-checked={on} aria-label={label} className="switch" disabled={disabled} onClick={() => onChange(!on)} />;
}

export function Stepper({ value, min, max, onChange, label, unit = "" }: {
  value: number; min: number; max: number; onChange: (value: number) => void; label: string; unit?: string;
}) {
  return (
    <div className="flex items-center gap-1" role="group" aria-label={label}>
      <button className="btn-secondary h-8 w-8 px-0" aria-label={`Fewer ${label}`} disabled={value <= min}
              onClick={() => onChange(Math.max(min, value - 1))}><Icon name="remove" size={16} /></button>
      <span className="w-14 text-center font-mono text-mono font-semibold text-on-surface" aria-live="polite">{value}{unit}</span>
      <button className="btn-secondary h-8 w-8 px-0" aria-label={`More ${label}`} disabled={value >= max}
              onClick={() => onChange(Math.min(max, value + 1))}><Icon name="add" size={16} /></button>
    </div>
  );
}

/** Choose one of a few options (the design's segmented control). */
export function Segmented<T extends string>({ options, value, onChange, label }: {
  options: { value: T; label: string; icon?: string }[]; value: T; onChange: (value: T) => void; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex gap-0.5 rounded-lg bg-surface-container-lowest p-0.5">
      {options.map((o) => (
        <button key={o.value} role="radio" aria-checked={o.value === value} onClick={() => onChange(o.value)}
                className={`flex items-center gap-1 rounded px-space-md py-1 text-body ${o.value === value
                  ? "border border-stroke bg-surface-container-high font-semibold text-primary shadow-sm" : "text-on-surface-variant hover:text-on-surface"}`}>
          {o.icon && <Icon name={o.icon} size={16} />}{o.label}
        </button>
      ))}
    </div>
  );
}

/** A number (or time) that saves when you leave the field or press Enter. */
export function CommitInput({ value, onCommit, label, type = "number", min, max, step, suffix, prefix, width = "w-24", placeholder, id }: {
  value: string | number | null; onCommit: (value: string) => void; label: string; type?: string; min?: number; max?: number;
  step?: number; suffix?: string; prefix?: string; width?: string; placeholder?: string; id?: string;
}) {
  const [draft, setDraft] = useState(value === null ? "" : String(value));
  useEffect(() => setDraft(value === null ? "" : String(value)), [value]);
  const commit = () => { if (draft !== (value === null ? "" : String(value))) onCommit(draft); };
  return (
    <span className="flex items-center gap-space-xs">
      {prefix && <span className="font-mono text-mono text-outline">{prefix}</span>}
      <input id={id} aria-label={label} type={type} min={min} max={max} step={step} placeholder={placeholder}
             className={`field font-mono text-mono ${width}`} value={draft}
             onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />
      {suffix && <span className="text-body text-on-surface-variant">{suffix}</span>}
    </span>
  );
}

/** Airports as chips, with autocomplete to add one ("Kraków (KRK)"). */
export function AirportChips({ codes, onChange, label, max = 12 }: {
  codes: string[]; onChange: (codes: string[]) => void; label: string; max?: number;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Airport[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    codes.filter((c) => !(c in names)).forEach((code) => {
      api.airports(code, 1).then((found) => {
        const hit = found.find((a) => a.code === code);
        if (hit) setNames((n) => ({ ...n, [code]: hit.city }));
      }).catch(() => {});
    });
  }, [codes.join(",")]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (query.trim().length < 2) { setResults([]); return; }
    const timer = window.setTimeout(() => api.airports(query.trim(), 6).then(setResults).catch(() => setResults([])), 150);
    return () => window.clearTimeout(timer);
  }, [query]);
  const add = (a: Airport) => {
    setNames((n) => ({ ...n, [a.code]: a.city }));
    if (!codes.includes(a.code)) onChange([...codes, a.code]);
    setQuery("");
    setResults([]);
    input.current?.focus();
  };
  return (
    <div className="flex w-[420px] flex-col gap-space-xs">
      <div className="flex flex-wrap items-center gap-space-xs rounded border border-stroke bg-surface-container-lowest p-1.5" role="group" aria-label={label}>
        {codes.map((code) => (
          <span key={code} className="flex items-center gap-1 rounded-full bg-primary-container/20 py-0.5 pr-1 pl-2 text-body text-on-surface">
            <span className="font-mono text-mono font-semibold text-primary">{code}</span>
            {names[code] && <span className="text-caption text-on-surface-variant">{names[code]}</span>}
            <button className="rounded-full p-0.5 text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface"
                    aria-label={`Remove ${code}`} onClick={() => onChange(codes.filter((c) => c !== code))}>
              <Icon name="close" size={14} />
            </button>
          </span>
        ))}
        {codes.length < max && (
          <input ref={input} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Add a city or airport…"
                 aria-label={`Add to ${label}`} role="combobox" aria-expanded={results.length > 0}
                 className="min-w-32 flex-1 bg-transparent px-1 text-body text-on-surface placeholder:text-outline focus:outline-none"
                 onKeyDown={(e) => e.key === "Enter" && results[0] && add(results[0])} />
        )}
      </div>
      {results.length > 0 && (
        <div role="listbox" className="flex flex-col rounded-lg border border-stroke bg-surface-container p-space-xs">
          {results.map((a) => (
            <button key={a.code} role="option" aria-selected={false} onClick={() => add(a)}
                    className="flex items-center gap-space-md rounded px-space-md py-1 text-left hover:bg-surface-container-highest">
              <span className="w-9 font-mono text-mono font-semibold text-primary">{a.code}</span>
              <span className="flex-1 text-body text-on-surface">{a.city}</span>
              <span className="truncate text-caption text-outline">{a.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * A key or token: never shown back in full ("set (…1234)"). Typing a new one
 * and pressing Save replaces it; Remove clears it; the eye shows what you type.
 */
export function SecretField({ secret, label, onSave, placeholder = "Paste it here" }: {
  secret: SecretKey; label: string; onSave: (value: string) => Promise<unknown>; placeholder?: string;
}) {
  const [draft, setDraft] = useState("");
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const save = (value: string) => { setBusy(true); onSave(value).then(() => setDraft("")).finally(() => setBusy(false)); };
  return (
    <div className="flex items-center gap-space-xs">
      <span className="relative">
        <input type={visible ? "text" : "password"} autoComplete="off" spellCheck={false} aria-label={label}
               placeholder={secret.set ? `Saved (${secret.hint}): paste a new one to replace it` : placeholder}
               className="field w-[340px] pr-9 font-mono text-mono" value={draft} onChange={(e) => setDraft(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && draft.trim() && save(draft.trim())} />
        <button className="absolute top-1 right-1 flex h-6 w-6 items-center justify-center text-outline hover:text-on-surface"
                aria-label={visible ? `Hide ${label}` : `Show ${label}`} onClick={() => setVisible((v) => !v)}>
          <Icon name={visible ? "visibility_off" : "visibility"} size={16} />
        </button>
      </span>
      <button className="btn-primary" disabled={busy || !draft.trim()} onClick={() => save(draft.trim())}>Save</button>
      {secret.set && <button className="btn-ghost" disabled={busy} onClick={() => save("")}>Remove</button>}
    </div>
  );
}

const MARKS = { ok: ["check_circle", "text-secondary"], warn: ["warning", "text-tertiary"], fail: ["cancel", "text-error"] } as const;

/** The result of a Check button or the setup checks: ✓ / ⚠ / ✗ with the reason. */
export function CheckList({ checks }: { checks: Check[] }) {
  return (
    <ul className="flex flex-col gap-1">
      {checks.map((c, i) => {
        const [icon, tone] = MARKS[c.level];
        return (
          <li key={`${c.name}-${i}`} className="flex items-start gap-space-sm rounded bg-surface-container-lowest px-space-sm py-1.5 text-body">
            <Icon name={icon} size={18} className={tone} />
            <span className="min-w-0"><span className="font-semibold text-on-surface">{c.name}:</span>{" "}
              <span className="break-words text-on-surface-variant">{c.detail}</span></span>
          </li>
        );
      })}
    </ul>
  );
}
