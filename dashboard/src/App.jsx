import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Activity, Brain, ShieldAlert, TrendingUp, TrendingDown, Minus,
  Server, Terminal, Cpu, Globe, Database, Lock, Zap, ChevronDown,
  RefreshCw, AlertTriangle, CheckCircle, Search,
} from 'lucide-react';
import { fetchPortfolio, fetchPairSuggestions, analyzeAsset, fetchSystem } from './api';
import { connectEvents } from './eventsPoller';

// ── constants ─────────────────────────────────────────────────────────────────

const DEFAULT_WATCHLIST = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN"];

// Map internal symbols → TradingView symbol format
function toTVSymbol(symbol) {
  if (!symbol) return "NSE:NIFTY50";
  const s = symbol.toUpperCase();
  if (s.endsWith("USDT"))  return `BINANCE:${s}`;
  if (s.endsWith("BUSD"))  return `BINANCE:${s}`;
  if (s === "BTCUSD")      return "COINBASE:BTCUSD";
  if (s.length === 6 && /^(EUR|GBP|AUD|NZD|USD|JPY|CAD|CHF)/.test(s)) return `FX_IDC:${s}`;
  if (["NIFTY","NIFTY50"].includes(s)) return "NSE:NIFTY50";
  if (s === "BANKNIFTY")   return "NSE:BANKNIFTY";
  if (["AAPL","MSFT","NVDA","TSLA","AMZN","GOOGL","META"].includes(s)) return `NASDAQ:${s}`;
  if (["SPY","QQQ","IWM"].includes(s)) return `AMEX:${s}`;
  return `NSE:${s}`;
}

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

function signalBg(action) {
  if (action === "BUY")  return "bg-emerald-500/10 border-emerald-500/30 text-emerald-400";
  if (action === "SELL") return "bg-rose-500/10 border-rose-500/30 text-rose-400";
  return "bg-amber-500/10 border-amber-500/30 text-amber-400";
}

function SignalIcon({ action, size = 14 }) {
  if (action === "BUY")  return <TrendingUp  style={{ width: size, height: size }} />;
  if (action === "SELL") return <TrendingDown style={{ width: size, height: size }} />;
  return <Minus style={{ width: size, height: size }} />;
}

// ── TradingView chart ─────────────────────────────────────────────────────────

let _tvScriptPromise = null;
function loadTVScript() {
  if (_tvScriptPromise) return _tvScriptPromise;
  _tvScriptPromise = new Promise((resolve, reject) => {
    if (window.TradingView) { resolve(); return; }
    const s = document.createElement("script");
    s.src = "https://s3.tradingview.com/tv.js";
    s.async = true;
    s.onload = resolve;
    s.onerror = reject;
    document.head.appendChild(s);
  });
  return _tvScriptPromise;
}

function TradingViewChart({ symbol }) {
  const containerId = "tv_main_chart";
  const widgetRef = useRef(null);

  useEffect(() => {
    if (!symbol) return;
    loadTVScript().then(() => {
      if (widgetRef.current) { try { widgetRef.current.remove(); } catch (_) {} }
      const el = document.getElementById(containerId);
      if (!el) return;
      el.innerHTML = "";
      widgetRef.current = new window.TradingView.widget({
        autosize: true,
        symbol: toTVSymbol(symbol),
        interval: "60",
        timezone: "Asia/Kolkata",
        theme: "dark",
        style: "1",
        locale: "en",
        toolbar_bg: "#171717",
        enable_publishing: false,
        hide_top_toolbar: false,
        hide_legend: false,
        withdateranges: true,
        allow_symbol_change: true,
        save_image: true,
        studies: [
          "RSI@tv-basicstudies",
          "MACD@tv-basicstudies",
          "Volume@tv-basicstudies",
          "BB@tv-basicstudies",
        ],
        show_popup_button: true,
        popup_width: "1200",
        popup_height: "700",
        container_id: containerId,
        backgroundColor: "rgba(10,10,10,1)",
        gridColor: "rgba(23,23,23,1)",
        details: true,
        hotlist: true,
        calendar: false,
      });
    }).catch(() => {});
    return () => {
      if (widgetRef.current) { try { widgetRef.current.remove(); } catch (_) {} widgetRef.current = null; }
    };
  }, [symbol]);

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden flex flex-col" style={{ height: 540 }}>
      <div id={containerId} style={{ flex: 1, minHeight: 0 }} />
    </div>
  );
}

