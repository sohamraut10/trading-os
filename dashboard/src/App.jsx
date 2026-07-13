import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Activity, BarChart2, Brain, ShieldAlert, TrendingUp, TrendingDown, Minus,
  Server, Terminal, Cpu, Globe, Database, Lock, Zap, ChevronDown,
  RefreshCw, AlertTriangle, CheckCircle, Search,
} from 'lucide-react';
import { createChart } from 'lightweight-charts';
import { fetchPortfolio, fetchPairSuggestions, analyzeAsset, fetchSystem, fetchCandles } from './api';
import { connectEvents } from './eventsPoller';

// ── constants ─────────────────────────────────────────────────────────────────

const DEFAULT_WATCHLIST = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "NATURALGAS"];

const AGENT_META = {
  Technical: { label: "Technical Analyst",  icon: BarChart2,   color: "blue",   indicators: ["RSI", "MACD", "EMA", "VWAP"] },
  Sentiment: { label: "Sentiment & News",    icon: Globe,       color: "purple", indicators: ["LLM NLP", "Keywords"] },
  Quant:     { label: "Quant & Statistical", icon: TrendingUp,  color: "orange", indicators: ["Hurst", "Z-Score", "Kelly EV"] },
  OrderFlow: { label: "Market Structure",    icon: Database,    color: "indigo", indicators: ["Volume Profile", "S/R", "Delta"] },
};

const COLOR = {
  blue:   { bg: "bg-blue-500/10",   border: "border-blue-500/20",   icon: "text-blue-400",   conf: "text-blue-400" },
  purple: { bg: "bg-purple-500/10", border: "border-purple-500/20", icon: "text-purple-400", conf: "text-purple-400" },
  orange: { bg: "bg-orange-500/10", border: "border-orange-500/20", icon: "text-orange-400", conf: "text-orange-400" },
  indigo: { bg: "bg-indigo-500/10", border: "border-indigo-500/20", icon: "text-indigo-400", conf: "text-indigo-400" },
};

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtMoney(val, currency = "INR") {
  const sym = currency === "USD" ? "$" : "₹";
  if (!val || isNaN(val)) return `${sym}0`;
  if (val >= 1e7) return `${sym}${(val / 1e7).toFixed(2)}Cr`;
  if (val >= 1e5) return `${sym}${(val / 1e5).toFixed(2)}L`;
  if (val >= 1e3) return `${sym}${(val / 1e3).toFixed(1)}k`;
  return `${sym}${val.toFixed(2)}`;
}

function signalCls(action) {
  if (action === "BUY")  return "text-emerald-400";
  if (action === "SELL") return "text-rose-400";
  return "text-amber-400";
}

function signalBadge(action) {
  if (action === "BUY")  return "bg-emerald-500/20 text-emerald-400 border border-emerald-500/20";
  if (action === "SELL") return "bg-rose-500/20 text-rose-400 border border-rose-500/20";
  return "bg-neutral-700 text-neutral-300 border border-neutral-600";
}

function signalBg(action) {
  if (action === "BUY")  return "bg-emerald-500/10 border-emerald-500/30 text-emerald-400";
  if (action === "SELL") return "bg-rose-500/10 border-rose-500/30 text-rose-400";
  return "bg-amber-500/10 border-amber-500/30 text-amber-400";
}

// ── Candlestick chart (lightweight-charts + Dhan candle data) ─────────────────

const TIMEFRAMES = [
  { label: "1m",  value: "1m",  limit: 120 },
  { label: "5m",  value: "5m",  limit: 100 },
  { label: "15m", value: "15m", limit: 100 },
  { label: "1h",  value: "1h",  limit: 100 },
  { label: "1d",  value: "1d",  limit: 200 },
];

