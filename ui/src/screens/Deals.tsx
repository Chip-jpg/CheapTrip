/**
 * Deals (the home screen), as designed: summary tiles, the learning banner, the
 * filter bar, the unusually cheap deals as large cards (then compact ones), and
 * the best of the rest as a table. A deal's details slide over it (DealDetail).
 */
import { useState, type ReactNode } from "react";
import { api, type Deal, type DealFilters, type DealType, type TripLength } from "../api";
import { CompactDealCard, DealCard, DeltaPill, cityOf } from "../components/DealCard";
import { Icon } from "../components/Icon";
import { Popover } from "../components/Popover";
import { useEngine } from "../engine";
import { useReloadOn } from "../events";
import { clock, dates, euros, pct, shortDay } from "../format";
import { useLoad } from "../load";
import { href, navigate } from "../router";
import { sourceName } from "../sources";

const TYPE_CHIPS: { label: string; types: (DealType | "error_fare")[]; icon?: string; tone?: string }[] = [
  { label: "Flights", types: ["flight", "one_way"] },
  { label: "Packages", types: ["package"] },
  { label: "Hotels", types: ["hotel"] },
  { label: "Flight + hotel", types: ["flight_hotel"] },
  { label: "Via a hub", types: ["via_hub"], icon: "alt_route" },
  { label: "Possible error fares", types: ["error_fare"], tone: "text-error" },
];
const LENGTHS: { value: TripLength; label: string }[] = [
  { value: "weekend", label: "Weekend (2–4n)" },
  { value: "short", label: "Short (4–7n)" },
  { value: "medium", label: "Medium (7–14n)" },
  { value: "long", label: "Long (14–30n)" },
];
const SORTS: { value: NonNullable<DealFilters["sort"]>; label: string }[] = [
  { value: "discount", label: "% below usual" },
  { value: "price", label: "Price: low to high" },
  { value: "date", label: "Departure date" },
];
const PRICE_STEPS = { min: 20, max: 600, step: 10 };
const HERO_CARDS = 6;  // with their fare range, which the engine sends for these only (COMPARED_CARDS)
const TABLE_ROWS = 25;

function toggled<T>(list: T[] | undefined, items: T[]): T[] {
  const current = list ?? [];
  const on = items.every((item) => current.includes(item));
  return on ? current.filter((x) => !items.includes(x)) : [...current, ...items.filter((x) => !current.includes(x))];
}

function hasFilters(f: DealFilters): boolean {
  return !!(f.types?.length || f.lengths?.length || f.max_price !== undefined || f.from || f.to || f.q || f.priority_only);
}

