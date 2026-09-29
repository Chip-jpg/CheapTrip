import { useState } from "react";
import { api } from "../api";
import { useLoad } from "../load";

const MARK = { ok: "✅", warn: "⚠️", fail: "❌" } as const;

/** First-run checks: keys, Telegram, preferences, database, sources (`cheaptrip doctor`). */
export function Setup() {
  const checks = useLoad(api.checks, []);
  const [message, setMessage] = useState<string>();
  return (
    <section>
      <h1>Setup checks</h1>
      <button onClick={checks.reload} disabled={checks.loading}>Check again</button>{" "}
      <button onClick={() => api.testTelegram()
        .then((r) => setMessage(r.detail)).catch((err: Error) => setMessage(err.message))}>Send a Telegram test</button>{" "}
      <button onClick={() => api.testNotification("desktop")
        .then(() => setMessage("Desktop notification sent")).catch((err: Error) => setMessage(err.message))}>
        Send a desktop test
      </button>
      {message && <p className="notice" role="status">{message}</p>}
      {checks.error && <p className="error">{checks.error}</p>}
      <ul className="checks">
        {checks.data?.checks.map((c) => (
          <li key={c.name} className={`check-${c.level}`}>{MARK[c.level]} <strong>{c.name}</strong>: {c.detail}</li>
        ))}
      </ul>
    </section>
  );
}
