// VITE_API_BASE is a build-time env var: "" for local/Docker (FastAPI serves
// routes at root), "/api" when deployed to Vercel (routes are mounted there
// — see api/index.py and vercel.json).
export const API_URL = `${window.location.protocol}//${window.location.host}${import.meta.env.VITE_API_BASE || ""}`;

export async function fetchPortfolio() {
  const res = await fetch(`${API_URL}/portfolio`);
  if (!res.ok) throw new Error("Failed to fetch portfolio");
  return res.json();
}

export async function fetchAgentPerformance() {
  const res = await fetch(`${API_URL}/agents/performance`);
  if (!res.ok) throw new Error("Failed to fetch agent performance");
  return res.json();
}

export async function pinStrategy(strategyType) {
  const res = await fetch(`${API_URL}/strategy/select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ strategy: strategyType }),
  });
  if (!res.ok) throw new Error("Failed to pin strategy");
  return res.json();
}

export async function fetchCycleEvents(cycleId) {
  const res = await fetch(`${API_URL}/cycles/${cycleId}/events`);
  if (!res.ok) throw new Error("Failed to fetch cycle events");
  return res.json();
}

export async function fetchCandles(asset, source = "") {
  const params = new URLSearchParams({ asset, timeframe: "1h", limit: 100 });
  if (source) params.set("source", source);
  const res = await fetch(`${API_URL}/candles?${params}`);
  if (!res.ok) throw new Error("Failed to fetch candles");
  return res.json();
}

export async function fetchPairSuggestions() {
  const res = await fetch(`${API_URL}/pairs/suggest`);
  if (!res.ok) return { pairs: [], broker: "" };
  return res.json();
}

export async function searchPairs(query) {
  const res = await fetch(`${API_URL}/pairs/search?q=${encodeURIComponent(query)}`);
  if (!res.ok) return { pairs: [], broker: "" };
  return res.json();
}

export async function fetchOptionExpiries(symbol) {
  const res = await fetch(`${API_URL}/options/expiries?symbol=${encodeURIComponent(symbol)}`);
  if (!res.ok) return { expiries: [] };
  return res.json();
}

export async function fetchOptionChain(symbol, expiry) {
  const res = await fetch(`${API_URL}/options/chain?symbol=${encodeURIComponent(symbol)}&expiry=${encodeURIComponent(expiry)}`);
  if (!res.ok) return { spot: 0, strikes: [] };
  return res.json();
}

export async function analyzeAsset(asset, timeframe = "1h", candle_limit = 100) {
  const res = await fetch(`${API_URL}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset, timeframe, candle_limit, execute_if_signal: false }),
  });
  if (!res.ok) throw new Error("Analysis failed");
  return res.json();
}
