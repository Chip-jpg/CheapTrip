import { api } from "../api";
import { useReloadOn } from "../events";
import { euros, when } from "../format";
import { useLoad } from "../load";

/** Activity: are the sources working, what was sent, and every search. */
export function Activity() {
  const activity = useLoad(api.activity, []);
  useReloadOn(["cycle_finished", "alert_sent", "digest_sent", "settings_changed"], activity.reload);
  const a = activity.data;
  if (activity.error) return <section><h1>Activity</h1><p className="error">{activity.error}</p></section>;
  if (!a) return <section><h1>Activity</h1>…</section>;
  return (
    <section>
      <h1>Activity</h1>
      <h2>Sources</h2>
      <table>
        <thead><tr><th>Source</th><th>Status</th><th>Last run</th><th>Found</th><th>Note</th></tr></thead>
        <tbody>
          {a.sources.map((s) => (
            <tr key={s.id}>
              <td>{s.id}</td>
              <td><span className={`tag status-${s.status.toLowerCase()}`}>{s.enabled ? s.status.toLowerCase() : "off"}</span></td>
              <td>{when(s.last_run_at)}</td>
              <td>{s.last_count ?? "–"}</td>
              <td className="muted">{s.enabled ? s.last_error ?? "" : s.reason ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Alerts</h2>
      <p className="muted">
        {a.alerts.sent_last_hour} of {a.alerts.per_hour_limit} instant alerts sent in the last hour
        {a.alerts.waiting ? ` · ${a.alerts.waiting} waiting` : ""}
      </p>
      <table>
        <thead><tr><th>Sent</th><th>Kind</th><th>Deal</th><th>Price</th><th>Where</th></tr></thead>
        <tbody>
          {a.alerts.log.map((entry, i) => (
            <tr key={`${entry.hash}-${i}`}>
              <td>{when(entry.sent_at)}</td>
              <td>{entry.alert_tier}</td>
              <td>{entry.route ?? <span className="muted">(no longer saved)</span>}</td>
              <td>{euros(entry.total_cost)}</td>
              <td>{entry.channel.split(",").join(" + ")}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Searches</h2>
      <table>
        <thead><tr><th>Started</th><th>Why</th><th>Result</th><th>Fares</th><th>Deals</th><th>Took</th></tr></thead>
        <tbody>
          {a.searches.map((c) => (
            <tr key={c.id}>
              <td>{when(c.started_at)}</td>
              <td>{c.trigger}</td>
              <td className={c.status === "failed" ? "error" : ""}>{c.status}{c.error ? `: ${c.error}` : ""}</td>
              <td>{c.fares ?? "–"}</td>
              <td>{(c.new_instant ?? 0) + (c.new_digest ?? 0)}{c.sent ? ` (${c.sent} sent)` : ""}</td>
              <td>{c.duration_s !== null ? `${Math.round(c.duration_s)} s` : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
