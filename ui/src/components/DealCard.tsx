/**
 * Deal cards, as designed: the large card for unusually cheap deals (a variant
 * per deal type), the compact card, and the pieces they share (type badge,
 * discount pill, price, actions menu).
 */
import type { ReactNode } from "react";
import { api, type Deal } from "../api";
import { ago, clock, dates, euros, pct, reasons, shortDay, TYPE_LABELS, tripDates } from "../format";
import { href, navigate } from "../router";
import { sourceName } from "../sources";
import { Sparkline } from "./Charts";
import { Icon } from "./Icon";
import { MenuItem, Popover } from "./Popover";
import { useToast } from "./Toast";

export function cityOf(deal: Deal): string {
  return deal.destination?.city ?? deal.hotel?.location.split(",")[0] ?? deal.route;
}

/** "Milan → Berlin" for flights; the hotel's name for a hotel. */
export function title(deal: Deal): string {
  if (deal.type === "hotel" && deal.hotel) return deal.hotel.name;
  return deal.route;
}

/** "BGY → BER (direct, Ryanair)". */
export function codes(deal: Deal): string {
  if (deal.type === "hotel") return deal.hotel?.location ?? "";
  const route = deal.origin && deal.destination ? `${deal.origin.code} → ${deal.destination.code}` : "";
  const flight = deal.flight;
  const detail = [flight?.stops === 0 ? "direct" : flight?.stops ? `${flight.stops} stop${flight.stops > 1 ? "s" : ""}` : null,
                  flight?.airline].filter(Boolean).join(", ");
  return detail ? `${route} (${detail})` : route;
}

export function priceTone(deal: Deal): string {
  if (deal.is_error_fare) return "text-error";
  return deal.discount_pct ? "text-secondary" : "text-primary";
}

export function TypeBadge({ deal }: { deal: Deal }) {
  if (deal.is_error_fare) {
    return <span className="badge whitespace-nowrap bg-error-container text-on-error-container"><Icon name="warning" size={13} />Error fare</span>;
  }
  const styles: Record<Deal["type"], string> = {
    flight: "bg-primary-container text-on-primary",
    one_way: "bg-primary-container text-on-primary",
    flight_hotel: "bg-primary-container text-on-primary",
    package: "bg-tertiary-container text-on-tertiary-container",
    hotel: "bg-surface-container-highest text-on-surface-variant",
    via_hub: "bg-tertiary-container/30 text-tertiary-fixed",
  };
  const hub = deal.type === "via_hub" ? deal.legs[0]?.to?.city : null;
  return <span className={`badge whitespace-nowrap ${styles[deal.type]}`}>{hub ? `Via ${hub}` : deal.type === "hotel" ? "Hotel only" : TYPE_LABELS[deal.type]}</span>;
}

const CONFIDENCE: Record<string, [string, string]> = {
  high: ["High confidence", "bg-secondary-container/20 text-secondary"],
  medium: ["Medium confidence", "bg-tertiary-container/20 text-tertiary"],
  low: ["Low confidence", "bg-surface-container-highest text-on-surface-variant"],
};

export function ConfidenceBadge({ deal }: { deal: Deal }) {
  const [label, style] = CONFIDENCE[deal.confidence] ?? CONFIDENCE.low;
  return (
    <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-caption font-semibold whitespace-nowrap ${style}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />{label}
    </span>
  );
}

export function PriorityBadge() {
  return (
    <span className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-caption font-semibold text-tertiary" title="Priority destination">
      <Icon name="star" size={13} filled />Priority
    </span>
  );
}

/** "-46%" in the deal colour (or amber-red for a possible error fare). */
export function DeltaPill({ deal, suffix = "" }: { deal: Deal; suffix?: string }) {
  if (!deal.discount_pct) return null;
  const style = deal.is_error_fare ? "bg-error-container text-on-error-container" : "bg-secondary-container text-on-secondary-container";
  return <span className={`delta ${style}`}>-{pct(deal.discount_pct)}{suffix}</span>;
}

/** Where the alert went: "Desktop + Telegram". */
export function channelsText(deal: Deal): string {
  return (deal.notified?.channels ?? []).map((c) => c.charAt(0).toUpperCase() + c.slice(1)).join(" + ");
}

/** When it was (or will be) notified: "Sent 10:42", "Waiting to be sent", "Found 4 min ago". */
export function notifiedText(deal: Deal): string {
  if (deal.notified) return `Sent ${clock(deal.notified.at)}`;
  if (deal.waiting) return "Waiting to be sent";
  return `Found ${ago(deal.found_at)}`;
}

