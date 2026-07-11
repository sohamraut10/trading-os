import { API_URL } from "./api";

// Polling replacement for the old /ws/events WebSocket stream: serverless
// deployments (Vercel) can't hold a persistent connection open, and this
// endpoint is backed by the same Postgres `events` table either way, so
// polling works identically for local/Docker and Vercel deployments.
const POLL_INTERVAL_MS = 3000;

export function connectEvents(onMessage, onStatusChange) {
  let cursor = null;
  let stopped = false;
  let timer = null;

  async function poll() {
    if (stopped) return;
    try {
      const url = new URL(`${API_URL}/events/recent`);
      if (cursor) url.searchParams.set("after", cursor);
      const res = await fetch(url);
      if (!res.ok) throw new Error(`events poll failed: ${res.status}`);
      const events = await res.json();

      onStatusChange("connected");
      for (const event of events) {
        onMessage(event);
        cursor = event.event_id;
      }
    } catch (err) {
      console.error("Event poll error:", err);
      onStatusChange("disconnected");
    } finally {
      if (!stopped) timer = setTimeout(poll, POLL_INTERVAL_MS);
    }
  }

  onStatusChange("connecting");
  poll();

  return {
    close: () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    },
  };
}