function PriceChart({ asset, source }) {
  const containerRef = useRef(null);
  const chartRef     = useRef(null);
  const seriesRef    = useRef(null);
  const [tf, setTf]           = useState("1h");
  const [loading, setLoading] = useState(false);
  const [err, setErr]         = useState(null);
  const [bars, setBars]       = useState(0);

  // init chart once on mount
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: "#0a0a0a" }, textColor: "#525252" },
      grid:   { vertLines: { color: "#171717" }, horzLines: { color: "#171717" } },
      rightPriceScale: { borderColor: "#262626" },
      timeScale: { borderColor: "#262626", timeVisible: true, secondsVisible: false },
    });
    const series = chart.addCandlestickSeries({
      upColor: "#34d399", downColor: "#f87171",
      borderVisible: false, wickUpColor: "#34d399", wickDownColor: "#f87171",
    });
    chartRef.current  = chart;
    seriesRef.current = series;
    return () => chart.remove();
  }, []);

  // fetch candles when asset or timeframe changes
  useEffect(() => {
    if (!asset) return;
    const { limit } = TIMEFRAMES.find(t => t.value === tf) || { limit: 100 };
    setLoading(true);
    setErr(null);
    fetchCandles(asset, source, tf, limit)
      .then(raw => {
        if (!Array.isArray(raw)) throw new Error(raw?.detail || "API error");
        const data = raw
          .filter(c => c.time && c.open && c.high && c.low && c.close)
          .map(c => ({ time: Math.floor(c.time), open: +c.open, high: +c.high, low: +c.low, close: +c.close }))
          .sort((a, b) => a.time - b.time);
        if (!data.length) throw new Error("No candles returned");
        seriesRef.current?.setData(data);
        chartRef.current?.timeScale().fitContent();
        setBars(data.length);
      })
      .catch(e => setErr(e.message || "Failed to load chart data"))
      .finally(() => setLoading(false));
  }, [asset, source, tf]);

  const tokenErr = err && (err.includes("DH-901") || err.includes("expired") || err.includes("invalid"));

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-neutral-800 flex justify-between items-center">
        <h2 className="text-xs font-bold text-neutral-300 uppercase tracking-wider flex items-center gap-2">
          <BarChart2 className="h-3.5 w-3.5 text-neutral-500" />
          {asset}
          {bars > 0 && <span className="text-neutral-600 font-normal">{bars} bars</span>}
        </h2>
        <div className="flex items-center gap-3">
          {/* Timeframe selector */}
          <div className="flex items-center gap-0.5 bg-neutral-800 rounded-lg p-0.5">
            {TIMEFRAMES.map(t => (
              <button
                key={t.value}
                onClick={() => setTf(t.value)}
                className={`px-2.5 py-1 rounded-md text-[11px] font-bold transition-all ${
                  tf === t.value
                    ? "bg-blue-600 text-white shadow"
                    : "text-neutral-500 hover:text-neutral-300"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          {loading && <span className="text-[10px] text-neutral-500 animate-pulse">loading…</span>}
          {err && !loading && (
            <span className="text-[10px] text-amber-500 max-w-xs truncate" title={err}>
              ⚠ {tokenErr ? "Token expired" : err}
            </span>
          )}
        </div>
      </div>
      <div ref={containerRef} style={{ height: 420 }}>
        {!loading && err && (
          <div className="h-full flex flex-col items-center justify-center gap-2 text-neutral-600">
            <BarChart2 className="h-8 w-8 opacity-30" />
            <p className="text-xs text-center px-6">
              {tokenErr
                ? <>Token expired. Regenerate at <span className="text-amber-500">dhanhq.co → My Account → API Access</span>, update <code>.env.prod</code>, restart api container.</>
                : err}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

// ── pair dropdown ─────────────────────────────────────────────────────────────

function PairDropdown({ pairs, selected, onSelect }) {
  const [open, setOpen]   = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef(null);
  const ref      = useRef(null);

  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 50); }, [open]);

  const filtered = query.trim()
    ? pairs.filter(p => p.symbol.toUpperCase().includes(query.toUpperCase()))
    : pairs;

  const handleCustom = (e) => {
    if (e.key === "Enter" && query.trim()) {
      onSelect({ symbol: query.trim().toUpperCase(), data_source: "" });
      setQuery(""); setOpen(false);
    }
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-neutral-700 bg-neutral-800 text-sm font-bold hover:border-neutral-600 transition-colors"
      >
        <span>{selected}</span>
        <ChevronDown className="h-3 w-3 text-neutral-500" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 w-56 bg-neutral-900 border border-neutral-700 rounded-xl shadow-xl z-50 overflow-hidden">
          <div className="p-2 border-b border-neutral-800">
            <div className="flex items-center gap-2 px-2 py-1 bg-neutral-800 rounded-lg">
              <Search className="h-3 w-3 text-neutral-500 flex-shrink-0" />
              <input
                ref={inputRef}
                value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={handleCustom}
                placeholder="Search or type symbol + Enter"
                className="bg-transparent text-xs text-neutral-200 outline-none w-full placeholder-neutral-600"
              />
            </div>
          </div>
          <div className="max-h-60 overflow-y-auto">
            {filtered.map(p => (
              <button
                key={p.symbol}
                onClick={() => { onSelect(p); setOpen(false); setQuery(""); }}
                className={`w-full text-left px-4 py-2 text-xs hover:bg-neutral-800 flex justify-between items-center transition-colors ${p.symbol === selected ? "text-blue-400 bg-neutral-800/50" : "text-neutral-300"}`}
              >
                <span className="font-bold">{p.symbol}</span>
                <span className="text-neutral-600 text-[10px]">{p.data_source || "NSE"}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── scanner card ─────────────────────────────────────────────────────────────

function ScannerCard({ symbol, result, isActive, onClick }) {
  const action    = result?.action;
  const conf      = result?.confidence || 0;
  const tradeable = result?.risk?.status === "APPROVED" || result?.risk?.status === "SCALED_DOWN";
  const scanning  = result === null;
  const price     = result?.price;

  return (
    <button
      onClick={onClick}
      className={`flex-shrink-0 rounded-xl border p-3 text-left transition-all cursor-pointer w-36
        ${isActive
          ? "border-blue-500/60 bg-blue-500/5 shadow-[0_0_12px_rgba(59,130,246,0.2)]"
          : "border-neutral-800 bg-neutral-900 hover:border-neutral-700"}`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs font-bold text-white">{symbol}</span>
        {scanning
          ? <RefreshCw className="h-3 w-3 text-neutral-600 animate-spin" />
          : <span className={`text-[10px] font-bold ${signalCls(action)}`}>{action || "—"}</span>
        }
      </div>
      {scanning ? (
        <div className="text-[10px] text-neutral-600 animate-pulse">scanning…</div>
      ) : (
        <>
          <div className="flex items-center gap-1 mb-1.5">
            <div className="flex-1 h-1 bg-neutral-800 rounded-full overflow-hidden">
              <div className={`h-full rounded-full transition-all ${
                action === "BUY" ? "bg-emerald-500" : action === "SELL" ? "bg-rose-500" : "bg-amber-500"
              }`} style={{ width: `${conf}%` }} />
            </div>
            <span className="text-[10px] text-neutral-500">{conf.toFixed(0)}%</span>
          </div>
          <div className="flex items-center justify-between">
            {price ? <span className="text-[10px] text-neutral-500">₹{price.toFixed(0)}</span> : <span />}
            {action && action !== "HOLD" && (
              <span className={`text-[9px] px-1 py-0.5 rounded border ${
                tradeable ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400" : "bg-neutral-800 border-neutral-700 text-neutral-500"
              }`}>
                {tradeable ? "✓ Capital OK" : "Low capital"}
              </span>
            )}
          </div>
        </>
      )}
    </button>
  );
}

// ── full agent card (detailed, from previous UI) ─────────────────────────────

function AgentCard({ agent }) {
  const meta = AGENT_META[agent.name] || { label: agent.name, icon: Cpu, color: "blue", indicators: [] };
  const clr  = COLOR[meta.color];
  const Icon = meta.icon;
  const dim  = agent.confidence < 55;

  return (
    <div className={`bg-neutral-900 border border-neutral-800 p-4 rounded-xl flex items-center gap-4 ${dim ? "opacity-60" : ""}`}>
      <div className={`h-11 w-11 rounded-lg ${clr.bg} ${clr.border} border flex items-center justify-center flex-shrink-0`}>
        <Icon className={`h-5 w-5 ${clr.icon}`} />
      </div>
      <div className="flex-grow min-w-0">
        <div className="flex justify-between items-center mb-1">
          <h3 className="text-xs font-bold text-neutral-200">{meta.label}</h3>
          <span className={`text-xs font-bold ${clr.conf}`}>{agent.confidence.toFixed(0)}%</span>
        </div>
        <p className="text-[10px] text-neutral-500 mb-2 truncate">{meta.indicators.join(", ")}</p>
        <div className="flex gap-1.5 flex-wrap">
          {agent.indicators && Object.entries(agent.indicators).slice(0, 2).map(([k, v]) => (
            <span key={k} className="text-[10px] px-1.5 py-0.5 bg-neutral-800 text-neutral-300 rounded capitalize">
              {k.replace(/_/g, " ")}: {typeof v === "number" ? v.toFixed(2) : String(v).slice(0, 12)}
            </span>
          ))}
          <span className={`text-[10px] px-1.5 py-0.5 rounded ${signalBadge(agent.decision)}`}>
            {agent.decision}
          </span>
          {dim && (
            <span className="text-[10px] text-amber-500 ml-auto">Below 55%</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ── infra + progress ──────────────────────────────────────────────────────────

function InfraRow({ icon: Icon, label, status, ok }) {
  return (
    <div className="flex items-center justify-between py-1">
      <div className="flex items-center gap-2">
        <Icon className="h-3.5 w-3.5 text-neutral-600" />
        <span className="text-[11px] text-neutral-400">{label}</span>
      </div>
      <span className={`text-[11px] font-bold ${ok ? "text-emerald-400" : "text-rose-400"}`}>{status}</span>
    </div>
  );
}

function Bar({ pct, color = "bg-blue-500" }) {
  return (
    <div className="h-1 w-full bg-neutral-800 rounded-full overflow-hidden">
      <div className={`h-full ${color} transition-all duration-500`} style={{ width: `${Math.min(pct, 100)}%` }} />
    </div>
  );
}

// ── main App ──────────────────────────────────────────────────────────────────

export default function App() {
  const [time, setTime]         = useState(new Date());
  const [pairs, setPairs]       = useState([]);
  const [selected, setSelected] = useState({ symbol: "ICICIBANK", data_source: "" });
  const [signal, setSignal]     = useState(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [portfolio, setPortfolio] = useState({ equity: 0, cash: 0, pnl: 0, currency: "INR", positions: {} });
  const [sys, setSys]           = useState({ ram_used_gb: 0, ram_total_gb: 8, ram_pct: 0, cpu_pct: 0, disk_pct: 0 });
  const [apiOk, setApiOk]       = useState(true);
  const [events, setEvents]     = useState([]);
  const [scanResults, setScanResults] = useState({});
  const scanActiveRef = useRef(false);
  const watchlistRef  = useRef(DEFAULT_WATCHLIST);

  // clock
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  // load pairs from API
  useEffect(() => {
    fetchPairSuggestions().then(d => {
      const all = d.pairs || [];
      setPairs(all);
      const wl = all.map(p => p.symbol).filter(Boolean);
      if (wl.length) watchlistRef.current = wl;
      if (all.length) setSelected({ symbol: all[0].symbol, data_source: all[0].data_source || "" });
    }).catch(() => {});
  }, []);

  // portfolio + system metrics every 10s
  useEffect(() => {
    const poll = async () => {
      try {
        const [port, s] = await Promise.all([fetchPortfolio(), fetchSystem()]);
        setPortfolio({ equity: port.equity, cash: port.cash, pnl: (port.daily_pnl_pct || 0) * 100, currency: port.currency || "INR", positions: port.positions || {} });
        setSys(s);
        setApiOk(true);
      } catch { setApiOk(false); }
    };
    poll();
    const t = setInterval(poll, 10_000);
    return () => clearInterval(t);
  }, []);

  // analyze selected asset on change + every 60s
  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      if (!selected.symbol) return;
      setAnalyzing(true);
      try {
        const r = await analyzeAsset(selected.symbol);
        if (!cancelled) {
          setSignal(r);
          setApiOk(true);
          addEvent("CONSENSUS", `${selected.symbol} → ${r.final_decision} (${(r.confidence || 0).toFixed(1)}%)`);
          setScanResults(prev => ({ ...prev, [selected.symbol]: { action: r.action, confidence: r.confidence, risk: r.risk_check, price: r.current_price } }));
        }
      } catch { if (!cancelled) setApiOk(false); }
      finally  { if (!cancelled) setAnalyzing(false); }
    };
    run();
    const t = setInterval(run, 60_000);
    return () => { cancelled = true; clearInterval(t); };
  }, [selected.symbol]);

  // background scanner: cycle through watchlist with 4s gap between assets
  const runScanner = useCallback(async () => {
    if (scanActiveRef.current) return;
    scanActiveRef.current = true;
    const wl = watchlistRef.current;
    setScanResults(prev => {
      const next = { ...prev };
      wl.forEach(s => { if (!(s in next)) next[s] = null; });
      return next;
    });
    for (const sym of wl) {
      if (!scanActiveRef.current) break;
      try {
        const r = await analyzeAsset(sym);
        setScanResults(prev => ({ ...prev, [sym]: { action: r.action, confidence: r.confidence, risk: r.risk_check, price: r.current_price } }));
        addEvent("SCAN", `${sym} → ${r.final_decision} ${r.action ? `(${(r.confidence||0).toFixed(0)}%)` : ""}`);
      } catch { /* skip */ }
      await new Promise(res => setTimeout(res, 4000));
    }
    scanActiveRef.current = false;
  }, []);

  useEffect(() => {
    runScanner();
    const t = setInterval(runScanner, 5 * 60_000);
    return () => { clearInterval(t); scanActiveRef.current = false; };
  }, [runScanner]);

  // event stream
  const addEvent = (tag, msg) =>
    setEvents(prev => [{ tag, msg, ts: new Date().toLocaleTimeString("en-IN") }, ...prev].slice(0, 60));

  useEffect(() => {
    const p = connectEvents(
      evt => addEvent(evt.event_type || "INFO", evt.data?.asset ? `${evt.data.asset}: ${evt.event_type}` : (evt.event_type || "event")),
      () => {}
    );
    return () => p.close();
  }, []);

  // derived
  const action     = signal?.action;
  const confidence = signal?.confidence || 0;
  const agents     = signal?.agents || [];
  const daVeto     = agents.find(a => a.name === "DevilsAdvocate");
  const activeAgents = agents.filter(a => a.name !== "DevilsAdvocate");
  const risk       = signal?.risk_check;
  const isTradeable = risk?.status === "APPROVED" || risk?.status === "SCALED_DOWN";
  const allocationPct = risk?.approved_size_pct ? (risk.approved_size_pct * 100).toFixed(1) : null;

  const tagColor = tag => {
    if (tag === "CONSENSUS" || tag === "FinalCall") return "text-purple-400";
    if (tag === "SCAN")               return "text-blue-400";
    if (tag.includes("ERR"))          return "text-rose-400";
    if (tag.includes("BAR") || tag.includes("Bar")) return "text-emerald-400";
    return "text-neutral-500";
  };

  const watchlist = watchlistRef.current;
  const sortedWL  = [...watchlist].sort((a, b) => {
    const ra = scanResults[a], rb = scanResults[b];
    const sa = ra?.action && ra.action !== "HOLD" ? (ra.confidence || 0) : 0;
    const sb = rb?.action && rb.action !== "HOLD" ? (rb.confidence || 0) : 0;
    return sb - sa;
  });
  const opportunities = sortedWL.filter(s => {
    const r = scanResults[s];
    return r && r.action && r.action !== "HOLD" && r.confidence >= 60;
  });

  const tradeableOpps = sortedWL
    .filter(s => {
      const r = scanResults[s];
      return r && r.action && r.action !== "HOLD" && r.confidence >= 60 &&
             (r.risk?.status === "APPROVED" || r.risk?.status === "SCALED_DOWN");
    })
    .map(s => ({ sym: s, result: scanResults[s] }));

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-50 font-mono flex flex-col">

      {/* ── Sticky Header ── */}
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-3 px-5 py-3 border-b border-neutral-800 bg-neutral-900/70 backdrop-blur sticky top-0 z-40">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 bg-blue-600/20 text-blue-500 rounded-lg flex items-center justify-center border border-blue-500/30 shadow-[0_0_12px_rgba(59,130,246,0.3)]">
            <Activity className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-base font-black tracking-tight text-white">TRADING_OS</h1>
            <p className="text-[10px] text-neutral-500">MULTI-AGENT CONSENSUS · NSE LIVE</p>
          </div>
        </div>

        <div className="flex items-center gap-4 text-xs flex-wrap">
          <PairDropdown pairs={pairs} selected={selected.symbol} onSelect={p => setSelected(p)} />

          <div className="flex flex-col items-end">
            <span className="text-[10px] text-neutral-500 uppercase">Cash</span>
            <span className="font-bold text-emerald-400">{fmtMoney(portfolio.cash, portfolio.currency)}</span>
          </div>
          <div className="flex flex-col items-end">
            <span className="text-[10px] text-neutral-500 uppercase">Equity</span>
            <span className="font-bold text-white">{fmtMoney(portfolio.equity, portfolio.currency)}</span>
          </div>
          <div className="flex flex-col items-end">
            <span className="text-[10px] text-neutral-500 uppercase">Daily P&L</span>
            <span className={`font-bold ${portfolio.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
              {portfolio.pnl >= 0 ? "+" : ""}{portfolio.pnl.toFixed(2)}%
            </span>
          </div>
          <div className="flex flex-col items-end">
            <span className="text-[10px] text-neutral-500 uppercase">Time IST</span>
            <span className="font-medium tabular-nums">
              {new Date(time.getTime() + 5.5 * 3600000).toISOString().slice(11, 19)}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${apiOk ? "bg-emerald-400 animate-pulse" : "bg-rose-400"}`} />
            <span className={`text-[11px] font-bold ${apiOk ? "text-emerald-400" : "text-rose-400"}`}>
              {apiOk ? "LIVE" : "ERR"}
            </span>
          </div>
        </div>
      </header>

      {/* ── Opportunity Scanner Strip ── */}
      <div className="px-5 py-3 border-b border-neutral-800 bg-neutral-900/30">
        <div className="flex items-center gap-2 mb-2">
          <RefreshCw className={`h-3 w-3 text-neutral-600 ${scanActiveRef.current ? "animate-spin" : ""}`} />
          <span className="text-[10px] text-neutral-500 uppercase font-bold">Live Scanner</span>
          {opportunities.length > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 border border-blue-500/30 text-blue-400 font-bold">
              {opportunities.length} signal{opportunities.length > 1 ? "s" : ""}
            </span>
          )}
          <span className="text-[10px] text-neutral-700 ml-auto">auto-refresh every 5 min · click to analyze</span>
        </div>
        <div className="flex gap-2 overflow-x-auto pb-1">
          {sortedWL.map(sym => (
            <ScannerCard
              key={sym}
              symbol={sym}
              result={scanResults[sym] ?? (sym in scanResults ? null : undefined)}
              isActive={selected.symbol === sym}
              onClick={() => setSelected({ symbol: sym, data_source: "" })}
            />
          ))}
        </div>
      </div>

      {/* ── Capital-Filtered Tradeable Opportunities ── */}
      {tradeableOpps.length > 0 && (
        <div className="px-5 pb-4">
          <div className="bg-neutral-900 border border-emerald-500/20 rounded-xl overflow-hidden">
            <div className="px-4 py-3 border-b border-neutral-800 flex items-center gap-2 flex-wrap">
              <CheckCircle className="h-3.5 w-3.5 text-emerald-400 flex-shrink-0" />
              <span className="text-xs font-bold text-emerald-400 uppercase tracking-wider">Tradeable Now</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 font-bold">
                {tradeableOpps.length} within capital
              </span>
              <span className="ml-auto text-[10px] text-neutral-600">
                Available cash: <span className="text-neutral-400">{fmtMoney(portfolio.cash, portfolio.currency)}</span>
              </span>
            </div>
            <div className="p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
              {tradeableOpps.map(({ sym, result }) => {
                const r = result.risk;
                const allPct = r?.approved_size_pct ? (r.approved_size_pct * 100).toFixed(1) : null;
                const reqCapital = allPct ? portfolio.equity * r.approved_size_pct : null;
                const isBuy = result.action === "BUY";
                return (
                  <div key={sym} className={`rounded-xl border p-3 flex flex-col gap-2 ${
                    isBuy ? "border-emerald-500/30 bg-emerald-500/5" : "border-rose-500/30 bg-rose-500/5"
                  }`}>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-black text-white">{sym}</span>
                      <span className={`text-[11px] font-bold px-2 py-0.5 rounded border ${signalBadge(result.action)}`}>
                        {result.action}
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-1 bg-neutral-800 rounded-full overflow-hidden">
                        <div className={`h-full rounded-full ${isBuy ? "bg-emerald-500" : "bg-rose-500"}`}
                          style={{ width: `${result.confidence}%` }} />
                      </div>
                      <span className="text-[10px] text-neutral-400 font-bold tabular-nums">{result.confidence.toFixed(0)}%</span>
                    </div>

                    <div className="grid grid-cols-3 gap-1.5 text-[10px]">
                      <div>
                        <div className="text-neutral-600 uppercase mb-0.5">Price</div>
                        <div className="text-neutral-200 font-bold">
                          {result.price ? `₹${result.price.toFixed(0)}` : "—"}
                        </div>
                      </div>
                      <div>
                        <div className="text-neutral-600 uppercase mb-0.5">Stop</div>
                        <div className="text-rose-400 font-bold">
                          {r?.stop_loss_price ? `₹${r.stop_loss_price.toFixed(0)}` : "—"}
                        </div>
                      </div>
                      <div>
                        <div className="text-neutral-600 uppercase mb-0.5">Target</div>
                        <div className="text-emerald-400 font-bold">
                          {r?.take_profit_price ? `₹${r.take_profit_price.toFixed(0)}` : "—"}
                        </div>
                      </div>
                    </div>

                    {reqCapital !== null && (
                      <div className="text-[10px] text-neutral-500 border-t border-neutral-800/60 pt-1.5">
                        Capital: <span className="text-white font-bold">{fmtMoney(reqCapital, portfolio.currency)}</span>
                        {allPct && <span className="text-neutral-600 ml-1">({allPct}% Kelly)</span>}
                      </div>
                    )}

                    <button
                      onClick={() => setSelected({ symbol: sym, data_source: "" })}
                      className={`mt-0.5 w-full py-1.5 rounded-lg text-[11px] font-bold transition-all ${
                        isBuy
                          ? "bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-400 border border-emerald-500/30"
                          : "bg-rose-500/20 hover:bg-rose-500/30 text-rose-400 border border-rose-500/30"
                      }`}
                    >
                      {isBuy ? "▲" : "▼"} Analyze {sym}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* ── Candlestick Chart (full width) ── */}
      <div className="px-5 pt-5 pb-4">
        <PriceChart asset={selected.symbol} source={selected.data_source} />
      </div>

      {/* ── 3-Column Analysis Grid ── */}
      <div className="px-5 pb-5 grid grid-cols-1 lg:grid-cols-12 gap-5">

        {/* ── Col 1 (4): Signal + Devil's Advocate ── */}
        <div className="lg:col-span-4 flex flex-col gap-5">

          {/* Meta-Agent Signal Card */}
          <div className="bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden shadow-xl">
            <div className="p-5 border-b border-neutral-800 bg-neutral-900/50 flex justify-between items-center">
              <h2 className="text-sm font-bold text-neutral-300 flex items-center gap-2 uppercase tracking-wider">
                <Brain className="h-4 w-4 text-purple-400" />
                Meta-Agent Consensus
              </h2>
              {analyzing && <span className="text-[10px] text-neutral-500 animate-pulse">analyzing…</span>}
            </div>
            <div className="p-6 flex flex-col items-center justify-center">
              {signal ? (
                <>
                  <div className={`text-5xl font-black mb-2 ${signalCls(action)}`}>
                    {action || "HOLD"}
                  </div>
                  <p className="text-sm text-neutral-400 mb-5">{signal.final_decision || "—"}</p>

                  <div className="w-full space-y-4">
                    <div className="flex justify-between items-end">
                      <span className="text-xs text-neutral-500 uppercase">Conviction Score</span>
                      <span className="text-lg font-bold text-white">{confidence.toFixed(1)}%</span>
                    </div>
                    <div className="h-2 w-full bg-neutral-800 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-gradient-to-r from-emerald-500 to-emerald-400 transition-all duration-700 shadow-[0_0_10px_rgba(52,211,153,0.5)]"
                        style={{ width: `${confidence}%` }}
                      />
                    </div>

                    <div className="grid grid-cols-2 gap-4 pt-4 border-t border-neutral-800">
                      <div>
                        <div className="text-[10px] text-neutral-500 uppercase mb-1">Asset</div>
                        <div className="text-sm font-semibold text-neutral-200">{signal.asset || selected.symbol}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-neutral-500 uppercase mb-1">Equity</div>
                        <div className="text-sm font-semibold text-blue-400">{fmtMoney(portfolio.equity, portfolio.currency)}</div>
                      </div>
                      {allocationPct && (
                        <>
                          <div>
                            <div className="text-[10px] text-neutral-500 uppercase mb-1">Sizing</div>
                            <div className="text-sm font-semibold text-neutral-200">Half-Kelly</div>
                          </div>
                          <div>
                            <div className="text-[10px] text-neutral-500 uppercase mb-1">Allocation</div>
                            <div className="text-sm font-semibold text-blue-400">{allocationPct}%</div>
                          </div>
                        </>
                      )}
                    </div>

                    {/* Risk summary */}
                    {risk && (
                      <div className={`rounded-lg border p-3 text-[11px] mt-1 ${
                        isTradeable ? "border-emerald-500/20 bg-emerald-500/5" : "border-neutral-700 bg-neutral-800/30"
                      }`}>
                        <div className="flex items-center gap-1.5 mb-1.5">
                          {isTradeable
                            ? <CheckCircle className="h-3.5 w-3.5 text-emerald-400" />
                            : <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />}
                          <span className={`font-bold ${isTradeable ? "text-emerald-400" : "text-amber-400"}`}>
                            {isTradeable ? "Capital sufficient" : risk.rejections?.[0] || "Not tradeable"}
                          </span>
                        </div>
                        {isTradeable && (
                          <div className="grid grid-cols-3 gap-2 text-neutral-400 mt-2">
                            <div>
                              <div className="text-[9px] text-neutral-600 mb-0.5">SIZE</div>
                              <div className="text-white font-bold">{allocationPct}%</div>
                            </div>
                            <div>
                              <div className="text-[9px] text-neutral-600 mb-0.5">STOP</div>
                              <div className="text-rose-400 font-bold">₹{(risk.stop_loss_price || 0).toFixed(0)}</div>
                            </div>
                            <div>
                              <div className="text-[9px] text-neutral-600 mb-0.5">TARGET</div>
                              <div className="text-emerald-400 font-bold">₹{(risk.take_profit_price || 0).toFixed(0)}</div>
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <div className="py-6 text-center text-neutral-600">
                  <Brain className="h-8 w-8 mx-auto mb-2 opacity-30" />
                  <p className="text-xs">Waiting for analysis…</p>
                </div>
              )}
            </div>
          </div>

          {/* Devil's Advocate */}
          <div className="bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden shadow-xl">
            <div className="p-4 border-b border-neutral-800 bg-neutral-900/50 flex justify-between items-center">
              <h2 className="text-sm font-bold text-neutral-300 flex items-center gap-2 uppercase tracking-wider">
                <ShieldAlert className="h-4 w-4 text-rose-500" />
                Devil's Advocate
              </h2>
              <span className={`text-xs px-2 py-1 rounded border ${
                daVeto?.decision === "SELL"
                  ? "bg-rose-500/10 text-rose-400 border-rose-500/20"
                  : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
              }`}>
                {daVeto?.decision === "SELL" ? "VETO" : "PASS"}
              </span>
            </div>
            <div className="p-4">
              <div className="grid grid-cols-2 gap-2 text-xs mb-3">
                {activeAgents.map(a => {
                  const warn = (a.warnings || []).length > 0;
                  return (
                    <div key={a.name} className="flex justify-between p-2 bg-neutral-800/50 rounded">
                      <span className="text-neutral-500">{(AGENT_META[a.name]?.label || a.name).split(" ")[0]}</span>
                      <span className={warn ? "text-amber-400 font-bold" : `${signalCls(a.decision)} font-bold`}>
                        {warn ? "WARN" : (a.decision || "—")}
                      </span>
                    </div>
                  );
                })}
              </div>
              <div className="text-[10px] text-neutral-500 border-t border-neutral-800 pt-3">
                {daVeto
                  ? <><span className="text-neutral-400">DA reasoning: </span><span className="text-white">{daVeto.reasoning?.slice(0, 120)}</span></>
                  : <span>Veto threshold: ≥85% SELL. Current conviction: <span className="text-white font-bold">{confidence.toFixed(1)}%</span></span>
                }
              </div>
            </div>
          </div>
        </div>

        {/* ── Col 2 (5): Detailed Agent Cards ── */}
        <div className="lg:col-span-5 flex flex-col gap-4">
          <div className="flex items-center gap-2">
            <Cpu className="h-5 w-5 text-neutral-400" />
            <h2 className="text-base font-bold text-white tracking-tight">
              Active Agents ({activeAgents.length}/4)
            </h2>
          </div>

          {activeAgents.length > 0
            ? activeAgents.map(a => <AgentCard key={a.name} agent={a} />)
            : Object.keys(AGENT_META).map(name => (
                <div key={name} className="bg-neutral-900 border border-neutral-800 p-4 rounded-xl opacity-40">
                  <div className="flex items-center gap-3">
                    {React.createElement(AGENT_META[name].icon, { className: "h-5 w-5 text-neutral-500" })}
                    <span className="text-sm text-neutral-500">{AGENT_META[name].label}</span>
                    <span className="text-[10px] text-neutral-600 ml-auto">waiting…</span>
                  </div>
                </div>
              ))
          }
        </div>

        {/* ── Col 3 (3): Infrastructure + Terminal ── */}
        <div className="lg:col-span-3 flex flex-col gap-5">

          {/* Infrastructure */}
          <div className="bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden">
            <div className="p-4 border-b border-neutral-800 flex justify-between items-center">
              <h2 className="text-sm font-bold text-neutral-300 uppercase">Infrastructure</h2>
              <Server className="h-4 w-4 text-neutral-500" />
            </div>
            <div className="p-4 flex flex-col gap-1">
              <InfraRow icon={Database} label="PostgreSQL 15"     status="OK"     ok={true} />
              <InfraRow icon={Zap}      label="Redis Cache"       status="OK"     ok={true} />
              <InfraRow icon={Globe}    label="Cloudflare Tunnel" status="SECURE" ok={true} />
              <InfraRow icon={Lock}     label="API Gateway"       status={apiOk ? "OK" : "DOWN"} ok={apiOk} />

              <div className="mt-3 pt-3 border-t border-neutral-800 space-y-2">
                <div className="flex justify-between text-[10px]">
                  <span className="text-neutral-600 uppercase">MacBook M1 RAM</span>
                  <span className="text-neutral-400">{sys.ram_used_gb?.toFixed(1)} / {sys.ram_total_gb?.toFixed(1)} GB</span>
                </div>
                <Bar pct={sys.ram_pct} color={sys.ram_pct > 85 ? "bg-rose-500" : "bg-blue-500"} />
                <div className="flex justify-between text-[10px]">
                  <span className="text-neutral-600 uppercase">CPU</span>
                  <span className="text-neutral-400">{sys.cpu_pct?.toFixed(0) || 0}%</span>
                </div>
                <Bar pct={sys.cpu_pct || 0} color={sys.cpu_pct > 80 ? "bg-amber-500" : "bg-emerald-500"} />
                <div className="flex justify-between text-[10px]">
                  <span className="text-neutral-600 uppercase">Disk</span>
                  <span className="text-neutral-400">{sys.disk_pct?.toFixed(0) || 0}%</span>
                </div>
                <Bar pct={sys.disk_pct || 0} color="bg-neutral-500" />
              </div>
            </div>
          </div>

          {/* Live Terminal */}
          <div className="bg-neutral-950 border border-neutral-800 rounded-xl flex flex-col overflow-hidden flex-grow" style={{ minHeight: 200 }}>
            <div className="p-3 border-b border-neutral-800 flex items-center gap-2 bg-neutral-900">
              <Terminal className="h-4 w-4 text-neutral-500" />
              <h2 className="text-xs font-bold text-neutral-400 uppercase">Live Output</h2>
            </div>
            <div className="p-3 text-[10px] text-neutral-500 font-mono overflow-y-auto space-y-1 flex-grow">
              {events.length === 0
                ? <p className="text-neutral-700">Waiting for events…</p>
                : events.slice(0, 40).map((e, i) => (
                  <p key={i}>
                    <span className="text-neutral-700">{e.ts} </span>
                    <span className={tagColor(e.tag)}>[{e.tag.toUpperCase().slice(0, 12)}]</span>{" "}
                    <span className="text-neutral-400">{e.msg}</span>
                  </p>
                ))
              }
            </div>
          </div>
        </div>
      </div>

      {/* ── Open Positions (full width footer) ── */}
      <div className="border-t border-neutral-800 bg-neutral-900/40">
        <div className="px-5 py-3 flex justify-between items-center">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-3.5 w-3.5 text-neutral-500" />
            <span className="text-[11px] font-bold text-neutral-400 uppercase">Open Positions</span>
            <span className="text-neutral-700 text-[10px]">{Object.keys(portfolio.positions).length} active</span>
          </div>
          <div className="flex items-center gap-4 text-[10px] text-neutral-500">
            <span>Equity: <span className="text-white font-bold">{fmtMoney(portfolio.equity, portfolio.currency)}</span></span>
            <span>Cash: <span className="text-emerald-400 font-bold">{fmtMoney(portfolio.cash, portfolio.currency)}</span></span>
            <span className={portfolio.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}>
              Daily P&L: {portfolio.pnl >= 0 ? "+" : ""}{portfolio.pnl.toFixed(2)}%
            </span>
          </div>
        </div>
        {Object.keys(portfolio.positions).length === 0 ? (
          <div className="px-5 pb-4 text-[11px] text-neutral-700 flex items-center gap-2">
            <Minus className="h-3 w-3" />
            No open positions — executed signals will appear here
          </div>
        ) : (
          <div className="overflow-x-auto px-5 pb-4">
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="text-neutral-600 border-b border-neutral-800">
                  <th className="text-left py-1.5 pr-4">Symbol</th>
                  <th className="text-right pr-4">Qty</th>
                  <th className="text-right pr-4">Avg Price</th>
                  <th className="text-right pr-4">Value</th>
                  <th className="text-right">Side</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(portfolio.positions).map(([sym, pos]) => (
                  <tr
                    key={sym}
                    className="border-b border-neutral-800/40 hover:bg-neutral-800/20 cursor-pointer"
                    onClick={() => setSelected({ symbol: sym, data_source: "" })}
                  >
                    <td className="py-2 pr-4 text-white font-bold">{sym}</td>
                    <td className="pr-4 text-right text-neutral-300">{pos.qty ?? "—"}</td>
                    <td className="pr-4 text-right text-neutral-400">
                      {pos.avg_price ? fmtMoney(pos.avg_price, portfolio.currency) : "—"}
                    </td>
                    <td className="pr-4 text-right text-white">
                      {pos.value ? fmtMoney(pos.value, portfolio.currency) : "—"}
                    </td>
                    <td className="text-right">
                      <span className={`px-1.5 py-0.5 rounded text-[9px] ${
                        (pos.side || "buy") === "buy"
                          ? "bg-emerald-500/20 text-emerald-400"
                          : "bg-rose-500/20 text-rose-400"
                      }`}>
                        {(pos.side || "BUY").toUpperCase()}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