/** Mute, priority, copy link and hide, with a confirmation (and Undo where it can be undone). */
export function useDealActions(onChange?: () => void) {
  const toast = useToast();
  const done = (text: string, undo?: () => Promise<unknown>) => {
    onChange?.();
    toast(text, undo ? { action: { label: "Undo", run: () => undo().then(() => onChange?.()) } } : {});
  };
  const failed = (err: Error) => toast(err.message, { tone: "error" });
  return {
    mute(deal: Deal) {
      const code = deal.destination?.code;
      if (!code) return;
      const on = !deal.is_muted;
      api.setMuted(code, on).then(() => done(on ? `${cityOf(deal)} muted: no more alerts for it` : `${cityOf(deal)} unmuted`,
        () => api.setMuted(code, !on))).catch(failed);
    },
    prioritize(deal: Deal) {
      const code = deal.destination?.code;
      if (!code) return;
      const on = !deal.is_priority;
      api.setPriority(code, on).then(() => done(on ? `${cityOf(deal)} is a priority: every fare to it alerts`
        : `${cityOf(deal)} is no longer a priority`, () => api.setPriority(code, !on))).catch(failed);
    },
    hide(deal: Deal) {
      api.hideDeal(deal.id).then(() => done("Deal hidden")).catch(failed);
    },
    copyLink(deal: Deal) {
      const link = deal.links.book ?? deal.links.hotels;
      if (!link) return;
      navigator.clipboard.writeText(link).then(() => toast("Booking link copied")).catch(failed);
    },
  };
}

export function DealMenu({ deal, onChange }: { deal: Deal; onChange?: () => void }) {
  const actions = useDealActions(onChange);
  const city = cityOf(deal);
  return (
    <Popover align="right" className="w-56" button={(open, toggle) => (
      <button className="btn-icon" onClick={toggle} aria-expanded={open} aria-haspopup="menu" aria-label={`More for ${deal.route}`}>
        <Icon name="more_vert" size={18} />
      </button>
    )}>
      {(close) => (
        <>
          {deal.destination && (
            <>
              <MenuItem onSelect={() => { actions.mute(deal); close(); }}>
                <Icon name={deal.is_muted ? "notifications" : "notifications_off"} size={16} />
                {deal.is_muted ? `Unmute ${city}` : `Mute ${city}`}
              </MenuItem>
              <MenuItem onSelect={() => { actions.prioritize(deal); close(); }}>
                <Icon name="star" size={16} filled={deal.is_priority} />
                {deal.is_priority ? `Remove ${city} from priorities` : `Make ${city} a priority`}
              </MenuItem>
            </>
          )}
          {(deal.links.book || deal.links.hotels) && (
            <MenuItem onSelect={() => { actions.copyLink(deal); close(); }}><Icon name="link" size={16} />Copy link</MenuItem>
          )}
          <MenuItem onSelect={() => { actions.hide(deal); close(); }}><Icon name="visibility_off" size={16} />Hide this deal</MenuItem>
        </>
      )}
    </Popover>
  );
}

function Well({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`well flex flex-col gap-1 p-space-sm ${className}`}>{children}</div>;
}

