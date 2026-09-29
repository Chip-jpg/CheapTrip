/**
 * A deal's details, sliding over the Deals screen (as designed): the price and
 * actions, booking confidence in plain words, why it's here, "How this fare
 * compares", "This fare over time", and the flight, legs or hotel.
 */
import { useEffect, type ReactNode } from "react";
import { api, type DealDetail as Detail } from "../api";
import { FareScatter, PriceSteps } from "../components/Charts";
import { cityOf, codes, DeltaPill, notifiedText, priceTone, title, TypeBadge, useDealActions } from "../components/DealCard";
import { Icon } from "../components/Icon";
import { useReloadOn } from "../events";
import { dayLabel, euros, nightsLabel, reasons, tripDates, when } from "../format";
import { useLoad } from "../load";
import { navigate } from "../router";
import { sourceName } from "../sources";

const CONFIDENCE_WORDS: Record<string, [string, string]> = {
  high: ["High", "The price comes straight from the airline or a live search, with a link that books this exact fare."],
  medium: ["Medium", "The price comes from a fare search engine, which can lag a little behind the airline: check it on the booking page."],
  low: ["Low", "The price comes from a deal site's post, so it may have changed since it was published: check the dates and the price before booking."],
};

function Section({ title: heading, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-space-xs">
      <div className="flex items-center justify-between">
        <h4 className="text-body font-semibold text-on-surface">{heading}</h4>
        {right && <span className="font-mono text-mono-sm text-outline">{right}</span>}
      </div>
      <div className="flex flex-col gap-space-sm rounded-xl bg-surface-container-low p-space-md">{children}</div>
    </section>
  );
}

function time(iso: string | null | undefined): string | null {
  return iso?.match(/(\d\d:\d\d)/)?.[1] ?? null;
}

