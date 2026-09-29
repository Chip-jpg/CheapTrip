import type { Deal } from "../api";
import { dates, euros, TYPE_LABELS, when } from "../format";
import { href } from "../router";

/** One deal in a list: what, how much, how cheap, when, and where it was sent. */
export function DealCard({ deal }: { deal: Deal }) {
  const cheaper = deal.discount_pct && deal.usual_price
    ? `${Math.round(deal.discount_pct)}% below the usual ${euros(deal.usual_price)}`
    : deal.previous_price && deal.previous_price > deal.price ? `was ${euros(deal.previous_price)}` : null;
  return (
    <a className={`deal-card${deal.is_error_fare ? " error-fare" : ""}`} href={href({ screen: "deals", dealId: deal.id })}
       data-testid="deal-card">
      <div className="deal-top">
        <strong>{deal.route}</strong>
        <span className="price">{euros(deal.price)}</span>
      </div>
      <div className="muted">
        {[TYPE_LABELS[deal.type], dates(deal), deal.nights ? `${deal.nights} nights` : null, cheaper]
          .filter(Boolean).join(" · ")}
      </div>
      <div className="tags">
        {deal.is_error_fare && <span className="tag warn">Possible error fare</span>}
        {deal.is_priority && <span className="tag">Priority</span>}
        {deal.waiting && <span className="tag">Waiting to be sent</span>}
        {deal.notified && <span className="tag ok">Sent {when(deal.notified.at)} · {deal.notified.channels.join(" + ")}</span>}
      </div>
    </a>
  );
}