/** The card's middle: what matters most for this kind of deal. */
function Middle({ deal }: { deal: Deal }) {
  if (deal.is_error_fare) {
    return (
      <div className="flex items-start gap-space-xs rounded-lg bg-error-container/20 p-space-sm text-on-surface">
        <Icon name="priority_high" size={18} className="text-error" />
        <span className="text-body">Possible error fare: book fast, verify after. Airlines sometimes cancel these, so don't
          book non-refundable hotels until the ticket is issued.</span>
      </div>
    );
  }
  if (deal.type === "via_hub" && deal.legs.length) {
    return (
      <Well className="gap-2 font-mono text-mono-sm">
        {deal.legs.map((leg, i) => (
          <div key={i} className="flex items-center justify-between">
            <span className="flex items-center gap-1.5">
              <span className={`font-semibold ${i ? "text-secondary" : "text-primary"}`}>Leg {i + 1}:</span>
              <span className="text-on-surface">{leg.from?.code} → {leg.to?.code} ({sourceName(leg.source)})</span>
            </span>
            <span className="text-on-surface-variant">{euros(leg.price)}</span>
          </div>
        ))}
        <span className="text-caption text-outline">Two separate tickets: allow time to change at {deal.legs[0].to?.city}.</span>
      </Well>
    );
  }
  if ((deal.type === "package" || deal.type === "flight_hotel") && (deal.package_hotel || deal.hotel)) {
    const hotel = deal.hotel;
    return (
      <Well>
        <span className="flex items-center gap-space-xs text-body text-on-surface">
          <Icon name="hotel" size={16} className="text-primary" />
          {deal.nights ? `${deal.nights} nights at ` : ""}{hotel?.name ?? deal.package_hotel}
        </span>
        <span className="flex justify-between text-caption text-outline">
          <span>{deal.type === "package" ? "Flight + hotel, per person" : `Flight + hotel ${hotel ? euros(hotel.total) : ""}`}</span>
          {hotel?.rating ? <span>{hotel.rating}/10{hotel.review_count ? ` · ${hotel.review_count} reviews` : ""}</span> : null}
        </span>
      </Well>
    );
  }
  if (deal.type === "hotel" && deal.hotel) {
    const hotel = deal.hotel;
    return (
      <Well>
        <span className="flex items-center gap-space-xs text-body text-on-surface">
          <Icon name="calendar_month" size={16} className="text-primary" />
          {hotel.check_in ? `${shortDay(hotel.check_in)} → ${shortDay(hotel.check_out)}` : hotel.travel_window ?? "Dates on the site"}
        </span>
        <span className="text-caption text-outline">
          {hotel.nights ? `${euros(hotel.total)} for ${hotel.nights} nights · ` : ""}{hotel.price_basis ?? "per night"}
        </span>
      </Well>
    );
  }
  const points = deal.comparison?.points ?? [];
  if (points.length >= 2) {
    const prices = points.map((p) => p.price);
    const lowest = Math.min(...prices) === deal.price;
    return (
      <Well>
        <div className="flex items-center justify-between text-caption text-outline">
          <span>Similar fares ±{deal.comparison!.window_days} days</span>
          <span className="font-mono text-mono-sm text-secondary">{euros(deal.price)}{lowest ? " (lowest)" : ""}</span>
        </div>
        <Sparkline comparison={deal.comparison!} />
        <div className="flex justify-between text-caption text-outline">
          <span>{euros(Math.max(...prices))} peak</span>
          {deal.comparison!.median !== null && <span>Usual {euros(deal.comparison!.median)}</span>}
          <span className="text-secondary">Now {euros(deal.price)}</span>
        </div>
      </Well>
    );
  }
  if (deal.usual_price === null) {
    return (
      <Well className="flex-row items-center gap-space-xs text-caption text-outline">
        <Icon name="hourglass_top" size={16} />No usual price yet · learning
      </Well>
    );
  }
  return null;
}

function BookButton({ deal }: { deal: Deal }) {
  const link = deal.links.book;
  if (deal.type === "via_hub") {
    return <a className="btn-primary" href={href({ screen: "deals", dealId: deal.id })}><Icon name="alt_route" size={18} />Both legs {euros(deal.price)}</a>;
  }
  if (!link) return <a className="btn-primary" href={href({ screen: "deals", dealId: deal.id })}>Details</a>;
  const [label, icon] = deal.is_error_fare ? [`Grab ${euros(deal.price)}`, "bolt"]
    : deal.type === "package" ? ["View package", "open_in_new"]
      : deal.type === "hotel" ? ["Reserve room", "bed"]
        : [`Book ${euros(deal.price)}`, "flight_takeoff"];
  return (
    <a className={deal.is_error_fare ? "btn-danger" : "btn-primary"} href={link} target="_blank" rel="noreferrer"
       title="Opens the booking page in your browser">
      <Icon name={icon} size={18} />{label}
    </a>
  );
}

