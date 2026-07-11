const API_URL = `${window.location.protocol}//${window.location.host}`;

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

export async function fetchCandles(asset) {
  const res = await fetch(`${API_URL}/candles?asset=${asset}&timeframe=1h&limit=100`);
  if (!res.ok) throw new Error("Failed to fetch candles");
  return res.json();
}
