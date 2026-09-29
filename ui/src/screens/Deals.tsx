import { useState } from "react";
import { api, type DealFilters, type DealType, type TripLength } from "../api";
import { DealCard } from "../components/DealCard";
import { useReloadOn } from "../events";
import { euros, TYPE_LABELS, when } from "../format";
import { useLoad } from "../load";

const TYPES = Object.keys(TYPE_LABELS) as DealType[];
const LENGTHS: TripLength[] = ["weekend", "short", "medium", "long"];

function toggle<T>(list: T[] | undefined, item: T): T[] {
  const current = list ?? [];
  return current.includes(item) ? current.filter((x) => x !== item) : [...current, item];
}

/** Deals: the unusually cheap ones first, then the cheapest fare per route found in the last day. */
export function Deals() {
  const [filters, setFilters] = useState<DealFilters>({ sort: "price" });
  const deals = useLoad(() => api.deals(filters), [JSON.stringify(filters)]);
  useReloadOn(["cycle_finished", "alert_sent", "deal_hidden", "preferences_changed"], deals.reload);
  const set = (change: Partial<DealFilters>) => setFilters((f) => ({ ...f, ...change }));
  const d = deals.data;

  return (
    <section>
      <h1>Deals</h1>
      {d && (
        <p className="muted" data-testid="summary">
          {d.summary.unusually_cheap} unusually cheap
          {d.summary.best && <> · cheapest {d.summary.best.route} {euros(d.summary.best.price)}</>}
          {d.summary.last_search && <> · last search {when(d.summary.last_search.at)}, {d.summary.last_search.fares} fares</>}
        </p>
      )}

      <div className="filters">
        {TYPES.map((t) => (
          <label key={t} className="chip">
            <input type="checkbox" checked={filters.types?.includes(t) ?? false}
                   onChange={() => set({ types: toggle(filters.types, t) })} />
            {TYPE_LABELS[t]}
          </label>
        ))}
        <label className="chip">
          <input type="checkbox" checked={filters.types?.includes("error_fare") ?? false}
                 onChange={() => set({ types: toggle(filters.types, "error_fare") })} />
          Error fares
        </label>
        {LENGTHS.map((l) => (
          <label key={l} className="chip">
            <input type="checkbox" checked={filters.lengths?.includes(l) ?? false}
                   onChange={() => set({ lengths: toggle(filters.lengths, l) })} />
            {l}
          </label>
        ))}
        <input type="search" placeholder="Destination" aria-label="Destination" value={filters.q ?? ""}
               onChange={(e) => set({ q: e.target.value || undefined })} />
        <input type="number" placeholder="Max €" aria-label="Max price" min={0} value={filters.max_price ?? ""}
               onChange={(e) => set({ max_price: e.target.value ? Number(e.target.value) : undefined })} />
        <label className="chip">
          <input type="checkbox" checked={filters.priority_only ?? false}
                 onChange={(e) => set({ priority_only: e.target.checked })} />
          Priority only
        </label>
        <select aria-label="Sort" value={filters.sort} onChange={(e) => set({ sort: e.target.value as DealFilters["sort"] })}>
          <option value="price">Cheapest first</option>
          <option value="discount">Biggest discount</option>
          <option value="date">Soonest</option>
        </select>
      </div>

      {deals.error && <p className="error">{deals.error}</p>}
      {d && (
        <>
          <h2>Unusually cheap</h2>
          {d.instant.length === 0 ? <p className="muted">Nothing unusually cheap right now.</p>
            : <div className="cards">{d.instant.map((deal) => <DealCard key={deal.id} deal={deal} />)}</div>}
          <h2>Best of the rest</h2>
          {d.digest.length === 0 ? <p className="muted">No other deals in the last day.</p>
            : <div className="cards">{d.digest.map((deal) => <DealCard key={deal.id} deal={deal} />)}</div>}
        </>
      )}
    </section>
  );
}