/** An unusually cheap deal (the big card). */
export function DealCard({ deal, onChange }: { deal: Deal; onChange?: () => void }) {
  const why = reasons(deal.reasons).slice(0, 2).join("; ");
  const perNight = deal.type === "hotel" && deal.hotel;
  return (
    <article data-testid="deal-card"
             className={`card group flex flex-col justify-between p-space-md transition-colors hover:border-stroke-strong hover:bg-surface-container-highest ${deal.is_error_fare ? "border-error/40" : ""}`}>
      <div className="flex flex-col gap-space-sm">
        <div className="flex items-center justify-between gap-space-xs">
          <div className="flex min-w-0 items-center gap-space-xs overflow-hidden">
            <TypeBadge deal={deal} />
            {deal.type === "package" || deal.type === "hotel"
              ? <span className="truncate rounded bg-surface-container px-2 py-0.5 text-caption font-semibold text-on-surface-variant">{deal.sources.map(sourceName).join(" · ")}</span>
              : <ConfidenceBadge deal={deal} />}
            {deal.is_priority && <PriorityBadge />}
          </div>
          <span className={`shrink-0 font-mono text-mono-sm ${deal.waiting ? "text-tertiary" : "text-outline"}`}
                title={deal.notified ? `Alert sent to ${channelsText(deal)}` : undefined}>{notifiedText(deal)}</span>
        </div>

        <div className="flex flex-col">
          <div className="flex items-baseline justify-between gap-space-sm">
            <h3 className="truncate text-headline-sm tracking-tight text-on-surface"><a href={href({ screen: "deals", dealId: deal.id })} className="hover:underline">{title(deal)}</a></h3>
            <div className="flex shrink-0 items-baseline gap-1.5">
              {deal.previous_price !== null && deal.previous_price > deal.price && (
                <span className="font-mono text-mono-sm text-outline line-through" title="Price before it dropped">{euros(deal.previous_price)}</span>
              )}
              <span className={`font-mono text-mono-lg ${priceTone(deal)}`}>
                {perNight ? euros(deal.hotel!.price_per_night, 2) : euros(deal.price)}
                {perNight ? <span className="text-caption text-outline">/night</span>
                  : deal.type === "package" ? <span className="text-caption text-outline">/pp</span> : null}
              </span>
            </div>
          </div>
          <div className="flex items-center justify-between gap-space-sm font-mono text-mono-sm text-on-surface-variant">
            <span className="truncate">{codes(deal)}</span>
            <DeltaPill deal={deal} />
          </div>
          {deal.type === "hotel" && deal.hotel?.rating ? (
            <span className="mt-0.5 flex items-center gap-space-xs font-mono text-mono-sm text-outline">
              <span className="rounded bg-surface-container-lowest px-1.5 font-semibold text-primary">{deal.hotel.rating} / 10</span>
              {deal.hotel.review_count ? `${deal.hotel.review_count} reviews` : ""}
            </span>
          ) : (
            <span className="mt-0.5 text-body text-outline">{tripDates(deal)}</span>
          )}
        </div>

        <Middle deal={deal} />

        {why && (
          <div className="flex items-start gap-space-xs text-caption text-on-surface-variant">
            <Icon name="auto_awesome" size={16} className="text-primary" />
            <span>Why it's here: {why}</span>
          </div>
        )}
        <div className="flex items-center gap-space-xs text-caption text-outline">
          <Icon name="sync_alt" size={14} />
          <span>{deal.sources.map(sourceName).join(" · ")}{deal.notified ? ` · alert sent to ${channelsText(deal)}` : ""}</span>
        </div>
      </div>

      <div className="mt-space-sm flex items-center justify-between gap-space-xs pt-space-md">
        <div className="flex items-center gap-space-xs">
          <BookButton deal={deal} />
          {deal.links.hotels && (
            <a className="btn-secondary px-space-sm" href={deal.links.hotels} target="_blank" rel="noreferrer"
               title={`Hotels in ${cityOf(deal)} for these dates (Booking.com)`}>
              <Icon name="hotel" size={16} />Hotels
            </a>
          )}
        </div>
        <div className="flex items-center">
          <a className="btn-ghost px-space-sm font-mono text-mono-sm text-primary" href={href({ screen: "deals", dealId: deal.id })}>
            Details<Icon name="chevron_right" size={16} />
          </a>
          <DealMenu deal={deal} onChange={onChange} />
        </div>
      </div>
    </article>
  );
}

/** A smaller unusually cheap deal (after the first six). */
export function CompactDealCard({ deal }: { deal: Deal }) {
  return (
    <article data-testid="deal-card" onClick={() => navigate({ screen: "deals", dealId: deal.id })}
             className="pane flex cursor-pointer flex-col justify-between gap-space-sm p-space-md transition-colors hover:bg-surface-container-high">
      <div>
        <div className="flex items-center justify-between font-mono text-mono-sm text-outline">
          <span>{deal.origin && deal.destination ? `${deal.origin.code} → ${deal.destination.code}` : TYPE_LABELS[deal.type]}</span>
          {deal.discount_pct ? <span className={`font-semibold ${deal.is_error_fare ? "text-error" : "text-secondary"}`}>-{pct(deal.discount_pct)}</span> : null}
        </div>
        <h4 className="mt-0.5 truncate text-headline-sm text-on-surface">{deal.type === "hotel" ? title(deal) : cityOf(deal)}</h4>
        <span className="text-body text-outline">{dates(deal)}{deal.nights ? ` (${deal.nights}n)` : ""}</span>
      </div>
      <div className="flex items-baseline justify-between pt-space-xs">
        <span className="flex items-baseline gap-1">
          <span className={`font-mono text-mono-lg ${deal.is_error_fare ? "text-error" : "text-on-surface"}`}>{euros(deal.price)}</span>
          {deal.usual_price !== null && deal.usual_price > deal.price && (
            <span className="font-mono text-mono-sm text-outline line-through" title="Usual price">{euros(deal.usual_price)}</span>
          )}
        </span>
        {deal.links.book && (
          <a className="btn h-7 border border-stroke bg-surface-container-highest px-space-sm text-on-surface hover:bg-primary-container hover:text-on-primary"
             href={deal.links.book} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>Book</a>
        )}
      </div>
    </article>
  );
}
