import { useState } from "react";
import { api } from "../api";
import { ComparisonChart, HistoryChart } from "../components/Charts";
import { dates, euros, TYPE_LABELS, when } from "../format";
import { useLoad } from "../load";
import { href, navigate } from "../router";

export function DealDetail({ id }: { id: string }) {
  const deal = useLoad(() => api.deal(id), [id]);
  const [busy, setBusy] = useState(false);
  const act = (call: () => Promise<unknown>, then?: () => void) => {
    setBusy(true);
    call().then(then ?? deal.reload).finally(() => setBusy(false));
  };

  if (deal.error) return <section><a href={href({ screen: "deals" })}>← Deals</a><p className="error">{deal.error}</p></section>;
  const d = deal.data;
  if (!d) return <section>…</section>;
  const city = d.destination?.city ?? d.route;
  return (
    <section>
      <a href={href({ screen: "deals" })}>← Deals</a>
      <h1>{d.route} · {euros(d.price)}</h1>
      <p className="muted">
        {[TYPE_LABELS[d.type], dates(d), d.nights ? `${d.nights} nights` : null, d.flight?.airline,
          d.flight?.stops === 0 ? "direct" : d.flight?.stops ? `${d.flight.stops} stop(s)` : null].filter(Boolean).join(" · ")}
      </p>
      {d.is_error_fare && <p className="tag warn">Possible error fare: book fast, verify after.</p>}
      <ul>
        {d.usual_price !== null && <li>Usual price {euros(d.usual_price)}{d.discount_pct ? ` (${Math.round(d.discount_pct)}% below)` : ""}</li>}
        {d.previous_price !== null && <li>Was {euros(d.previous_price)}</li>}
        {d.reasons.map((r) => <li key={r}>{r}</li>)}
        {d.hotel && <li>{d.hotel.name}: {euros(d.hotel.price_per_night, 2)}/night{d.hotel.rating ? `, ${d.hotel.rating}/10` : ""}</li>}
        {d.package_hotel && <li>Hotel: {d.package_hotel}</li>}
        {d.legs.map((leg, i) => <li key={i}>{leg.from?.city} → {leg.to?.city}: {euros(leg.price)}</li>)}
        <li>Found by {d.sources.join(", ")} · booking confidence {d.confidence}</li>
        {d.notified ? <li>Sent {when(d.notified.at)} to {d.notified.channels.join(" and ")}</li>
          : d.waiting ? <li>Waiting to be sent (hourly limit or alerts paused)</li> : null}
      </ul>

      <div className="actions">
        {d.links.book && <a className="button primary" href={d.links.book} target="_blank" rel="noreferrer">Book</a>}
        {d.links.hotels && <a className="button" href={d.links.hotels} target="_blank" rel="noreferrer">Hotels in {city}</a>}
        {d.destination && (
          <>
            <button disabled={busy} onClick={() => act(() => api.setPriority(d.destination!.code, !d.is_priority))}>
              {d.is_priority ? "Remove priority" : `Make ${city} a priority`}
            </button>
            <button disabled={busy} onClick={() => act(() => api.setMuted(d.destination!.code, !d.is_muted))}>
              {d.is_muted ? `Unmute ${city}` : `Mute ${city}`}
            </button>
          </>
        )}
        <button disabled={busy} onClick={() => act(() => api.hideDeal(d.id), () => navigate({ screen: "deals" }))}>
          Hide this deal
        </button>
      </div>

      {d.comparison && (
        <>
          <h2>How this fare compares</h2>
          <p className="muted">Similar fares departing within ±{d.comparison.window_days} days, each at its latest price.</p>
          <ComparisonChart comparison={d.comparison} />
          <h2>This fare over time</h2>
          <HistoryChart history={d.comparison.history} />
        </>
      )}
    </section>
  );
}
