import { useState } from "react";
import { api, type Airport, type DestinationTab } from "../api";
import { useReloadOn } from "../events";
import { euros, when } from "../format";
import { useLoad } from "../load";

const TABS: { tab: DestinationTab; label: string }[] = [
  { tab: "priority", label: "Priority" },
  { tab: "muted", label: "Muted" },
  { tab: "seen", label: "All seen (30 days)" },
];

/** Add an airport to the priority or muted list, with autocomplete. */
function AddAirport({ tab, onAdded }: { tab: "priority" | "muted"; onAdded: () => void }) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<Airport[]>([]);
  const search = (q: string) => {
    setQuery(q);
    if (q.trim().length >= 2) api.airports(q).then(setFound).catch(() => setFound([]));
    else setFound([]);
  };
  const add = (code: string) =>
    (tab === "priority" ? api.setPriority(code, true) : api.setMuted(code, true)).then(() => {
      setQuery(""); setFound([]); onAdded();
    });
  return (
    <div className="add-airport">
      <input type="search" placeholder="Add a city or airport" aria-label="Add a city or airport" value={query}
             onChange={(e) => search(e.target.value)} />
      {found.length > 0 && (
        <ul className="suggestions">
          {found.map((a) => (
            <li key={a.code}><button onClick={() => add(a.code)}>{a.city} · {a.name} ({a.code})</button></li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Destinations() {
  const [tab, setTab] = useState<DestinationTab>("priority");
  const list = useLoad(() => api.destinations(tab), [tab]);
  useReloadOn(["preferences_changed", "cycle_finished"], list.reload);
  const toggle = (call: Promise<unknown>) => call.then(list.reload);

  return (
    <section>
      <h1>Destinations</h1>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.tab} role="tab" aria-selected={tab === t.tab} className={tab === t.tab ? "active" : ""}
                  onClick={() => setTab(t.tab)}>{t.label}</button>
        ))}
      </div>
      {tab !== "seen" && <AddAirport tab={tab} onAdded={list.reload} />}
      {list.error && <p className="error">{list.error}</p>}
      <table>
        <thead><tr><th>Destination</th><th>Best price</th><th>Usual</th><th>Fares</th><th>Last seen</th><th /></tr></thead>
        <tbody>
          {list.data?.destinations.map((d) => (
            <tr key={d.code}>
              <td>{d.city} <span className="muted">{d.code}{d.country ? ` · ${d.country}` : ""}</span></td>
              <td>{euros(d.best_price)}{d.best_depart && <span className="muted"> · {d.best_depart}</span>}</td>
              <td>{euros(d.usual_price)}</td>
              <td>{d.fares}</td>
              <td>{when(d.last_seen)}</td>
              <td className="row-actions">
                <button onClick={() => toggle(api.setPriority(d.code, !d.is_priority))}>
                  {d.is_priority ? "Remove priority" : "Priority"}
                </button>
                <button onClick={() => toggle(api.setMuted(d.code, !d.is_muted))}>{d.is_muted ? "Unmute" : "Mute"}</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {list.data?.destinations.length === 0 && <p className="muted">None yet.</p>}
    </section>
  );
}