function duration(minutes: number | null | undefined): string {
  if (!minutes) return "";
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

function FlightRow({ icon, label, route, detail, right }: { icon: string; label: string; route: string; detail: string; right?: string }) {
  return (
    <div className="flex items-start justify-between gap-space-md">
      <div className="flex items-start gap-space-md">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-surface-container-high text-primary">
          <Icon name={icon} size={18} />
        </div>
        <div className="flex flex-col">
          <span className="text-body font-semibold text-on-surface">{label}</span>
          <span className="font-mono text-mono text-on-surface-variant">{route}</span>
          {detail && <span className="text-caption text-outline">{detail}</span>}
        </div>
      </div>
      {right && <span className="font-mono text-mono-sm text-on-surface-variant">{right}</span>}
    </div>
  );
}

function Trip({ d }: { d: Detail }) {
  if (d.type === "via_hub" && d.legs.length) {
    return (
      <Section title="Two tickets via a hub" right={`${euros(d.price)} in total`}>
        {d.legs.map((leg, i) => (
          <div key={i} className="flex items-start justify-between gap-space-md">
            <FlightRow icon={i ? "flight_takeoff" : "connecting_airports"} label={`Leg ${i + 1} · ${leg.from?.city} → ${leg.to?.city}`}
                       route={`${leg.from?.code} → ${leg.to?.code} · ${dayLabel(leg.depart_date)}${leg.return_date ? ` → ${dayLabel(leg.return_date)}` : ""}`}
                       detail={sourceName(leg.source)} />
            <div className="flex shrink-0 flex-col items-end gap-1">
              <span className="font-mono text-mono font-semibold text-on-surface">{euros(leg.price)}</span>
              {leg.booking_url && <a className="btn-secondary h-7 px-space-sm" href={leg.booking_url} target="_blank" rel="noreferrer">Book leg {i + 1}</a>}
            </div>
          </div>
        ))}
        <span className="text-caption text-outline">
          Separate tickets: if the first flight is late, the second airline doesn't have to wait. Leave plenty of time at {d.legs[0].to?.city}.
        </span>
      </Section>
    );
  }
  const f = d.flight;
  if (!f || !d.origin || !d.destination) return null;
  const stops = f.stops === 0 ? "Direct" : f.stops ? `${f.stops} stop${f.stops > 1 ? "s" : ""}` : null;
  const dep = time(f.departure_time), arr = time(f.arrival_time);
  return (
    <Section title="Flights">
      <FlightRow icon="flight_takeoff" label={`Outbound · ${dayLabel(d.depart_date)}`}
                 route={dep ? `${dep} ${d.origin.code} → ${arr ?? "?"} ${d.destination.code}` : `${d.origin.code} → ${d.destination.code}`}
                 detail={[f.airline, stops].filter(Boolean).join(" · ")} right={duration(f.duration_minutes)} />
      {d.return_date && (
        <FlightRow icon="flight_land" label={`Return · ${dayLabel(d.return_date)}`}
                   route={`${d.destination.code} → ${d.origin.code}`} detail="Times on the booking page" />
      )}
    </Section>
  );
}

function Hotel({ d }: { d: Detail }) {
  const h = d.hotel;
  if (!h && !d.package_hotel) return null;
  return (
    <Section title={d.type === "package" ? "The package" : "The hotel"}
             right={h ? `${euros(h.price_per_night, 2)} a night` : undefined}>
      <div className="flex items-start gap-space-md">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-surface-container-high text-primary">
          <Icon name="apartment" size={18} />
        </div>
        <div className="flex flex-col">
          <span className="text-body font-semibold text-on-surface">{h?.name ?? d.package_hotel}</span>
          {h && <span className="text-caption text-outline">{h.location}</span>}
          {h && (
            <span className="font-mono text-mono-sm text-on-surface-variant">
              {[h.rating ? `${h.rating}/10` : null, h.review_count ? `${h.review_count} reviews` : null,
                h.stars ? `${h.stars}★` : null].filter(Boolean).join(" · ")}
            </span>
          )}
          <span className="text-caption text-outline">
            {h?.check_in ? `${dayLabel(h.check_in)} → ${dayLabel(h.check_out)} · ${nightsLabel(h.nights)} · ${euros(h.total)}`
              : h?.travel_window ?? (d.nights ? `Flight + ${nightsLabel(d.nights)}, per person` : "Flight + hotel, per person")}
          </span>
        </div>
      </div>
      {h?.booking_url && <a className="btn-secondary self-start" href={h.booking_url} target="_blank" rel="noreferrer">Open the hotel</a>}
    </Section>
  );
}

export function DealDetail({ id }: { id: string }) {
  const deal = useLoad(() => api.deal(id), [id]);
  useReloadOn(["preferences_changed", "alert_sent"], deal.reload);
  const actions = useDealActions(deal.reload);
  const close = () => navigate({ screen: "deals" });

  useEffect(() => {
    const escape = (event: KeyboardEvent) => event.key === "Escape" && close();
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, []);

  const d = deal.data;
  const why = d ? reasons(d.reasons) : [];
  const [confidence, confidenceWords] = d ? CONFIDENCE_WORDS[d.confidence] ?? CONFIDENCE_WORDS.low : ["", ""];
  const similar = d?.comparison ? d.comparison.points.filter((p) => !p.is_this).length : 0;

  return (
    <>
      <div className="fixed inset-0 z-50 bg-scrim backdrop-blur-sm" onClick={close} aria-hidden="true" />
      <aside role="dialog" aria-modal="true" aria-label={d ? `${d.route} details` : "Deal details"}
             className="fixed top-0 right-0 bottom-0 z-50 flex w-full max-w-[540px] flex-col overflow-y-auto border-l border-stroke bg-surface-container-high/95 shadow-[-12px_0_32px_rgba(0,0,0,0.45)] backdrop-blur-2xl">
        <div className="sticky top-0 z-20 flex flex-col gap-space-md bg-surface-container-highest/40 p-space-lg backdrop-blur-xl">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-space-xs">
              <span className="badge bg-primary-container text-on-primary">Deal details</span>
              {d && <TypeBadge deal={d} />}
              {d && <span className="rounded bg-secondary-container/20 px-2 py-0.5 font-mono text-mono-sm font-semibold text-secondary">
                {d.tier === "instant" ? "Unusually cheap" : "Best of the rest"}</span>}
            </div>
            <div className="flex items-center gap-space-xs">
              {d?.destination && (
                <button className={`btn-icon ${d.is_priority ? "text-tertiary" : ""}`} onClick={() => actions.prioritize(d)}
                        aria-pressed={d.is_priority} title={d.is_priority ? `Remove ${cityOf(d)} from priorities` : `Make ${cityOf(d)} a priority`}
                        aria-label={d.is_priority ? `Remove ${cityOf(d)} from priorities` : `Make ${cityOf(d)} a priority`}>
                  <Icon name="star" size={18} filled={d.is_priority} />
                </button>
              )}
              <button className="btn-icon" onClick={close} aria-label="Close"><Icon name="close" size={20} /></button>
            </div>
          </div>

          {!d ? (
            deal.error ? <p className="text-body text-error">{deal.error}</p> : <div className="skeleton h-24" />
          ) : (
            <>
              <div className="flex items-start justify-between gap-space-md">
                <div className="flex min-w-0 flex-col">
                  <h2 className="text-headline-lg tracking-tight text-on-surface">{title(d)}</h2>
                  <span className="text-body text-on-surface-variant">
                    {d.type === "hotel" ? d.hotel?.location : tripDates(d)}{d.return_date && d.type !== "hotel" ? " round trip" : ""}
                  </span>
                  <span className="font-mono text-mono-sm text-outline">{codes(d)}</span>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <div className="flex items-baseline gap-1.5">
                    {d.previous_price !== null && d.previous_price > d.price && (
                      <span className="font-mono text-mono-sm text-outline line-through">{euros(d.previous_price)}</span>
                    )}
                    <span className={`font-mono text-mono-lg font-bold ${priceTone(d)}`}>{euros(d.price)}</span>
                  </div>
                  <DeltaPill deal={d} suffix=" vs usual" />
                  {d.usual_price !== null && <span className="text-caption text-outline">Usually {euros(d.usual_price)}</span>}
                </div>
              </div>
              <div className="flex items-center gap-space-xs">
                {d.type === "via_hub" && <span className="flex-1 text-body text-on-surface-variant">Two tickets: book each leg below.</span>}
                {d.links.book && d.type !== "via_hub" && (
                  <a className={`${d.is_error_fare ? "btn-danger" : "btn-primary"} h-9 flex-1`} href={d.links.book} target="_blank" rel="noreferrer">
                    <Icon name="airplane_ticket" size={18} />
                    {d.type === "hotel" ? "Reserve" : "Book"} for {euros(d.price)}{d.sources[0] ? ` on ${sourceName(d.sources[0])}` : ""}
                  </a>
                )}
                {d.links.hotels && (
                  <a className="btn-secondary h-9" href={d.links.hotels} target="_blank" rel="noreferrer">
                    <Icon name="apartment" size={18} />Hotels
                  </a>
                )}
                {d.destination && (
                  <button className="btn-secondary h-9 w-9 px-0" onClick={() => actions.mute(d)}
                          aria-label={d.is_muted ? `Unmute ${cityOf(d)}` : `Mute ${cityOf(d)}`}
                          title={d.is_muted ? `Unmute ${cityOf(d)}` : `Mute ${cityOf(d)}: no more alerts for it`}>
                    <Icon name={d.is_muted ? "notifications" : "notifications_off"} size={18} />
                  </button>
                )}
                <button className="btn-secondary h-9 w-9 px-0" aria-label="Hide this deal" title="Hide this deal"
                        onClick={() => { actions.hide(d); close(); }}>
                  <Icon name="visibility_off" size={18} />
                </button>
              </div>
            </>
          )}
        </div>

        {d && (
          <div className="flex flex-1 flex-col gap-space-xl p-space-lg">
            {d.is_error_fare && (
              <div className="flex items-start gap-space-sm rounded-xl bg-error-container/25 p-space-md text-body text-on-surface">
                <Icon name="warning" size={20} className="text-error" />
                <span><strong>Possible error fare: book fast, verify after.</strong> Fares this far below normal are sometimes
                  mistakes, and airlines can cancel them. Don't book non-refundable hotels or trains until the ticket is issued.</span>
              </div>
            )}

            <div className="flex flex-col gap-1.5 rounded-xl bg-surface-container-low p-space-md">
              <div className="flex items-center gap-space-xs text-secondary">
                <Icon name="verified" size={20} />
                <span className="text-body font-semibold text-on-surface">Booking confidence: {confidence}</span>
              </div>
              <p className="text-body text-on-surface-variant">{confidenceWords}</p>
              <span className="font-mono text-mono-sm text-outline">
                Found by {d.sources.map(sourceName).join(", ")} · {when(d.found_at)} · {notifiedText(d)}
              </span>
            </div>

            {why.length > 0 && (
              <Section title="Why it's here">
                <ul className="flex flex-col gap-1">
                  {why.map((reason) => (
                    <li key={reason} className="flex items-start gap-space-xs text-body text-on-surface-variant">
                      <Icon name="check_circle" size={16} className="text-secondary" />{reason}
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            {d.comparison && d.depart_date && (
              <Section title="How this fare compares" right={`Departure ±${d.comparison.window_days} days`}>
                {d.comparison.points.length > 1 ? (
                  <>
                    <div className="flex items-center justify-end gap-space-md font-mono text-mono-sm text-outline">
                      <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-outline" />Other departures</span>
                      <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-secondary" />This deal ({euros(d.price)})</span>
                    </div>
                    <div className="rounded-lg bg-surface-container-lowest/60 p-2"><FareScatter comparison={d.comparison} depart={d.depart_date} /></div>
                    <span className="text-caption text-outline">
                      Compared with {similar} similar {similar === 1 ? "fare" : "fares"} (same route and trip length) seen in the last 30 days.
                    </span>
                  </>
                ) : (
                  <span className="text-body text-on-surface-variant">Not enough similar fares yet: CheapTrip is still learning this route's prices.</span>
                )}
              </Section>
            )}

            {d.comparison && d.comparison.history.length > 0 && (
              <Section title="This fare over time" right={`Seen ${d.comparison.history.length} ${d.comparison.history.length === 1 ? "time" : "times"}`}>
                {d.comparison.history.length > 1 ? <PriceSteps history={d.comparison.history} />
                  : <span className="text-body text-on-surface-variant">Seen once so far, at {euros(d.comparison.history[0].price)} ({when(d.comparison.history[0].at)}).</span>}
              </Section>
            )}

            <Trip d={d} />
            <Hotel d={d} />

            {(d.feasibility_notes.length > 0 || d.verdict) && (
              <Section title="Good to know">
                {d.verdict && <p className="text-body text-on-surface-variant">{d.verdict}</p>}
                {d.feasibility_notes.map((note) => (
                  <p key={note} className="flex items-start gap-space-xs text-body text-on-surface-variant">
                    <Icon name="info" size={16} className="text-primary" />{note}
                  </p>
                ))}
              </Section>
            )}
          </div>
        )}

        <div className="sticky bottom-0 z-20 flex items-center justify-between bg-surface-container-highest/60 p-space-md backdrop-blur-xl">
          <span className="flex items-center gap-space-xs text-caption text-outline">
            <Icon name="verified_user" size={16} className="text-secondary" />Prices are per person, in euros
          </span>
          <button className="btn-secondary" onClick={close}>Close</button>
        </div>
      </aside>
    </>
  );
}
