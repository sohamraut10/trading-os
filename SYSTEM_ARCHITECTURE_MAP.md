# 🌌 Fable & Omega Ecosystem: End-to-End System State

This document provides a comprehensive map of the entire architecture built so far, detailing how the components interact and suggesting logical next steps for the ecosystem.

---

## 0. Canonical ports & env (Fable OS monorepo)

| Surface | Port / URL | Notes |
|---------|------------|-------|
| Host-local Omega (`make backend` from monorepo root) | **`:8003`** | `uvicorn api.main:app --host 0.0.0.0 --port 8003` |
| Fable frontend (`make frontend`) | `:3000` | Next.js 15 |
| Frontend → Omega env | `OMEGA_API_URL=http://localhost:8003` | `TRADING_OS_URL` is a deprecated alias |
| Docker Compose API | `api:8003` / host `localhost:8003` | Container listen, host map, and in-network all **8003** |
| Compose Fable → Omega | `OMEGA_API_URL` / `TRADING_OS_URL` = `http://api:8003` | Same Docker network; no `host.docker.internal` for Omega |

Auth: protected routes require `API_AUTH_TOKEN` (`X-API-Key` or `Authorization: Bearer`). Empty token → fail-closed `503`. `/v1/tasks*` is gated.

`api/main.py` is still a large monolith (~1590 lines); split deferred — not blocking current work.

---

## 1. System Architecture Overview

The system is currently divided into three primary domains, heavily integrated via Docker (Trading OS network) and Redis:

### A. Fable (The Interface & Orchestrator)
Fable is a Next.js 15 App Router application that acts as the front door to the entire system (sibling `frontend/` repo in the Fable OS monorepo).
* **Voice Agent:** A real-time voice interface powered by TTS and an LLM chain.
* **LLM Chain (`lib/fable/llm.ts`):** A resilient, multi-backend router supporting `cli-agy`, `cli-claude`, `cli-grok`, Anthropic API, Groq, Ollama, Gemini, and NVIDIA. If one API rate-limits, it automatically fails over to the next and puts the failed backend on a 5-minute cooldown.
* **Context Injection:** Intercepts queries to fetch live quotes (Yahoo Finance), portfolio state, and trading signals, directly injecting them into the system prompt.
* **Omega Integration:** Parses `<omega_dispatch>` and `<omega_check>` XML tags from the LLM's output to asynchronously trigger heavy analytical tasks on the backend at `OMEGA_API_URL` (**`:8003`** locally).

### B. Omega & Trading OS (The Analytical Engine)
The FastAPI backend (`api/main.py` in this repo / `sohamraut10/trading-os`) that executes trades and runs deep quantitative analysis.
* **The Agent Council (`StateGraph`):** A pure-Python, zero-dependency async state machine. Heavy tasks are routed through a gauntlet of specialized agents (Archivist, Harbinger, Seer, Warden, and finally Fable).
* **The Scratchpad:** A shared dictionary memory structure that allows agents to read each other's outputs concurrently without thread-locking or race conditions.
* **Execution & Risk:** Handled by `trading-os` through smart routing, bracket orders, and a strict risk engine.

### C. The Dashboards (User & Ops)
We recently built out two massive visualization platforms inside Fable:

#### The Platform (`/platform`) — User-Facing
* **Playground:** Interact with Omega agents directly.
* **Memory Inspector:** Real-time visibility into the RAG pipeline (Redis keys, summaries, TTLs).
* **Usage & Costs:** Tracking token burn and API spend across different models.
* **Settings:** User configuration for trading endpoints and API keys.

#### The Console (`/console`) — Developer/Ops-Facing
* **Overview:** High-level metrics (p50/p95 latency, 24h turns), `recharts` visualizations of backend distribution, and a live health-strip showing LLM cooldown states.
* **Logs (The Ledger):** A reverse-chronological ledger (clamped to 2000 records) of every single system interaction, recording duration, symbols, and injected context. Clicking a row reveals the exact JSON trace.
* **Sessions Manager:** Real-time tracking of active Redis sessions, their TTLs, and rolling summaries.
* **Debug Playground:** A specialized chat interface that forces `x-fable-debug` headers, exposing the exact raw context and system prompts injected under the hood.

---

## 2. Infrastructure & Storage
* **Compute:** Managed via `docker-compose.prod.yml` running on OrbStack.
* **Memory (Redis):** Acts as the nervous system.
  * `fable:sess:{id}:history`: Rolling chat history.
  * `fable:sess:{id}:summary`: Condensed, long-term memory via LLM summarization.
  * `fable:ledger`: The global fire-and-forget telemetry ledger for the Console.
* **Database (Postgres):** Historical trade journaling and long-term Omega task persistence.

---

## 3. Strategic Suggestions for Next Phases

Now that the foundational architecture, multi-agent processing, and comprehensive observability dashboards are complete, here are the highest-impact areas to target next:

### Suggestion 1: WebSockets for Omega Task Streaming
* **Current State:** The frontend polls for Omega task updates or relies on the user asking "is it done yet?".
* **The Upgrade:** Implement a WebSocket or Server-Sent Events (SSE) bridge between Fable and the FastAPI backend.
* **Impact:** The moment the `Warden` agent approves a trade on the backend, the Fable UI instantly flashes green and updates the portfolio in real-time without requiring a page refresh or polling.

### Suggestion 2: Long-Term Memory (Vector Database)
* **Current State:** Fable relies on rolling Redis summaries to remember user preferences. This is great for active sessions but fades over months.
* **The Upgrade:** Integrate `pgvector` into the existing Postgres container. When a session ends, embed the final summary and store it.
* **Impact:** If you tell Fable "I never trade on Fridays," it will permanently remember this constraint by querying the vector database before every future execution, effectively giving Fable a permanent, evolving personality.

### Suggestion 3: Interactive Trading UI in the Console
* **Current State:** The console tracks LLM latency and chat logs.
* **The Upgrade:** Add an "Order Book / Execution" tab to `/console`. Let the developer manually override the `Warden` agent, force-liquidate positions, or manually trigger the `Harbinger` agent to scan a specific ticker on demand.
* **Impact:** Turns the Fable Console from an observability tool into a full "Bloomberg Terminal" command center.

### Suggestion 4: Automated Backtesting Loop
* **Current State:** Omega runs live analysis and execution.
* **The Upgrade:** Expose a `/v1/backtest` endpoint on the FastAPI side. Allow Fable to spin up a simulated environment where the Agent Council processes 2 years of historical data over a weekend.
* **Impact:** You can definitively prove the efficacy of the Agent Council's logic before risking live capital.
