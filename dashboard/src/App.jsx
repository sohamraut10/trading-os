import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Activity, BarChart2, Brain, ShieldAlert, TrendingUp, TrendingDown, Minus,
  Server, Terminal, Cpu, Globe, Database, Lock, Zap, ChevronDown,
  RefreshCw, AlertTriangle, CheckCircle, Search,
} from 'lucide-react';
import { createChart } from 'lightweight-charts';
import { fetchPortfolio, fetchPairSuggestions, analyzeAsset, fetchSystem, fetchCandles, fetchPositions, closePosition, fetchOptionExpiries, fetchOptionChain, fetchTradeHistory, isMarketLive } from './api';
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
  const action      = result?.action;
  const conf        = result?.confidence || 0;
  const tradeable   = result?.risk?.status === "APPROVED" || result?.risk?.status === "SCALED_DOWN";
  const scanning    = result === null;
  const closed      = result?.marketClosed === true;
  const price       = result?.price;

  return (
    <button
      onClick={onClick}
      className={`flex-shrink-0 rounded-xl border p-3 text-left transition-all cursor-pointer w-36
        ${isActive
          ? "border-blue-500/60 bg-blue-500/5 shadow-[0_0_12px_rgba(59,130,246,0.2)]"
          : closed
            ? "border-neutral-800/50 bg-neutral-900/50 opacity-40"
            : "border-neutral-800 bg-neutral-900 hover:border-neutral-700"}`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs font-bold text-white">{symbol}</span>
        {scanning
          ? <RefreshCw className="h-3 w-3 text-neutral-600 animate-spin" />
          : closed
            ? <span className="text-[10px] text-neutral-600">CLOSED</span>
            : <span className={`text-[10px] font-bold ${signalCls(action)}`}>{action || "—"}</span>
        }
      </div>
      {scanning ? (
        <div className="text-[10px] text-neutral-600 animate-pulse">scanning…</div>
      ) : closed ? (
        <div className="text-[10px] text-neutral-700">market closed</div>
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

// ── Fee Calculator ────────────────────────────────────────────────────────────

const FEE_PROFILES = {
  "NSE CNC (Delivery)": {
    brokerage: () => 0,
    stt: (qty, price) => (qty * price * 0.001) * 2,          // 0.1% both sides
    exchange: (qty, price) => qty * price * 0.0000297 * 2,   // 0.00297% both sides
    stamp: (qty, price) => qty * price * 0.00015,             // 0.015% buy side
  },
  "NSE Intraday": {
    brokerage: (qty, price) => Math.min(qty * price * 0.0003, 20) * 2,
    stt: (qty, price) => qty * price * 0.00025,               // 0.025% sell side
    exchange: (qty, price) => qty * price * 0.0000297 * 2,
    stamp: (qty, price) => qty * price * 0.00003,             // 0.003% buy side
  },
  "NSE F&O Options": {
    brokerage: (qty, price) => Math.min(qty * price * 0.0003, 20) * 2,
    stt: (qty, price) => qty * price * 0.000625,              // 0.0625% sell side (premium)
    exchange: (qty, price) => qty * price * 0.00053 * 2,     // 0.053% both sides
    stamp: (qty, price) => qty * price * 0.00003,
  },
  "NSE F&O Futures": {
    brokerage: (qty, price) => Math.min(qty * price * 0.0003, 20) * 2,
    stt: (qty, price) => qty * price * 0.0000125,             // 0.00125% sell side
    exchange: (qty, price) => qty * price * 0.00002 * 2,
    stamp: (qty, price) => qty * price * 0.00002,
  },
  "MCX Commodity": {
    brokerage: (qty, price) => Math.min(qty * price * 0.0003, 20) * 2,
    stt: () => 0,
    exchange: (qty, price) => qty * price * 0.000026 * 2,    // ~0.0026% both sides
    stamp: (qty, price) => qty * price * 0.00002,
  },
};

function calcFees(profile, qty, price) {
  const p = FEE_PROFILES[profile];
  if (!p || !qty || !price) return null;
  const brok   = p.brokerage(qty, price);
  const stt    = p.stt(qty, price);
  const exch   = p.exchange(qty, price);
  const stamp  = p.stamp(qty, price);
  const sebi   = qty * price * 0.000001 * 2;
  const gst    = (brok + exch) * 0.18;
  const total  = brok + stt + exch + stamp + sebi + gst;
  return { brok, stt, exch, stamp, sebi, gst, total };
}

// Per-leg fee for a single trade execution (used in trade history)
function calcLegFee(exchange, product, optionType, side, qty, price) {
  if (!qty || !price) return 0;
  const tv = qty * price; // turnover for this leg
  const isBuy  = side === "BUY";
  const isCNC  = product === "CNC";
  const isMCX  = exchange === "MCX_COMM";
  const isFNO  = exchange === "NSE_FNO";
  const isOpt  = optionType === "CALL" || optionType === "PUT";

  // Brokerage per leg
  const brok = isCNC ? 0 : Math.min(tv * 0.0003, 20);

  // STT (charged on specific side)
  let stt = 0;
  if (isMCX) {
    stt = 0;
  } else if (isCNC) {
    stt = tv * 0.001;            // 0.1% both sides
  } else if (isFNO && isOpt) {
    stt = isBuy ? 0 : tv * 0.000625; // 0.0625% sell side only (on premium)
  } else if (isFNO) {
    stt = isBuy ? 0 : tv * 0.0000125; // 0.00125% sell side futures
  } else {
    stt = isBuy ? 0 : tv * 0.00025;  // intraday equity 0.025% sell only
  }

  // Exchange charge per leg
  let exch = 0;
  if (isMCX)       exch = tv * 0.00002;
  else if (isOpt)  exch = tv * 0.00053;
  else if (isFNO)  exch = tv * 0.00002;
  else              exch = tv * 0.0000297;

  // Stamp duty (buy side only)
  let stamp = 0;
  if (isBuy) {
    if (isMCX)       stamp = tv * 0.00002;
    else if (isCNC)  stamp = tv * 0.00015;
    else if (isFNO)  stamp = tv * 0.00003;
    else              stamp = tv * 0.00003;
  }

  const sebi = tv * 0.000001;
  const gst  = (brok + exch) * 0.18;
  return brok + stt + exch + stamp + sebi + gst;
}

// Build P&L summary by symbol from trade history rows
function buildTradePnL(trades) {
  const bySymbol = {};
  for (const t of trades) {
    const s = t.symbol;
    if (!bySymbol[s]) bySymbol[s] = { buyVal: 0, sellVal: 0, fees: 0, buyQty: 0, sellQty: 0 };
    const fee = calcLegFee(t.exchange, t.product, t.option_type, t.side, t.qty, t.price);
    bySymbol[s].fees += fee;
    if (t.side === "BUY")  { bySymbol[s].buyVal  += t.qty * t.price; bySymbol[s].buyQty  += t.qty; }
    if (t.side === "SELL") { bySymbol[s].sellVal += t.qty * t.price; bySymbol[s].sellQty += t.qty; }
  }
  return bySymbol;
}

function FeeCalculator() {
  const [profile, setProfile]   = useState("NSE CNC (Delivery)");
  const [qty, setQty]           = useState("");
  const [price, setPrice]       = useState("");
  const [open, setOpen]         = useState(false);

  const fees = calcFees(profile, parseFloat(qty), parseFloat(price));
  const turnover = parseFloat(qty) * parseFloat(price) || 0;

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full px-4 py-3 flex items-center gap-2 hover:bg-neutral-800/40 transition-colors"
      >
        <Zap className="h-3.5 w-3.5 text-amber-400 flex-shrink-0" />
        <span className="text-xs font-bold text-amber-400 uppercase tracking-wider">Fee Calculator</span>
        <span className="text-[10px] text-neutral-600 ml-1">Dhan brokerage + taxes</span>
        {fees && (
          <span className="ml-auto text-[11px] font-bold text-white">
            Total: <span className="text-amber-400">₹{fees.total.toFixed(2)}</span>
          </span>
        )}
        <ChevronDown className={`h-3.5 w-3.5 text-neutral-500 ml-1 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="border-t border-neutral-800 p-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
            <div>
              <label className="block text-[10px] text-neutral-600 uppercase mb-1">Segment</label>
              <select
                value={profile}
                onChange={e => setProfile(e.target.value)}
                className="w-full bg-neutral-800 border border-neutral-700 text-neutral-200 text-[11px] rounded px-2 py-1.5 focus:outline-none focus:border-blue-500"
              >
                {Object.keys(FEE_PROFILES).map(k => <option key={k} value={k}>{k}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[10px] text-neutral-600 uppercase mb-1">Quantity / Lots</label>
              <input
                type="number" min="1" value={qty} onChange={e => setQty(e.target.value)}
                placeholder="e.g. 50"
                className="w-full bg-neutral-800 border border-neutral-700 text-neutral-200 text-[11px] rounded px-2 py-1.5 focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-[10px] text-neutral-600 uppercase mb-1">Price (₹)</label>
              <input
                type="number" min="0" step="0.05" value={price} onChange={e => setPrice(e.target.value)}
                placeholder="e.g. 1200.00"
                className="w-full bg-neutral-800 border border-neutral-700 text-neutral-200 text-[11px] rounded px-2 py-1.5 focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>

          {fees ? (
            <>
              <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 mb-3">
                {[
                  { label: "Brokerage", val: fees.brok, color: "text-neutral-300" },
                  { label: "STT",       val: fees.stt,  color: "text-rose-400" },
                  { label: "Exch. Fee", val: fees.exch, color: "text-neutral-300" },
                  { label: "Stamp",     val: fees.stamp, color: "text-neutral-300" },
                  { label: "SEBI",      val: fees.sebi, color: "text-neutral-300" },
                  { label: "GST 18%",   val: fees.gst,  color: "text-amber-400/80" },
                ].map(({ label, val, color }) => (
                  <div key={label} className="bg-neutral-800/60 rounded p-2 text-center">
                    <div className="text-[9px] text-neutral-600 uppercase mb-1">{label}</div>
                    <div className={`text-[11px] font-bold ${color}`}>₹{val.toFixed(2)}</div>
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between bg-neutral-800/40 rounded-lg px-4 py-2.5">
                <div className="text-[10px] text-neutral-500">
                  Turnover: <span className="text-neutral-300 font-bold">₹{turnover.toFixed(2)}</span>
                  <span className="ml-3">Break-even: <span className="text-neutral-300 font-bold">₹{(fees.total / (parseFloat(qty) || 1)).toFixed(3)}/unit</span></span>
                </div>
                <div className="text-sm font-black">
                  Total Fees: <span className="text-amber-400">₹{fees.total.toFixed(2)}</span>
                  <span className="text-[10px] text-neutral-500 font-normal ml-1.5">
                    ({turnover > 0 ? ((fees.total / turnover) * 100).toFixed(3) : "0"}% of turnover)
                  </span>
                </div>
              </div>
            </>
          ) : (
            <div className="text-center py-3 text-[11px] text-neutral-700">Enter quantity and price to calculate fees</div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Options Chain Panel ───────────────────────────────────────────────────────

function fmtOI(v) {
  if (!v || v <= 0) return "—";
  if (v >= 1e5) return `${(v / 1e5).toFixed(1)}L`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}k`;
  return String(v);
}

function OptionsChainPanel({ symbol, expiries, expiry, onExpiry, chain, loading, signalAction }) {
  if (!expiries.length && !chain && !loading) return null;

  const spot = chain?.spot || 0;
  const allStrikes = chain?.strikes || [];

  let atmIdx = 0;
  if (spot > 0 && allStrikes.length) {
    let minDiff = Infinity;
    allStrikes.forEach((s, i) => {
      const d = Math.abs(s.strike - spot);
      if (d < minDiff) { minDiff = d; atmIdx = i; }
    });
  }

  const autoPickIdx = signalAction === "BUY"
    ? atmIdx + 1
    : signalAction === "SELL"
    ? atmIdx - 1
    : -1;

  const lo = Math.max(0, atmIdx - 8);
  const hi = Math.min(allStrikes.length - 1, atmIdx + 8);
  const visible = allStrikes.slice(lo, hi + 1);

  const fmtLtp = v => (v != null && v > 0) ? v.toFixed(2) : "—";

  return (
    <div className="px-5 pb-4">
      <div className="bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden">
        {/* header */}
        <div className="px-4 py-3 border-b border-neutral-800 flex items-center gap-3 flex-wrap">
          <BarChart2 className="h-3.5 w-3.5 text-blue-400 flex-shrink-0" />
          <span className="text-xs font-bold text-blue-400 uppercase tracking-wider">Options Chain</span>
          <span className="text-[11px] text-neutral-500">{symbol}</span>
          {spot > 0 && (
            <span className="text-[11px] text-neutral-400">
              Spot: <span className="text-white font-bold">₹{spot.toFixed(0)}</span>
            </span>
          )}
          {loading && <RefreshCw className="h-3 w-3 text-neutral-600 animate-spin" />}
          <div className="ml-auto flex gap-1.5 flex-wrap">
            {expiries.slice(0, 6).map(exp => (
              <button
                key={exp}
                onClick={() => onExpiry(exp)}
                className={`px-2 py-0.5 text-[10px] rounded border font-mono transition-colors ${
                  exp === expiry
                    ? "bg-blue-500/20 border-blue-500/40 text-blue-300 font-bold"
                    : "border-neutral-700 text-neutral-500 hover:border-neutral-600 hover:text-neutral-400"
                }`}
              >
                {exp}
              </button>
            ))}
          </div>
        </div>

        {/* table */}
        {visible.length > 0 ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-[10px] font-mono">
                <thead>
                  <tr className="border-b border-neutral-800 bg-neutral-950/40">
                    <th className="text-right py-2 px-2 text-emerald-700 font-medium">OI</th>
                    <th className="text-right px-2 text-emerald-700 font-medium">IV%</th>
                    <th className="text-right px-2 text-emerald-700 font-medium">Δ</th>
                    <th className="text-right px-2 pr-3 text-emerald-400 font-bold">CE LTP</th>
                    <th className="text-center px-3 text-neutral-200 font-black bg-neutral-800/50">STRIKE</th>
                    <th className="text-left px-2 pl-3 text-rose-400 font-bold">PE LTP</th>
                    <th className="text-left px-2 text-rose-700 font-medium">Δ</th>
                    <th className="text-left px-2 text-rose-700 font-medium">IV%</th>
                    <th className="text-left px-2 text-rose-700 font-medium">OI</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((s, vi) => {
                    const ri = lo + vi;
                    const isAtm = ri === atmIdx;
                    const isAuto = ri === autoPickIdx;
                    const ce = s.ce || {};
                    const pe = s.pe || {};
                    const autoCE = isAuto && signalAction === "BUY";
                    const autoPE = isAuto && signalAction === "SELL";
                    return (
                      <tr
                        key={s.strike}
                        className={`border-b transition-colors ${
                          isAtm
                            ? "border-blue-500/30 bg-blue-500/10"
                            : isAuto
                            ? `border-neutral-800/40 ${signalAction === "BUY" ? "bg-emerald-500/10" : "bg-rose-500/10"}`
                            : "border-neutral-800/30 hover:bg-neutral-800/20"
                        }`}
                      >
                        <td className="text-right py-1.5 px-2 text-emerald-800">{fmtOI(ce.oi)}</td>
                        <td className="text-right px-2 text-emerald-800">{ce.iv ? ce.iv.toFixed(1) : "—"}</td>
                        <td className="text-right px-2 text-emerald-700">{ce.delta ? ce.delta.toFixed(2) : "—"}</td>
                        <td className={`text-right px-2 pr-3 font-bold ${autoCE ? "text-emerald-300" : "text-emerald-500"}`}>
                          {fmtLtp(ce.ltp)}
                          {autoCE && (
                            <span className="ml-1 text-[8px] bg-emerald-500/25 text-emerald-400 px-1 rounded border border-emerald-500/30">AUTO</span>
                          )}
                        </td>
                        <td className={`text-center px-3 font-black text-sm bg-neutral-800/40 ${isAtm ? "text-white" : "text-neutral-400"}`}>
                          {s.strike}
                          {isAtm && <span className="ml-1.5 text-[8px] text-blue-400 font-normal">ATM</span>}
                        </td>
                        <td className={`text-left px-2 pl-3 font-bold ${autoPE ? "text-rose-300" : "text-rose-500"}`}>
                          {fmtLtp(pe.ltp)}
                          {autoPE && (
                            <span className="ml-1 text-[8px] bg-rose-500/25 text-rose-400 px-1 rounded border border-rose-500/30">AUTO</span>
                          )}
                        </td>
                        <td className="text-left px-2 text-rose-700">{pe.delta ? pe.delta.toFixed(2) : "—"}</td>
                        <td className="text-left px-2 text-rose-800">{pe.iv ? pe.iv.toFixed(1) : "—"}</td>
                        <td className="text-left px-2 text-rose-800">{fmtOI(pe.oi)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="px-4 py-2 border-t border-neutral-800/50 flex items-center gap-4 text-[9px] text-neutral-700">
              <span className="text-blue-500/60">■ ATM</span>
              {signalAction && signalAction !== "HOLD" && autoPickIdx >= 0 && (
                <span className={signalAction === "BUY" ? "text-emerald-700/80" : "text-rose-700/80"}>
                  ■ AUTO-PICK ({signalAction === "BUY" ? "CE" : "PE"} — {signalAction === "BUY" ? "1 OTM above ATM" : "1 OTM below ATM"})
                </span>
              )}
              <span className="ml-auto">showing ATM ±8 of {allStrikes.length} strikes</span>
            </div>
          </>
        ) : loading ? (
          <div className="px-4 py-8 text-center text-neutral-600 text-xs">Loading chain…</div>
        ) : (
          <div className="px-4 py-8 text-center text-neutral-700 text-xs">
            {expiry ? "No chain data for this symbol / expiry" : "Select an expiry above"}
          </div>
        )}
      </div>
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
  const [positions, setPositions]   = useState({});
  const [closingPos, setClosingPos] = useState(null);
  const [confirmClose, setConfirmClose] = useState(null);
  const [optExpiries, setOptExpiries]       = useState([]);
  const [optExpiry, setOptExpiry]           = useState("");
  const [optChain, setOptChain]             = useState(null);
  const [optChainLoading, setOptChainLoading] = useState(false);
  const [activeTab, setActiveTab]           = useState("positions");
  const [tradeHistory, setTradeHistory]     = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
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

  // enriched positions (P&L + SL/TP) every 15s
  useEffect(() => {
    const poll = () => fetchPositions().then(setPositions).catch(() => {});
    poll();
    const t = setInterval(poll, 15_000);
    return () => clearInterval(t);
  }, []);

  const handleClosePosition = async (symbol) => {
    setClosingPos(symbol);
    setConfirmClose(null);
    try {
      await closePosition(symbol);
      setPositions(prev => { const n = { ...prev }; delete n[symbol]; return n; });
    } catch (e) {
      alert(`Close failed: ${e.message}`);
    } finally {
      setClosingPos(null);
    }
  };

  // options chain: fetch expiries when symbol changes
  useEffect(() => {
    if (!selected.symbol) return;
    setOptExpiries([]);
    setOptExpiry("");
    setOptChain(null);
    fetchOptionExpiries(selected.symbol)
      .then(d => {
        const list = d.expiries || [];
        setOptExpiries(list);
        if (list.length) setOptExpiry(list[0]);
      })
      .catch(() => {});
  }, [selected.symbol]);

  // options chain: fetch chain when expiry changes
  useEffect(() => {
    if (!optExpiry || !selected.symbol) return;
    setOptChainLoading(true);
    fetchOptionChain(selected.symbol, optExpiry)
      .then(d => setOptChain(d))
      .catch(() => setOptChain(null))
      .finally(() => setOptChainLoading(false));
  }, [selected.symbol, optExpiry]);

  // trade history: fetch on tab switch, refresh every 2 min
  useEffect(() => {
    if (activeTab !== "history") return;
    let cancelled = false;
    const load = async () => {
      setHistoryLoading(true);
      try {
        const d = await fetchTradeHistory(30);
        if (!cancelled) setTradeHistory(d.trades || []);
      } catch { /* silently skip */ }
      finally { if (!cancelled) setHistoryLoading(false); }
    };
    load();
    const t = setInterval(load, 120_000);
    return () => { cancelled = true; clearInterval(t); };
  }, [activeTab]);

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
      if (!isMarketLive(sym)) {
        setScanResults(prev => ({ ...prev, [sym]: { action: null, confidence: null, marketClosed: true } }));
        continue;
      }
      try {
        const r = await analyzeAsset(sym);
        setScanResults(prev => ({ ...prev, [sym]: { action: r.action, confidence: r.confidence, risk: r.risk_check, price: r.current_price, marketClosed: false } }));
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

      {/* ── Positions + History Tab Panel ── */}
      <div className="border-b border-neutral-800 bg-neutral-900/40">
        {/* tab bar */}
        <div className="px-5 pt-2.5 flex items-center gap-1 border-b border-neutral-800/60">
          <button
            onClick={() => setActiveTab("positions")}
            className={`flex items-center gap-1.5 px-3 py-2 text-[11px] font-bold border-b-2 transition-colors ${
              activeTab === "positions"
                ? "border-blue-500 text-white"
                : "border-transparent text-neutral-500 hover:text-neutral-300"
            }`}
          >
            <TrendingUp className="h-3 w-3" />
            Open Positions
            {Object.keys(positions).length > 0 && (
              <span className="ml-1 px-1.5 py-0.5 rounded-full bg-blue-500/20 text-blue-400 text-[9px] font-black">
                {Object.keys(positions).length}
              </span>
            )}
          </button>
          <button
            onClick={() => setActiveTab("history")}
            className={`flex items-center gap-1.5 px-3 py-2 text-[11px] font-bold border-b-2 transition-colors ${
              activeTab === "history"
                ? "border-blue-500 text-white"
                : "border-transparent text-neutral-500 hover:text-neutral-300"
            }`}
          >
            <Terminal className="h-3 w-3" />
            Trade History
            {historyLoading && <RefreshCw className="h-2.5 w-2.5 animate-spin text-neutral-600" />}
          </button>
          <div className="ml-auto flex items-center gap-4 text-[10px] text-neutral-500 pb-1">
            <span>Equity: <span className="text-white font-bold">{fmtMoney(portfolio.equity, portfolio.currency)}</span></span>
            <span>Cash: <span className="text-emerald-400 font-bold">{fmtMoney(portfolio.cash, portfolio.currency)}</span></span>
            <span className={portfolio.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}>
              P&L: {portfolio.pnl >= 0 ? "+" : ""}{portfolio.pnl.toFixed(2)}%
            </span>
          </div>
        </div>

        {/* ── Open Positions tab ── */}
        {activeTab === "positions" && (
          <>
            {confirmClose && (
              <div className="mx-5 mt-3 p-3 bg-rose-500/10 border border-rose-500/30 rounded-lg flex items-center justify-between">
                <span className="text-[11px] text-rose-300">Close <span className="font-bold text-white">{confirmClose}</span> at market?</span>
                <div className="flex gap-2">
                  <button onClick={() => handleClosePosition(confirmClose)}
                    className="px-3 py-1 text-[10px] bg-rose-600 hover:bg-rose-500 text-white rounded font-bold">Confirm</button>
                  <button onClick={() => setConfirmClose(null)}
                    className="px-3 py-1 text-[10px] bg-neutral-700 hover:bg-neutral-600 text-neutral-300 rounded">Cancel</button>
                </div>
              </div>
            )}
            {Object.keys(positions).length === 0 ? (
              <div className="px-5 py-4 text-[11px] text-neutral-700 flex items-center gap-2">
                <Minus className="h-3 w-3" />
                No open positions — executed signals will appear here
              </div>
            ) : (
              <div className="overflow-x-auto px-5 pb-3 pt-2">
                <table className="w-full text-[11px] font-mono">
                  <thead>
                    <tr className="text-neutral-600 border-b border-neutral-800">
                      <th className="text-left py-1.5 pr-3">Symbol</th>
                      <th className="text-right pr-3">Qty</th>
                      <th className="text-right pr-3">Avg</th>
                      <th className="text-right pr-3">LTP</th>
                      <th className="text-right pr-3">P&L</th>
                      <th className="text-right pr-3">SL</th>
                      <th className="text-right pr-3">TP</th>
                      <th className="text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(positions).map(([sym, pos]) => {
                      const pnl = pos.unrealized_pnl ?? 0;
                      const pnlPct = pos.unrealized_pnl_pct ?? 0;
                      const isLong = (pos.qty ?? 0) > 0;
                      const isClosing = closingPos === sym;
                      return (
                        <tr key={sym} className="border-b border-neutral-800/40 hover:bg-neutral-800/20">
                          <td className="py-2 pr-3">
                            <button className="text-white font-bold hover:text-blue-400 text-left"
                              onClick={() => setSelected({ symbol: sym, data_source: "" })}>{sym}</button>
                            <span className={`ml-1.5 px-1 py-0.5 rounded text-[8px] ${isLong ? "bg-emerald-500/20 text-emerald-400" : "bg-rose-500/20 text-rose-400"}`}>
                              {isLong ? "LONG" : "SHORT"}
                            </span>
                          </td>
                          <td className="pr-3 text-right text-neutral-300">{Math.abs(pos.qty ?? 0)}</td>
                          <td className="pr-3 text-right text-neutral-400">{pos.avg_price ? fmtMoney(pos.avg_price, portfolio.currency) : "—"}</td>
                          <td className="pr-3 text-right text-neutral-300">{pos.ltp ? fmtMoney(pos.ltp, portfolio.currency) : "—"}</td>
                          <td className={`pr-3 text-right font-bold ${pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                            <span title={`${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%`}>
                              {pnl >= 0 ? "+" : ""}{fmtMoney(pnl, portfolio.currency)}
                              <span className="text-[9px] ml-1 opacity-70">{pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(1)}%</span>
                            </span>
                          </td>
                          <td className="pr-3 text-right text-rose-400/80">{pos.sl_price ? fmtMoney(pos.sl_price, portfolio.currency) : <span className="text-neutral-700">—</span>}</td>
                          <td className="pr-3 text-right text-emerald-400/80">{pos.tp_price ? fmtMoney(pos.tp_price, portfolio.currency) : <span className="text-neutral-700">—</span>}</td>
                          <td className="text-right">
                            <button disabled={isClosing} onClick={() => setConfirmClose(sym)}
                              className="px-2 py-1 text-[9px] bg-rose-600/20 hover:bg-rose-600/40 border border-rose-600/40 text-rose-400 rounded disabled:opacity-40 disabled:cursor-not-allowed">
                              {isClosing ? "Closing…" : "Close"}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        {/* ── Trade History tab ── */}
        {activeTab === "history" && (
          <>
            {historyLoading && tradeHistory.length === 0 ? (
              <div className="px-5 py-4 text-[11px] text-neutral-600 flex items-center gap-2">
                <RefreshCw className="h-3 w-3 animate-spin" /> Loading trade history…
              </div>
            ) : tradeHistory.length === 0 ? (
              <div className="px-5 py-4 text-[11px] text-neutral-700 flex items-center gap-2">
                <Minus className="h-3 w-3" /> No executed trades in the last 30 days
              </div>
            ) : (() => {
              const pnlBySymbol = buildTradePnL(tradeHistory);
              const totalFees = Object.values(pnlBySymbol).reduce((a, v) => a + v.fees, 0);
              const totalGross = Object.entries(pnlBySymbol).reduce((a, [, v]) => {
                return v.buyQty === v.sellQty ? a + (v.sellVal - v.buyVal) : a;
              }, 0);
              const totalNet = totalGross - totalFees;
              return (
                <div className="px-5 pb-3 pt-2">
                  {/* P&L Summary bar */}
                  <div className="mb-3 grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {[
                      { label: "Gross P&L", val: totalGross, fmt: v => `${v >= 0 ? "+" : ""}₹${Math.abs(v).toFixed(2)}`, color: totalGross >= 0 ? "text-emerald-400" : "text-rose-400" },
                      { label: "Total Fees", val: totalFees, fmt: v => `−₹${v.toFixed(2)}`, color: "text-amber-400" },
                      { label: "Net P&L", val: totalNet, fmt: v => `${v >= 0 ? "+" : ""}₹${Math.abs(v).toFixed(2)}`, color: totalNet >= 0 ? "text-emerald-400" : "text-rose-400" },
                      { label: "Trades", val: tradeHistory.length, fmt: v => `${v} legs`, color: "text-neutral-300" },
                    ].map(({ label, val, fmt, color }) => (
                      <div key={label} className="bg-neutral-800/50 rounded-lg px-3 py-2">
                        <div className="text-[9px] text-neutral-600 uppercase mb-0.5">{label}</div>
                        <div className={`text-sm font-black ${color}`}>{fmt(val)}</div>
                      </div>
                    ))}
                  </div>

                  {/* Trade history table */}
                  <div className="overflow-x-auto">
                    <table className="w-full text-[11px] font-mono">
                      <thead>
                        <tr className="text-neutral-600 border-b border-neutral-800">
                          <th className="text-left py-1.5 pr-3">Time</th>
                          <th className="text-left pr-3">Symbol</th>
                          <th className="text-center pr-3">Side</th>
                          <th className="text-right pr-3">Qty</th>
                          <th className="text-right pr-3">Price</th>
                          <th className="text-right pr-3">Turnover</th>
                          <th className="text-right pr-3 text-amber-600">Fees</th>
                          <th className="text-left pr-3">Strike</th>
                          <th className="text-left">Expiry</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tradeHistory.map((t, i) => {
                          const isBuy = t.side === "BUY";
                          const isOpt = t.option_type === "CALL" || t.option_type === "PUT";
                          const timeStr = t.time ? t.time.slice(0, 16) : "—";
                          const tv = t.qty * t.price;
                          const fee = calcLegFee(t.exchange, t.product, t.option_type, t.side, t.qty, t.price);
                          return (
                            <tr key={t.trade_id || i} className="border-b border-neutral-800/40 hover:bg-neutral-800/20">
                              <td className="py-1.5 pr-3 text-neutral-500 tabular-nums text-[10px]">{timeStr}</td>
                              <td className="pr-3">
                                <button className="text-white font-bold hover:text-blue-400 text-left"
                                  onClick={() => setSelected({ symbol: t.symbol.split("-")[0], data_source: "" })}>
                                  {t.symbol}
                                </button>
                                {isOpt && (
                                  <span className={`ml-1 px-1 py-0.5 rounded text-[8px] ${t.option_type === "CALL" ? "bg-emerald-500/20 text-emerald-400" : "bg-rose-500/20 text-rose-400"}`}>
                                    {t.option_type === "CALL" ? "CE" : "PE"}
                                  </span>
                                )}
                              </td>
                              <td className="pr-3 text-center">
                                <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                                  isBuy ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/20"
                                        : "bg-rose-500/20 text-rose-400 border-rose-500/20"
                                }`}>{t.side}</span>
                              </td>
                              <td className="pr-3 text-right text-neutral-300 tabular-nums">{t.qty || "—"}</td>
                              <td className="pr-3 text-right text-neutral-200 font-bold tabular-nums">
                                {t.price > 0 ? `₹${t.price.toFixed(2)}` : "—"}
                              </td>
                              <td className="pr-3 text-right text-neutral-400 tabular-nums">
                                {tv > 0 ? `₹${tv.toFixed(0)}` : "—"}
                              </td>
                              <td className="pr-3 text-right text-amber-500/80 tabular-nums font-mono">
                                {fee > 0 ? `₹${fee.toFixed(2)}` : "—"}
                              </td>
                              <td className="pr-3 text-neutral-500 tabular-nums">
                                {t.strike ? `₹${parseFloat(t.strike).toFixed(0)}` : <span className="text-neutral-700">—</span>}
                              </td>
                              <td className="text-neutral-600 tabular-nums text-[10px]">
                                {t.expiry ? t.expiry.slice(0, 10) : <span className="text-neutral-700">—</span>}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>

                  {/* Per-symbol P&L breakdown */}
                  <div className="mt-3 pt-3 border-t border-neutral-800/60">
                    <div className="text-[9px] text-neutral-600 uppercase mb-2 font-bold">Per-Symbol Summary (closed positions)</div>
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(pnlBySymbol).filter(([, v]) => v.buyQty > 0 || v.sellQty > 0).map(([sym, v]) => {
                        const closed = v.buyQty === v.sellQty;
                        const gross = closed ? v.sellVal - v.buyVal : null;
                        const net = gross !== null ? gross - v.fees : null;
                        return (
                          <div key={sym} className={`px-2.5 py-1.5 rounded-lg border text-[10px] ${
                            net !== null
                              ? net >= 0 ? "border-emerald-500/20 bg-emerald-500/5" : "border-rose-500/20 bg-rose-500/5"
                              : "border-neutral-700 bg-neutral-800/30"
                          }`}>
                            <span className="text-white font-bold">{sym.split("-")[0]}</span>
                            {net !== null ? (
                              <>
                                <span className={`ml-2 font-bold ${net >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                                  Net {net >= 0 ? "+" : ""}₹{net.toFixed(2)}
                                </span>
                                <span className="ml-1.5 text-neutral-600">
                                  (Gross {gross >= 0 ? "+" : ""}₹{gross.toFixed(2)} − Fees ₹{v.fees.toFixed(2)})
                                </span>
                              </>
                            ) : (
                              <span className="ml-2 text-neutral-500">open · fees ₹{v.fees.toFixed(2)}</span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                  <div className="pt-2 text-[9px] text-neutral-700">
                    {tradeHistory.length} trade legs · last 30 days · click symbol to chart
                  </div>
                </div>
              );
            })()}
          </>
        )}
      </div>

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

      {/* ── Options Chain ── */}
      <OptionsChainPanel
        symbol={selected.symbol}
        expiries={optExpiries}
        expiry={optExpiry}
        onExpiry={setOptExpiry}
        chain={optChain}
        loading={optChainLoading}
        signalAction={action}
      />

      {/* ── Fee Calculator ── */}
      <div className="px-5 pb-4">
        <FeeCalculator />
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

    </div>
  );
}