function Tile({ label, icon, iconTone, children, onClick, live }: {
  label: string; icon: string; iconTone: string; children: ReactNode; onClick?: () => void; live?: boolean;
}) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag onClick={onClick}
         className={`card relative flex items-center justify-between gap-space-md overflow-hidden p-space-lg text-left ${onClick ? "transition-colors hover:bg-surface-container-highest" : ""}`}>
      <div className="z-10 flex min-w-0 flex-col gap-0.5">
        <div className="flex items-center gap-space-xs">
          {live && (
            <span className="relative flex h-2 w-2">
              <span className="animate-ping-dot absolute inline-flex h-full w-full rounded-full bg-secondary opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-secondary" />
            </span>
          )}
          <span className="label-caps">{label}</span>
        </div>
        {children}
      </div>
      <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-container ${iconTone}`}>
        <Icon name={icon} size={22} />
      </div>
    </Tag>
  );
}

function SummaryTiles({ best, cheap, lastSearch, onLearning }: {
  best: Deal | null; cheap: number; lastSearch: { at: string; fares: number } | null; onLearning: () => void;
}) {
  const { status } = useEngine();
  const learning = status?.learning;
  return (
    <section className="grid grid-cols-1 gap-gutter @2xl:grid-cols-2 @6xl:grid-cols-4" aria-label="Summary">
      <Tile label="Unusually cheap" icon="radar" iconTone="text-secondary" live={cheap > 0}>
        <div className="mt-1 flex items-baseline gap-space-xs" data-testid="summary">
          <span className="font-mono text-mono-lg text-on-surface">{cheap}</span>
          <span className="text-body font-semibold text-secondary-fixed">unusually cheap today</span>
        </div>
        <span className="text-caption text-outline">{cheap ? "Found in the last 24 hours" : "None in the last 24 hours"}</span>
      </Tile>
      <Tile label="Best today" icon="trending_down" iconTone="text-primary"
            onClick={best ? () => navigate({ screen: "deals", dealId: best.id }) : undefined}>
        {best ? (
          <>
            <div className="mt-1 flex items-baseline gap-space-xs">
              <span className="truncate text-headline-sm text-on-surface">{cityOf(best)}</span>
              <span className="font-mono text-mono-lg text-primary">{euros(best.price)}</span>
            </div>
            <div className="flex flex-wrap items-center gap-x-space-sm gap-y-1">
              <DeltaPill deal={best} suffix=" below usual" />
              <span className="truncate text-caption text-outline">
                {best.origin && best.destination ? `${best.origin.code} → ${best.destination.code}` : best.route}
                {best.nights ? ` (${best.nights}n)` : ""}
              </span>
            </div>
          </>
        ) : <span className="mt-1 text-body text-on-surface-variant">No deals yet</span>}
      </Tile>
      <Tile label="Last search" icon="history_toggle_off" iconTone="text-on-surface-variant">
        <div className="mt-1 flex items-baseline gap-space-xs">
          <span className="font-mono text-mono-lg text-on-surface">{lastSearch ? lastSearch.fares : "–"}</span>
          <span className="text-body text-on-surface-variant">fares checked</span>
        </div>
        <span className="font-mono text-mono-sm text-outline">
          {lastSearch ? `At ${clock(lastSearch.at)}` : "No search yet"}
          {status?.next_search_at ? ` · Next ${clock(status.next_search_at)}` : ""}
        </span>
      </Tile>
      <Tile label="Learning prices" icon="model_training" iconTone="text-tertiary" onClick={onLearning}>
        <div className="mt-1 flex items-baseline gap-space-xs">
          <span className="font-mono text-mono-lg whitespace-nowrap text-tertiary">
            {learning?.trips ? `${learning.with_usual_price ?? 0} / ${learning.trips}` : "–"}
          </span>
        </div>
        <span className="text-caption text-outline">trips have a usual price · <span className="text-tertiary-fixed-dim">what does this mean?</span></span>
      </Tile>
    </section>
  );
}

function LearningBanner({ onClose }: { onClose: () => void }) {
  const { status } = useEngine();
  const learning = status?.learning;
  const known = learning?.with_usual_price ?? 0;
  return (
    <div className="pane flex items-center justify-between gap-space-md p-space-md" role="note">
      <div className="flex min-w-0 items-center gap-space-md">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-container-highest text-tertiary">
          <Icon name="insights" size={20} />
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-body text-on-surface-variant">
            <span className="font-semibold text-on-surface">Learning prices: </span>
            {learning?.trips
              ? `${known} of the ${learning.trips} trips in the last search have enough history to judge.`
              : "the first search hasn't finished yet."}
          </span>
          <span className="font-mono text-mono-sm text-outline">
            Unusually-cheap alerts start after about a day. Priority destinations and fares under your price floors alert right away.
          </span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-space-xs">
        <a className="btn-secondary font-mono text-mono-sm" href={href({ screen: "activity" })}>Search history</a>
        <button className="btn-icon h-7 w-7" onClick={onClose} aria-label="Hide this message"><Icon name="close" size={16} /></button>
      </div>
    </div>
  );
}

function DateWindow({ filters, set }: { filters: DealFilters; set: (change: Partial<DealFilters>) => void }) {
  const label = filters.from || filters.to
    ? `${filters.from ? shortDay(filters.from) : "Now"} – ${filters.to ? shortDay(filters.to) : "any"}` : "Any dates";
  return (
    <Popover align="right" className="w-72" button={(open, toggle) => (
      <button onClick={toggle} aria-expanded={open} aria-label="Departure dates"
              className="field flex items-center justify-between gap-space-xs font-mono text-mono-sm">
        <span className="text-outline">Departs:</span>
        <span className="truncate text-on-surface">{label}</span>
        <Icon name="calendar_month" size={16} className="text-outline" />
      </button>
    )}>
      {(close) => (
        <div className="flex flex-col gap-space-sm p-space-sm">
          <span className="label-caps">Departure between</span>
          <label className="flex items-center justify-between gap-space-sm text-body">From
            <input type="date" className="field w-40" value={filters.from ?? ""}
                   onChange={(e) => set({ from: e.target.value || undefined })} />
          </label>
          <label className="flex items-center justify-between gap-space-sm text-body">To
            <input type="date" className="field w-40" value={filters.to ?? ""} min={filters.from}
                   onChange={(e) => set({ to: e.target.value || undefined })} />
          </label>
          <div className="flex justify-end gap-space-xs">
            <button className="btn-ghost" onClick={() => { set({ from: undefined, to: undefined }); close(); }}>Any dates</button>
            <button className="btn-primary" onClick={close}>Done</button>
          </div>
        </div>
      )}
    </Popover>
  );
}

function FilterBar({ filters, set }: { filters: DealFilters; set: (change: Partial<DealFilters>) => void }) {
  const [price, setPrice] = useState(filters.max_price ?? PRICE_STEPS.max);
  const [shown, setShown] = useState(filters.max_price);
  if (filters.max_price !== shown) {  // set elsewhere ("Clear filters"): the slider follows
    setShown(filters.max_price);
    setPrice(filters.max_price ?? PRICE_STEPS.max);
  }
  const commitPrice = (value: number) => set({ max_price: value >= PRICE_STEPS.max ? undefined : value });
  return (
    <section className="pane flex flex-col gap-space-md p-space-lg" aria-label="Filters">
      <div className="flex flex-wrap items-center justify-between gap-space-md">
        <div className="flex flex-wrap items-center gap-space-xs" role="group" aria-label="Deal types">
          {TYPE_CHIPS.map((chip) => {
            const on = chip.types.every((t) => filters.types?.includes(t));
            return (
              <button key={chip.label} aria-pressed={on} onClick={() => set({ types: toggled(filters.types, chip.types) })}
                      className={`chip ${on ? "chip-on" : chip.tone ?? ""}`}>
                {on ? <span className="h-1.5 w-1.5 rounded-full bg-on-primary" />
                  : chip.icon ? <Icon name={chip.icon} size={14} className="text-tertiary" />
                    : chip.tone ? <span className="h-1.5 w-1.5 rounded-full bg-error" /> : null}
                {chip.label}
              </button>
            );
          })}
        </div>
        <div className="inline-flex rounded-lg bg-surface-container-lowest p-0.5 shadow-inner" role="group" aria-label="Trip length">
          {LENGTHS.map((length) => {
            const on = filters.lengths?.includes(length.value) ?? false;
            return (
              <button key={length.value} aria-pressed={on} onClick={() => set({ lengths: toggled(filters.lengths, [length.value]) })}
                      className={`rounded-md px-3 py-1.5 font-mono text-mono-sm ${on
                        ? "bg-surface-container-high font-semibold text-primary shadow-sm" : "text-outline hover:text-on-surface"}`}>
                {length.label}
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-space-md">
        <div className="relative flex min-w-[15rem] flex-[2_1_15rem] items-center">
          <Icon name="search" size={18} className="pointer-events-none absolute left-space-md text-outline" />
          <input className="field pl-10" placeholder="Filter destinations, IATA…" aria-label="Destination"
                 value={filters.q ?? ""} onChange={(e) => set({ q: e.target.value || undefined })} />
        </div>
        <div className="flex min-w-[13rem] flex-[2_1_13rem] flex-col gap-1 px-space-xs">
          <div className="flex items-center justify-between text-caption text-on-surface-variant">
            <label htmlFor="max-price">Max price</label>
            <span className="font-mono text-mono-sm font-semibold text-primary">{price >= PRICE_STEPS.max ? "Any" : euros(price)}</span>
          </div>
          <input id="max-price" type="range" min={PRICE_STEPS.min} max={PRICE_STEPS.max} step={PRICE_STEPS.step}
                 value={price} onChange={(e) => setPrice(Number(e.target.value))}
                 onMouseUp={() => commitPrice(price)} onKeyUp={() => commitPrice(price)} onTouchEnd={() => commitPrice(price)}
                 className="h-1.5 w-full cursor-pointer accent-[var(--c-primary-container)]" />
        </div>
        <div className="min-w-[14rem] flex-[1_1_14rem]"><DateWindow filters={filters} set={set} /></div>
        <label className="flex flex-none items-center gap-space-sm text-body text-on-surface-variant">
          <button role="switch" aria-checked={filters.priority_only ?? false} aria-label="Priority only" className="switch"
                  onClick={() => set({ priority_only: !filters.priority_only })} />
          Priority only
        </label>
        <div className="relative ml-auto min-w-[12rem] max-w-[20rem] flex-[1_1_12rem]">
          <select aria-label="Sort" value={filters.sort} className="field cursor-pointer appearance-none pr-9"
                  onChange={(e) => set({ sort: e.target.value as DealFilters["sort"] })}>
            {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <Icon name="unfold_more" size={16} className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-outline" />
        </div>
      </div>
    </section>
  );
}

function SectionTitle({ children, count, right, dot }: { children: ReactNode; count?: string; right?: ReactNode; dot?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-space-md">
      <div className="flex items-center gap-space-sm">
        {dot && <span className="h-2.5 w-2.5 rounded-full bg-secondary" />}
        <h2 className="text-headline-sm text-on-surface">{children}</h2>
        {count && <span className="rounded-full bg-surface-container px-2 py-0.5 font-mono text-mono-sm font-semibold text-on-surface-variant">{count}</span>}
      </div>
      {right}
    </div>
  );
}

function Empty({ icon, title, children }: { icon: string; title: string; children: ReactNode }) {
  return (
    <div className="pane flex flex-col items-center gap-space-sm p-space-xl text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-surface-container-highest text-primary">
        <Icon name={icon} size={24} />
      </div>
      <h3 className="text-body-lg font-semibold text-on-surface">{title}</h3>
      <div className="max-w-xl text-body text-on-surface-variant">{children}</div>
    </div>
  );
}

function RestTable({ deals, sort }: { deals: Deal[]; sort: DealFilters["sort"] }) {
  const [all, setAll] = useState(false);
  const shown = all ? deals : deals.slice(0, TABLE_ROWS);
  return (
    <section className="pane flex flex-col gap-space-md p-space-md" aria-label="Best of the rest">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-space-sm">
          <Icon name="dataset" size={20} className="text-outline" />
          <h2 className="text-headline-sm text-on-surface">Best of the rest</h2>
          <span className="font-mono text-mono-sm text-outline">
            Cheapest per route today · sorted by {SORTS.find((s) => s.value === sort)?.label.toLowerCase()}
          </span>
        </div>
        <span className="font-mono text-mono-sm text-outline">{deals.length} routes</span>
      </div>
      <div className="overflow-x-auto">
        <div className="overflow-x-auto"><table className="w-full min-w-[42rem] text-left text-body">
          <thead>
            <tr className="bg-surface-container-lowest/50 text-caption tracking-wider text-outline uppercase">
              <th className="rounded-l px-space-md py-2.5 font-normal">Destination</th>
              <th className="px-space-md py-2.5 font-normal">Routing</th>
              <th className="px-space-md py-2.5 font-normal">Dates</th>
              <th className="px-space-md py-2.5 font-normal">Price</th>
              <th className="px-space-md py-2.5 font-normal">Usual</th>
              <th className="px-space-md py-2.5 font-normal">Delta</th>
              <th className="rounded-r px-space-md py-2.5 text-right font-normal">Book</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((deal) => (
              <tr key={deal.id} data-testid="deal-row" onClick={() => navigate({ screen: "deals", dealId: deal.id })}
                  className="cursor-pointer transition-colors hover:bg-surface-container-high">
                <td className="px-space-md py-2.5 font-semibold text-on-surface">
                  {deal.type === "hotel" && deal.hotel ? deal.hotel.name : `${cityOf(deal)}${deal.destination ? ` (${deal.destination.code})` : ""}`}
                  {deal.is_priority && <Icon name="star" size={13} filled className="ml-1 text-tertiary" />}
                </td>
                <td className="px-space-md py-2.5 font-mono text-mono-sm text-outline">
                  {deal.origin && deal.destination ? `${deal.origin.code} → ${deal.destination.code} · ` : ""}
                  {deal.flight?.airline ?? deal.sources.map(sourceName).join(", ")}
                </td>
                <td className="px-space-md py-2.5 font-mono text-mono-sm text-on-surface-variant">
                  {dates(deal)}{deal.nights ? ` (${deal.nights}n)` : ""}
                </td>
                <td className={`px-space-md py-2.5 font-mono text-mono font-bold ${deal.discount_pct ? "text-secondary" : "text-on-surface"}`}>{euros(deal.price)}</td>
                <td className="px-space-md py-2.5 font-mono text-mono-sm text-outline">{deal.usual_price !== null ? euros(deal.usual_price) : "learning"}</td>
                <td className="px-space-md py-2.5">
                  {deal.discount_pct ? <span className="delta bg-secondary-container text-on-secondary-container">-{pct(deal.discount_pct)}</span>
                    : <span className="font-mono text-mono-sm text-outline">–</span>}
                </td>
                <td className="px-space-md py-2.5 text-right">
                  {deal.links.book && (
                    <a className="btn h-7 border border-stroke bg-surface-container-highest px-space-md text-on-surface hover:bg-primary-container hover:text-on-primary"
                       href={deal.links.book} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>Book</a>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>
      {deals.length > TABLE_ROWS && (
        <button className="btn-ghost self-center" onClick={() => setAll((a) => !a)}>
          {all ? "Show fewer" : `Show all ${deals.length} routes`}<Icon name={all ? "expand_less" : "expand_more"} size={16} />
        </button>
      )}
    </section>
  );
}

function Skeleton() {
  return (
    <div className="flex flex-col gap-space-lg" aria-busy="true" aria-label="Loading deals">
      <div className="grid grid-cols-1 gap-gutter @2xl:grid-cols-2 @6xl:grid-cols-4">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-[88px]" />)}</div>
      <div className="skeleton h-[104px]" />
      <div className="grid grid-cols-1 gap-gutter @3xl:grid-cols-2 @6xl:grid-cols-3">{[0, 1, 2].map((i) => <div key={i} className="skeleton h-80" />)}</div>
    </div>
  );
}

export function Deals() {
  const [filters, setFilters] = useState<DealFilters>({ sort: "discount" });
  const [banner, setBanner] = useState<boolean | null>(null);
  const deals = useLoad(() => api.deals(filters), [JSON.stringify(filters)]);
  const settings = useLoad(api.settings, []);
  const { status } = useEngine();
  useReloadOn(["cycle_finished", "alert_sent", "deal_hidden", "preferences_changed"], deals.reload);
  const set = (change: Partial<DealFilters>) => setFilters((f) => ({ ...f, ...change }));
  const d = deals.data;

  if (!d) {
    return deals.error ? (
      <Empty icon="cloud_off" title="Can't load the deals">
        {deals.error}<div className="mt-space-md"><button className="btn-primary" onClick={deals.reload}>Try again</button></div>
      </Empty>
    ) : <Skeleton />;
  }

  const learning = status?.learning;
  const stillLearning = !learning?.trips || (learning.with_usual_price ?? 0) / learning.trips < 0.5;
  const showBanner = banner ?? stillLearning;
  const filtered = hasFilters(filters);
  const home = settings.data?.preferences?.home_airports;

  return (
    <div className="flex flex-col gap-space-lg">
      <SummaryTiles best={d.summary.best} cheap={d.summary.unusually_cheap} lastSearch={d.summary.last_search}
                    onLearning={() => setBanner(!showBanner)} />
      {showBanner && <LearningBanner onClose={() => setBanner(false)} />}
      <FilterBar filters={filters} set={set} />

      <section className="flex flex-col gap-space-md" aria-label="Unusually cheap today">
        <SectionTitle dot count={`${d.instant.length} ${d.instant.length === 1 ? "deal" : "deals"}`} right={
          <span className="flex items-center gap-space-xs text-caption text-outline">
            <Icon name="verified" size={16} className="text-secondary" />
            Compared with similar fares departing within 30 days either side
          </span>
        }>Unusually cheap today</SectionTitle>
        {d.instant.length === 0 ? (
          filtered ? (
            <Empty icon="filter_alt_off" title="No unusually cheap deals match these filters">
              <button className="btn-secondary mt-space-sm" onClick={() => setFilters({ sort: filters.sort })}>Clear filters</button>
            </Empty>
          ) : stillLearning ? (
            <Empty icon="model_training" title="Nothing unusually cheap yet">
              CheapTrip is still learning what each route usually costs, so it can tell a real bargain from a normal price.
              That takes about a day of searches. Meanwhile priority destinations and fares under your price floors alert
              right away, and today's best fares are below.
            </Empty>
          ) : (
            <Empty icon="radar" title="Nothing unusually cheap right now">
              No fare is well below its usual price at the moment. CheapTrip keeps searching and will tell you as soon as
              one is; today's best fares are below.
            </Empty>
          )
        ) : (
          <>
            <div className="grid grid-cols-1 gap-gutter @3xl:grid-cols-2 @6xl:grid-cols-3">
              {d.instant.slice(0, HERO_CARDS).map((deal) => <DealCard key={deal.id} deal={deal} onChange={deals.reload} />)}
            </div>
            {d.instant.length > HERO_CARDS && (
              <div className="grid grid-cols-1 gap-gutter @2xl:grid-cols-2 @6xl:grid-cols-4">
                {d.instant.slice(HERO_CARDS).map((deal) => <CompactDealCard key={deal.id} deal={deal} />)}
              </div>
            )}
          </>
        )}
      </section>

      {d.digest.length > 0 ? <RestTable deals={d.digest} sort={filters.sort} /> : (
        <Empty icon="travel_explore" title={filtered ? "No other deals match these filters" : "No other deals in the last day"}>
          {filtered ? <button className="btn-secondary mt-space-sm" onClick={() => setFilters({ sort: filters.sort })}>Clear filters</button>
            : "They appear here after the next search."}
        </Empty>
      )}

      <footer className="flex flex-wrap items-center justify-between gap-space-md rounded-xl bg-surface-container-low p-space-md text-caption text-outline">
        <span className="flex items-center gap-space-md">
          <span className="flex items-center gap-space-xs"><Icon name="euro" size={14} />Prices per person, in euros</span>
          {home && <><span aria-hidden="true">•</span><span>Searching from {home.join(" / ")}</span></>}
        </span>
        {d.summary.last_search && <span className="font-mono text-mono-sm">{d.summary.last_search.fares} fares checked at {clock(d.summary.last_search.at)}</span>}
      </footer>
    </div>
  );
}