// ── pair dropdown with search ─────────────────────────────────────────────────

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

  const submit = () => {
    const sym = query.trim().toUpperCase();
    if (!sym) return;
    onSelect({ symbol: sym, data_source: "" });
    setQuery(""); setOpen(false);
  };

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 bg-neutral-800 hover:bg-neutral-700 border border-neutral-700 rounded-lg px-3 py-1.5 text-sm font-mono transition-colors">
        <Search className="h-3 w-3 text-neutral-500" />
        <span className="text-white font-bold">{selected || "Select"}</span>
        <ChevronDown className="h-3 w-3 text-neutral-400" />
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-56 bg-neutral-900 border border-neutral-700 rounded-xl shadow-2xl z-50 flex flex-col overflow-hidden">
          <div className="p-2 border-b border-neutral-800">
            <input ref={inputRef} value={query} onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === "Enter" && submit()}
              placeholder="Type symbol + Enter…"
              className="w-full bg-neutral-800 text-xs text-white placeholder-neutral-600 rounded-lg px-2.5 py-1.5 outline-none border border-neutral-700 focus:border-blue-500 transition-colors" />
          </div>
          <div className="max-h-60 overflow-y-auto">
            {filtered.map(p => (
              <button key={`${p.symbol}-${p.data_source || "x"}`}
                onClick={() => { onSelect(p); setQuery(""); setOpen(false); }}
                className={`w-full text-left px-3 py-2 text-xs font-mono hover:bg-neutral-800 flex justify-between items-center ${p.symbol === selected ? "text-blue-400" : "text-neutral-300"}`}>
                <span>{p.symbol}</span>
                {p.data_source && <span className="text-[9px] text-neutral-600 uppercase">{p.data_source}</span>}
              </button>
            ))}
            {query.trim() && !filtered.find(p => p.symbol.toUpperCase() === query.toUpperCase()) && (
              <div className="px-3 py-2 text-[10px] text-neutral-500 border-t border-neutral-800">
                Press Enter → analyze <span className="text-white font-bold">{query.toUpperCase()}</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── opportunity scanner strip ─────────────────────────────────────────────────

function ScannerCard({ symbol, result, isActive, onClick }) {
  const action    = result?.action;
  const conf      = result?.confidence || 0;
  const tradeable = result?.risk?.status === "APPROVED" || result?.risk?.status === "SCALED_DOWN";
  const scanning  = result === null;
  const price     = result?.price;

  return (
    <button onClick={onClick}
      className={`flex-shrink-0 rounded-xl border p-3 text-left transition-all cursor-pointer w-36
        ${isActive
          ? "border-blue-500/60 bg-blue-500/5 shadow-[0_0_12px_rgba(59,130,246,0.2)]"
          : "border-neutral-800 bg-neutral-900 hover:border-neutral-700"}`}>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs font-bold text-white">{symbol}</span>
        {scanning
          ? <RefreshCw className="h-3 w-3 text-neutral-600 animate-spin" />
          : <span className={`text-[10px] font-bold ${signalCls(action)}`}>
              {action || "—"}
            </span>
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

// ── compact agent votes panel ─────────────────────────────────────────────────

const AGENT_META = {
  Technical: { label: "Technical", color: "blue" },
  Sentiment: { label: "Sentiment", color: "purple" },
  Quant:     { label: "Quant",     color: "orange" },
  OrderFlow: { label: "Structure", color: "indigo" },
};

const CONF_COLOR = {
  blue:   "text-blue-400 bg-blue-500/10",
  purple: "text-purple-400 bg-purple-500/10",
  orange: "text-orange-400 bg-orange-500/10",
  indigo: "text-indigo-400 bg-indigo-500/10",
};

function AgentVotes({ agents, daVeto }) {
  const active = (agents || []).filter(a => a.name !== "DevilsAdvocate");
  return (
    <div className="grid grid-cols-2 gap-2">
      {active.map(a => {
        const meta = AGENT_META[a.name] || { label: a.name, color: "blue" };
        const clr  = CONF_COLOR[meta.color];
        const warn = (a.warnings || []).length > 0;
        return (
          <div key={a.name} className="bg-neutral-800/60 rounded-lg p-2.5 border border-neutral-700/50">
            <div className="flex justify-between items-center mb-1">
              <span className="text-[10px] text-neutral-400 font-bold">{meta.label}</span>
              <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${signalBg(a.decision)}`}>
                {warn ? "WARN" : (a.decision || "—")}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="flex-1 h-0.5 bg-neutral-700 rounded-full overflow-hidden">
                <div className={`h-full ${a.decision === "BUY" ? "bg-emerald-500" : a.decision === "SELL" ? "bg-rose-500" : "bg-amber-500"}`}
                  style={{ width: `${a.confidence || 0}%` }} />
              </div>
              <span className={`text-[10px] font-bold ${clr} px-1 rounded`}>{(a.confidence || 0).toFixed(0)}%</span>
            </div>
          </div>
        );
      })}
      {daVeto && (
        <div className={`col-span-2 rounded-lg p-2.5 border text-[10px] ${
          daVeto.decision === "SELL"
            ? "border-rose-500/30 bg-rose-500/5 text-rose-400"
            : "border-emerald-500/20 bg-emerald-500/5 text-emerald-400"
        }`}>
          <div className="flex items-center gap-1.5 mb-0.5">
            <ShieldAlert className="h-3 w-3" />
            <span className="font-bold">Devil's Advocate: {daVeto.decision === "SELL" ? "VETO" : "PASS"}</span>
          </div>
          <p className="text-neutral-400 line-clamp-2">{daVeto.reasoning?.slice(0, 100)}</p>
        </div>
      )}
    </div>
  );
}

// ── infra row ─────────────────────────────────────────────────────────────────

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
  const [scanResults, setScanResults] = useState({});  // symbol → result
  const [scanQueue, setScanQueue]     = useState([]);
  const scanActiveRef = useRef(false);
  const watchlistRef  = useRef(DEFAULT_WATCHLIST);

  // clock
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  // load pairs
  useEffect(() => {
    fetchPairSuggestions().then(d => {
      const all = d.pairs || [];
      setPairs(all);
      const nse = all.filter(p => !p.data_source || p.data_source === "");
      const wl = nse.map(p => p.symbol).filter(Boolean);
      if (wl.length) watchlistRef.current = wl;
    }).catch(() => {});
  }, []);

  // portfolio poll every 10s
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

  // analyze selected asset, re-run every 60s
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
          addEvent("CONSENSUS", `${selected.symbol} → ${r.final_decision} (${(r.confidence||0).toFixed(1)}%)`);
          // update scanner result for selected asset
          setScanResults(prev => ({ ...prev, [selected.symbol]: { action: r.action, confidence: r.confidence, final_decision: r.final_decision, risk: r.risk_check, price: r.current_price } }));
        }
      } catch { if (!cancelled) setApiOk(false); }
      finally { if (!cancelled) setAnalyzing(false); }
    };
    run();
    const t = setInterval(run, 60_000);
    return () => { cancelled = true; clearInterval(t); };
  }, [selected.symbol]);

  // background scanner: cycle through full watchlist, 1 asset at a time
  const runScanner = useCallback(async () => {
    if (scanActiveRef.current) return;
    scanActiveRef.current = true;
    const wl = watchlistRef.current;
    // init null placeholders for unseen assets
    setScanResults(prev => {
      const next = { ...prev };
      wl.forEach(s => { if (!(s in next)) next[s] = null; });
      return next;
    });
    for (const sym of wl) {
      if (!scanActiveRef.current) break;
      try {
        const r = await analyzeAsset(sym);
        setScanResults(prev => ({ ...prev, [sym]: { action: r.action, confidence: r.confidence, final_decision: r.final_decision, risk: r.risk_check, price: r.current_price } }));
        addEvent("SCAN", `${sym} → ${r.final_decision} ${r.action ? "(" + (r.confidence||0).toFixed(0) + "%)" : ""}`);
      } catch { /* skip */ }
      // brief pause between stocks to avoid hammering Dhan
      await new Promise(res => setTimeout(res, 4000));
    }
    scanActiveRef.current = false;
  }, []);

  // run scanner on mount and every 5 min
  useEffect(() => {
    runScanner();
    const t = setInterval(runScanner, 5 * 60_000);
    return () => { clearInterval(t); scanActiveRef.current = false; };
  }, [runScanner]);

  // event stream
  const addEvent = (tag, msg) => setEvents(prev => [{ tag, msg, ts: new Date().toLocaleTimeString() }, ...prev].slice(0, 60));

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
  const risk       = signal?.risk_check;
  const isTradeable = risk?.status === "APPROVED" || risk?.status === "SCALED_DOWN";

  const tagColor = tag => {
    if (tag === "CONSENSUS" || tag === "FinalCall") return "text-purple-400";
    if (tag === "SCAN")      return "text-blue-400";
    if (tag.includes("ERR")) return "text-rose-400";
    if (tag.includes("BAR") || tag.includes("Bar")) return "text-emerald-400";
    return "text-neutral-500";
  };

  // sort watchlist: BUY/SELL first, highest confidence
  const watchlist = watchlistRef.current;
  const sortedWL  = [...watchlist].sort((a, b) => {
    const ra = scanResults[a], rb = scanResults[b];
    const scoreA = ra?.action && ra.action !== "HOLD" ? (ra.confidence || 0) : 0;
    const scoreB = rb?.action && rb.action !== "HOLD" ? (rb.confidence || 0) : 0;
    return scoreB - scoreA;
  });

  const opportunities = sortedWL.filter(s => {
    const r = scanResults[s];
    return r && r.action && r.action !== "HOLD" && r.confidence >= 60;
  });

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-50 font-mono flex flex-col">

      {/* ── Header ── */}
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-3 px-5 py-3 border-b border-neutral-800 bg-neutral-900/60 backdrop-blur sticky top-0 z-40">
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
            <span className="font-medium">{new Date(time.getTime() + 5.5*3600000).toISOString().slice(11,19)}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${apiOk ? "bg-emerald-400 animate-pulse" : "bg-rose-400"}`} />
            <span className={`text-[11px] font-bold ${apiOk ? "text-emerald-400" : "text-rose-400"}`}>
              {apiOk ? "LIVE" : "ERR"}
            </span>
          </div>
        </div>
      </header>

      {/* ── Opportunity Scanner strip ── */}
      <div className="px-5 py-3 border-b border-neutral-800 bg-neutral-900/30">
        <div className="flex items-center gap-2 mb-2">
          <RefreshCw className={`h-3 w-3 text-neutral-600 ${scanActiveRef.current ? "animate-spin" : ""}`} />
          <span className="text-[10px] text-neutral-500 uppercase font-bold">Live Scanner</span>
          {opportunities.length > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 border border-blue-500/30 text-blue-400 font-bold">
              {opportunities.length} opportunity{opportunities.length > 1 ? "s" : ""}
            </span>
          )}
          <span className="text-[10px] text-neutral-600 ml-auto">auto-refresh every 5 min · click to deep-dive</span>
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

      {/* ── Main body ── */}
      <div className="flex flex-col lg:flex-row gap-0 flex-1 min-h-0">

        {/* ── LEFT: TradingView chart ── */}
        <div className="lg:w-[62%] p-5 border-r border-neutral-800">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-white">{selected.symbol}</span>
              <span className="text-[10px] text-neutral-500">{toTVSymbol(selected.symbol)}</span>
            </div>
            {analyzing && <span className="text-[10px] text-neutral-500 animate-pulse flex items-center gap-1"><RefreshCw className="h-3 w-3 animate-spin" /> analyzing…</span>}
          </div>
          <TradingViewChart symbol={selected.symbol} />
        </div>

        {/* ── RIGHT: signal + agents + infra ── */}
        <div className="lg:w-[38%] flex flex-col overflow-y-auto" style={{ maxHeight: "calc(100vh - 130px)" }}>

          {/* Consensus */}
          <div className="p-5 border-b border-neutral-800">
            <div className="flex items-center gap-2 mb-4">
              <Brain className="h-4 w-4 text-purple-400" />
              <span className="text-xs font-bold text-neutral-300 uppercase">Meta-Agent Consensus</span>
            </div>

            {signal ? (
              <div className="space-y-4">
                {/* Big signal */}
                <div className={`rounded-xl border p-4 flex items-center justify-between ${
                  action === "BUY"  ? "border-emerald-500/30 bg-emerald-500/5" :
                  action === "SELL" ? "border-rose-500/30 bg-rose-500/5" :
                  "border-amber-500/20 bg-amber-500/5"
                }`}>
                  <div>
                    <div className={`text-4xl font-black ${signalCls(action)}`}>
                      {action || "HOLD"}
                    </div>
                    <div className="text-[10px] text-neutral-500 mt-1">{signal.final_decision}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-2xl font-black text-white">{confidence.toFixed(0)}<span className="text-sm text-neutral-500">%</span></div>
                    <div className="text-[10px] text-neutral-500">conviction</div>
                  </div>
                </div>

                {/* Capital / risk summary */}
                {risk && (
                  <div className={`rounded-lg border p-3 text-[11px] ${
                    isTradeable ? "border-emerald-500/20 bg-emerald-500/5" : "border-neutral-700 bg-neutral-800/30"
                  }`}>
                    <div className="flex items-center gap-1.5 mb-1.5">
                      {isTradeable
                        ? <CheckCircle className="h-3.5 w-3.5 text-emerald-400" />
                        : <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
                      }
                      <span className={`font-bold ${isTradeable ? "text-emerald-400" : "text-amber-400"}`}>
                        {isTradeable ? "Capital sufficient — tradeable" : risk.rejections?.[0] || "Not tradeable"}
                      </span>
                    </div>
                    {isTradeable && (
                      <div className="grid grid-cols-3 gap-2 text-neutral-400 mt-2">
                        <div>
                          <div className="text-[9px] text-neutral-600 mb-0.5">SIZE</div>
                          <div className="text-white font-bold">{((risk.approved_size_pct || 0)*100).toFixed(1)}%</div>
                        </div>
                        <div>
                          <div className="text-[9px] text-neutral-600 mb-0.5">STOP LOSS</div>
                          <div className="text-rose-400 font-bold">₹{(risk.stop_loss_price || risk.sl_price || 0).toFixed(0)}</div>
                        </div>
                        <div>
                          <div className="text-[9px] text-neutral-600 mb-0.5">TARGET</div>
                          <div className="text-emerald-400 font-bold">₹{(risk.take_profit_price || risk.tp_price || 0).toFixed(0)}</div>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Reason */}
                {signal.reason && (
                  <p className="text-[10px] text-neutral-500 leading-relaxed line-clamp-3">{signal.reason}</p>
                )}

                {/* Agent votes */}
                <AgentVotes agents={agents} daVeto={daVeto} />
              </div>
            ) : (
              <div className="text-center py-8 text-neutral-600 text-xs">
                <Brain className="h-8 w-8 mx-auto mb-2 opacity-30" />
                <p>Click any scanner card to analyze</p>
              </div>
            )}
          </div>

          {/* Infra + system */}
          <div className="p-5 border-b border-neutral-800">
            <div className="flex items-center gap-2 mb-3">
              <Server className="h-3.5 w-3.5 text-neutral-500" />
              <span className="text-[11px] font-bold text-neutral-400 uppercase">Infrastructure</span>
            </div>
            <div className="space-y-0.5">
              <InfraRow icon={Database} label="PostgreSQL" status="OK" ok={true} />
              <InfraRow icon={Zap}      label="Redis"     status="OK" ok={true} />
              <InfraRow icon={Globe}    label="Cloudflare Tunnel" status="SECURE" ok={true} />
              <InfraRow icon={Lock}     label="API Gateway" status={apiOk ? "OK" : "DOWN"} ok={apiOk} />
            </div>
            <div className="mt-3 space-y-1.5">
              <div className="flex justify-between text-[10px]">
                <span className="text-neutral-600">RAM</span>
                <span className="text-neutral-400">{sys.ram_used_gb?.toFixed(1)}/{sys.ram_total_gb?.toFixed(1)} GB</span>
              </div>
              <Bar pct={sys.ram_pct} color={sys.ram_pct > 85 ? "bg-rose-500" : "bg-blue-500"} />
              <div className="flex justify-between text-[10px]">
                <span className="text-neutral-600">CPU</span>
                <span className="text-neutral-400">{sys.cpu_pct?.toFixed(0)}%</span>
              </div>
              <Bar pct={sys.cpu_pct} color={sys.cpu_pct > 80 ? "bg-amber-500" : "bg-emerald-500"} />
            </div>
          </div>

          {/* Live terminal */}
          <div className="flex-1 flex flex-col p-5">
            <div className="flex items-center gap-2 mb-2">
              <Terminal className="h-3.5 w-3.5 text-neutral-600" />
              <span className="text-[11px] font-bold text-neutral-500 uppercase">Event Log</span>
            </div>
            <div className="flex-1 overflow-y-auto space-y-0.5 min-h-0">
              {events.length === 0
                ? <p className="text-[10px] text-neutral-700">Waiting for events…</p>
                : events.map((e, i) => (
                  <p key={i} className="text-[10px] leading-relaxed">
                    <span className="text-neutral-700">{e.ts} </span>
                    <span className={tagColor(e.tag)}>[{e.tag.slice(0,12)}]</span>{" "}
                    <span className="text-neutral-400">{e.msg}</span>
                  </p>
                ))
              }
            </div>
          </div>
        </div>
      </div>

      {/* ── Open Positions ── */}
      <div className="border-t border-neutral-800 bg-neutral-900/40">
        <div className="px-5 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-3.5 w-3.5 text-neutral-500" />
            <span className="text-[11px] font-bold text-neutral-400 uppercase">Open Positions</span>
            <span className="text-neutral-700 text-[10px]">{Object.keys(portfolio.positions).length} active</span>
          </div>
        </div>
        {Object.keys(portfolio.positions).length === 0 ? (
          <div className="px-5 pb-4 text-[11px] text-neutral-700 flex items-center gap-2">
            <Minus className="h-3 w-3" />
            No open positions — enable AUTO_EXECUTE_SIGNALS or place manual orders via Dhan
          </div>
        ) : (
          <div className="overflow-x-auto px-5 pb-4">
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="text-neutral-600 border-b border-neutral-800">
                  <th className="text-left py-1.5 pr-4">Symbol</th>
                  <th className="text-right pr-4">Qty</th>
                  <th className="text-right pr-4">Avg</th>
                  <th className="text-right pr-4">Value</th>
                  <th className="text-right">Side</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(portfolio.positions).map(([sym, pos]) => (
                  <tr key={sym} className="border-b border-neutral-800/40 hover:bg-neutral-800/20 cursor-pointer"
                    onClick={() => setSelected({ symbol: sym, data_source: "" })}>
                    <td className="py-2 pr-4 text-white font-bold">{sym}</td>
                    <td className="pr-4 text-right text-neutral-300">{pos.qty ?? "—"}</td>
                    <td className="pr-4 text-right text-neutral-400">{pos.avg_price ? fmtMoney(pos.avg_price, portfolio.currency) : "—"}</td>
                    <td className="pr-4 text-right text-white">{pos.value ? fmtMoney(pos.value, portfolio.currency) : "—"}</td>
                    <td className="text-right">
                      <span className={`px-1.5 py-0.5 rounded text-[9px] ${(pos.side||"buy")==="buy" ? "bg-emerald-500/20 text-emerald-400" : "bg-rose-500/20 text-rose-400"}`}>
                        {(pos.side||"BUY").toUpperCase()}
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
