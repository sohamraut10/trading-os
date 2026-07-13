import React, { useState, useEffect, useRef } from 'react';
import {
  Activity, BarChart2, Brain, ShieldAlert, TrendingUp,
  Server, Terminal, Cpu, Globe, Database, Lock, Zap, ChevronDown,
} from 'lucide-react';
import { createChart } from 'lightweight-charts';
import { fetchPortfolio, fetchPairSuggestions, analyzeAsset, fetchSystem, fetchCandles } from './api';
import { connectEvents } from './eventsPoller';

// ── helpers ──────────────────────────────────────────────────────────────────

const AGENT_META = {
  Technical:  { label: "Technical Analyst",   icon: BarChart2,   color: "blue",   indicators: ["RSI", "MACD", "EMA", "VWAP"] },
  Sentiment:  { label: "Sentiment & News",     icon: Globe,       color: "purple", indicators: ["LLM NLP", "Keywords"] },
  Quant:      { label: "Quant & Statistical",  icon: TrendingUp,  color: "orange", indicators: ["Hurst", "Z-Score", "Kelly EV"] },
  OrderFlow:  { label: "Market Structure",     icon: Database,    color: "indigo", indicators: ["Volume Profile", "S/R", "Delta"] },
};

const COLOR = {
  blue:   { bg: "bg-blue-500/10",   border: "border-blue-500/20",   icon: "text-blue-400",   conf: "text-blue-400" },
  purple: { bg: "bg-purple-500/10", border: "border-purple-500/20", icon: "text-purple-400", conf: "text-purple-400" },
  orange: { bg: "bg-orange-500/10", border: "border-orange-500/20", icon: "text-orange-400", conf: "text-orange-400" },
  indigo: { bg: "bg-indigo-500/10", border: "border-indigo-500/20", icon: "text-indigo-400", conf: "text-indigo-400" },
};

function signalColor(decision) {
  if (decision === "BUY")  return "text-emerald-400";
  if (decision === "SELL") return "text-rose-400";
  return "text-neutral-400";
}

function signalBadge(decision) {
  if (decision === "BUY")  return "bg-emerald-500/20 text-emerald-400 border border-emerald-500/20";
  if (decision === "SELL") return "bg-rose-500/20 text-rose-400 border border-rose-500/20";
  return "bg-neutral-700 text-neutral-300 border border-neutral-600";
}

function finalActionDisplay(signal) {
  if (!signal || signal.error) return { label: "—", cls: "text-neutral-500" };
  const action = signal.action;
  if (action === "BUY")  return { label: "BUY",  cls: "text-emerald-400 drop-shadow-[0_0_10px_rgba(52,211,153,0.4)]" };
  if (action === "SELL") return { label: "SELL", cls: "text-rose-400 drop-shadow-[0_0_10px_rgba(251,113,133,0.4)]" };
  return { label: "HOLD", cls: "text-amber-400" };
}

// ── sub-components ───────────────────────────────────────────────────────────

function PairDropdown({ pairs, selected, onSelect }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 bg-neutral-800 hover:bg-neutral-700 border border-neutral-700 rounded-lg px-3 py-1.5 text-sm font-mono transition-colors"
      >
        <span className="text-white font-bold">{selected || "Select pair"}</span>
        <ChevronDown className="h-3 w-3 text-neutral-400" />
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-48 bg-neutral-900 border border-neutral-700 rounded-lg shadow-xl z-50 max-h-64 overflow-y-auto">
          {pairs.map(p => (
            <button
              key={`${p.symbol}-${p.data_source || "primary"}`}
              onClick={() => { onSelect(p); setOpen(false); }}
              className={`w-full text-left px-3 py-2 text-xs font-mono hover:bg-neutral-800 transition-colors flex justify-between items-center ${p.symbol === selected ? "text-blue-400" : "text-neutral-300"}`}
            >
              <span>{p.symbol}</span>
              {p.data_source && <span className="text-[9px] text-neutral-500 uppercase">{p.data_source}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function AgentCard({ agent }) {
  const meta  = AGENT_META[agent.name] || { label: agent.name, icon: Cpu, color: "blue", indicators: [] };
  const clr   = COLOR[meta.color];
  const Icon  = meta.icon;
  const dim   = agent.confidence < 55;

  return (
    <div className={`bg-neutral-900 border border-neutral-800 p-4 rounded-xl flex items-center gap-4 ${dim ? "opacity-70" : ""}`}>
      <div className={`h-12 w-12 rounded-lg ${clr.bg} ${clr.border} border flex items-center justify-center flex-shrink-0`}>
        <Icon className={`h-6 w-6 ${clr.icon}`} />
      </div>
      <div className="flex-grow min-w-0">
        <div className="flex justify-between items-center mb-1">
          <h3 className="text-sm font-bold text-neutral-200">{meta.label}</h3>
          <span className={`text-xs font-bold ${clr.conf}`}>{agent.confidence.toFixed(0)}% CONF</span>
        </div>
        <p className="text-xs text-neutral-500 mb-2 truncate">{meta.indicators.join(", ")}</p>
        <div className="flex gap-2 flex-wrap">
          {agent.indicators && Object.entries(agent.indicators).slice(0, 2).map(([k, v]) => (
            <span key={k} className="text-[10px] px-1.5 py-0.5 bg-neutral-800 text-neutral-300 rounded capitalize">
              {k.replace(/_/g, " ")}: {typeof v === "number" ? v.toFixed(2) : String(v).slice(0, 12)}
            </span>
          ))}
          <span className={`text-[10px] px-1.5 py-0.5 rounded ${signalBadge(agent.decision)}`}>
            VOTE: {agent.decision}
          </span>
          {dim && (
            <span className="text-[10px] text-amber-500 flex items-center ml-auto">Below 55% filter</span>
          )}
        </div>
      </div>
    </div>
  );
}

function InfraRow({ icon: Icon, label, status, statusCls }) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-neutral-500" />
        <span className="text-xs text-neutral-400">{label}</span>
      </div>
      <span className={`text-xs ${statusCls}`}>{status}</span>
    </div>
  );
}

function ProgressBar({ pct, color = "bg-blue-500" }) {
  return (
    <div className="h-1 w-full bg-neutral-800 rounded-full overflow-hidden">
      <div className={`h-full ${color} transition-all duration-500`} style={{ width: `${Math.min(pct, 100)}%` }} />
    </div>
  );
}

function PriceChart({ asset, source }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: { background: { color: '#0a0a0a' }, textColor: '#525252' },
      grid: { vertLines: { color: '#171717' }, horzLines: { color: '#171717' } },
      rightPriceScale: { borderColor: '#262626' },
      timeScale: { borderColor: '#262626', timeVisible: true, secondsVisible: false },
      width: containerRef.current.clientWidth,
      height: 200,
    });
    const series = chart.addCandlestickSeries({
      upColor: '#34d399', downColor: '#f87171',
      borderVisible: false, wickUpColor: '#34d399', wickDownColor: '#f87171',
    });
    chartRef.current = chart;
    seriesRef.current = series;

    const ro = new ResizeObserver(e => chart.resize(e[0].contentRect.width, 200));
    ro.observe(containerRef.current);
    return () => { ro.disconnect(); chart.remove(); };
  }, []);

  useEffect(() => {
    if (!seriesRef.current || !asset) return;
    setLoading(true);
    fetchCandles(asset, source)
      .then(bars => {
        const data = bars
          .filter(c => c.time && c.open)
          .map(c => ({ time: Math.floor(c.time), open: c.open, high: c.high, low: c.low, close: c.close }))
          .sort((a, b) => a.time - b.time);
        if (data.length) { seriesRef.current.setData(data); chartRef.current.timeScale().fitContent(); }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [asset, source]);

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-neutral-800 flex justify-between items-center">
        <h2 className="text-xs font-bold text-neutral-300 uppercase tracking-wider flex items-center gap-2">
          <BarChart2 className="h-3.5 w-3.5 text-neutral-500" />
          {asset} · 1H Candlestick
        </h2>
        {loading && <span className="text-[10px] text-neutral-500 animate-pulse">loading…</span>}
      </div>
      <div ref={containerRef} />
    </div>
  );
}

// ── main App ─────────────────────────────────────────────────────────────────

export default function App() {
  const [time, setTime]           = useState(new Date());
  const [pairs, setPairs]         = useState([]);
  const [selectedAsset, setSelectedAsset] = useState("ICICIBANK");
  const [selectedSource, setSelectedSource] = useState("");
  const [signal, setSignal]       = useState(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [portfolio, setPortfolio] = useState({ equity: 0, cash: 0, pnl: 0 });
  const [sysMetrics, setSysMetrics] = useState({ ram_used_gb: 0, ram_total_gb: 8, ram_pct: 0, cpu_pct: 0, disk_pct: 0 });
  const [apiOk, setApiOk]         = useState(true);
  const [events, setEvents]       = useState([]);
  const eventsRef = useRef(events);
  eventsRef.current = events;

  // Clock
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  // Load pairs on mount
  useEffect(() => {
    fetchPairSuggestions()
      .then(data => {
        const all = data.pairs || [];
        setPairs(all);
        if (all.length > 0) {
          setSelectedAsset(all[0].symbol);
          setSelectedSource(all[0].data_source || "");
        }
      })
      .catch(() => {});
  }, []);

  // Auto-analyze whenever the selected asset changes, then re-run every 60s
  useEffect(() => {
    let cancelled = false;
    async function run() {
      if (!selectedAsset) return;
      setAnalyzing(true);
      try {
        const result = await analyzeAsset(selectedAsset);
        if (!cancelled) {
          setSignal(result);
          setApiOk(true);
          addEvent("CONSENSUS", `${selectedAsset} → ${result.final_decision} (${(result.confidence || 0).toFixed(1)}%)`);
        }
      } catch {
        if (!cancelled) setApiOk(false);
      } finally {
        if (!cancelled) setAnalyzing(false);
      }
    }
    run();
    const interval = setInterval(run, 60_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [selectedAsset]);

  // Portfolio + system metrics every 10s
  useEffect(() => {
    async function poll() {
      try {
        const [port, sys] = await Promise.all([fetchPortfolio(), fetchSystem()]);
        setPortfolio({ equity: port.equity, cash: port.cash, pnl: (port.daily_pnl_pct || 0) * 100 });
        setSysMetrics(sys);
        setApiOk(true);
      } catch { /* non-fatal */ }
    }
    poll();
    const t = setInterval(poll, 10_000);
    return () => clearInterval(t);
  }, []);

  // Event stream
  function addEvent(tag, msg) {
    setEvents(prev => {
      const next = [{ tag, msg, ts: new Date().toISOString() }, ...prev].slice(0, 50);
      return next;
    });
  }

  useEffect(() => {
    const poller = connectEvents(
      (evt) => {
        const tag  = evt.event_type || "INFO";
        const body = evt.data?.asset ? `${evt.data.asset}: ${tag}` : tag;
        addEvent(tag, body);
      },
      () => {},
    );
    return () => poller.close();
  }, []);

  const handleSelectPair = (pair) => {
    setSelectedAsset(pair.symbol);
    setSelectedSource(pair.data_source || "");
  };

  // Derived signal display
  const { label: actionLabel, cls: actionCls } = finalActionDisplay(signal);
  const conviction = signal?.confidence || 0;
  const agents = signal?.agents || [];
  const daVeto = agents.find(a => a.name === "DevilsAdvocate");
  const activeAgents = agents.filter(a => a.name !== "DevilsAdvocate");

  const riskSummary = signal?.risk_check;
  const allocationPct = riskSummary?.approved_size_pct
    ? (riskSummary.approved_size_pct * 100).toFixed(1)
    : null;

  const tagColor = (tag) => {
    if (tag.includes("CONSENSUS") || tag.includes("Signal")) return "text-purple-400";
    if (tag.includes("ERROR") || tag.includes("VETO"))  return "text-rose-400";
    if (tag.includes("SUCCESS") || tag.includes("BarClosed")) return "text-emerald-400";
    if (tag.includes("EXEC"))  return "text-blue-400";
    return "text-blue-400";
  };

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-50 font-mono p-4 md:p-6 flex flex-col gap-6">

      {/* Header */}
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 pb-6 border-b border-neutral-800">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 bg-blue-600/20 text-blue-500 rounded-lg flex items-center justify-center border border-blue-500/30 shadow-[0_0_15px_rgba(59,130,246,0.3)]">
            <Activity className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
              TRADING_OS
              <span className="text-xs bg-blue-600/20 text-blue-400 px-2 py-0.5 rounded border border-blue-500/30">v2.4.1</span>
            </h1>
            <p className="text-xs text-neutral-400">MULTI-AGENT CONSENSUS ENGINE</p>
          </div>
        </div>

        <div className="flex items-center gap-4 text-sm flex-wrap">
          <PairDropdown pairs={pairs} selected={selectedAsset} onSelect={handleSelectPair} />

          <div className="flex flex-col items-end">
            <span className="text-neutral-500 uppercase text-xs">System Time (UTC)</span>
            <span className="font-medium">{time.toISOString().split("T")[1].split(".")[0]}</span>
          </div>

          <div className="flex flex-col items-end">
            <span className="text-neutral-500 uppercase text-xs">Status</span>
            <span className={`flex items-center gap-1.5 ${apiOk ? "text-emerald-400" : "text-rose-400"}`}>
              <span className={`h-2 w-2 rounded-full ${apiOk ? "bg-emerald-400 animate-pulse" : "bg-rose-400"}`} />
              {apiOk ? "ONLINE" : "ERROR"}
            </span>
          </div>
        </div>
      </header>

      {/* ── Price Chart (full width) ── */}
      <PriceChart asset={selectedAsset} source={selectedSource} />

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">

        {/* ── Left Column: Meta Consensus + Devil's Advocate ── */}
        <div className="lg:col-span-4 flex flex-col gap-6">

          {/* Signal Card */}
          <div className="bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden shadow-xl">
            <div className="p-5 border-b border-neutral-800 bg-neutral-900/50 flex justify-between items-center">
              <h2 className="text-sm font-bold text-neutral-300 flex items-center gap-2 uppercase tracking-wider">
                <Brain className="h-4 w-4 text-purple-400" />
                Meta-Agent Consensus
              </h2>
              {analyzing && (
                <span className="text-[10px] text-neutral-500 animate-pulse">analyzing…</span>
              )}
            </div>
            <div className="p-6 flex flex-col items-center justify-center">
              <div className={`text-5xl font-black mb-2 ${actionCls}`}>
                {actionLabel}
              </div>
              <p className="text-sm text-neutral-400 mb-6">
                {signal?.final_decision || "—"}
              </p>

              <div className="w-full space-y-4">
                <div className="flex justify-between items-end">
                  <span className="text-xs text-neutral-500 uppercase">Conviction Score</span>
                  <span className="text-lg font-bold text-white">{conviction.toFixed(1)}%</span>
                </div>
                <div className="h-2 w-full bg-neutral-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-emerald-500 to-emerald-400 transition-all duration-700 shadow-[0_0_10px_rgba(52,211,153,0.5)]"
                    style={{ width: `${conviction}%` }}
                  />
                </div>

                <div className="grid grid-cols-2 gap-4 pt-4 border-t border-neutral-800">
                  <div>
                    <div className="text-xs text-neutral-500 uppercase mb-1">Asset</div>
                    <div className="text-sm font-semibold text-neutral-200">{signal?.asset || selectedAsset}</div>
                  </div>
                  <div>
                    <div className="text-xs text-neutral-500 uppercase mb-1">Equity</div>
                    <div className="text-sm font-semibold text-blue-400">
                      {portfolio.equity > 1000
                        ? `$${(portfolio.equity / 1000).toFixed(1)}k`
                        : `₹${portfolio.equity.toFixed(2)}`}
                    </div>
                  </div>
                  {allocationPct && (
                    <>
                      <div>
                        <div className="text-xs text-neutral-500 uppercase mb-1">Position Size</div>
                        <div className="text-sm font-semibold text-neutral-200">Half-Kelly</div>
                      </div>
                      <div>
                        <div className="text-xs text-neutral-500 uppercase mb-1">Allocation</div>
                        <div className="text-sm font-semibold text-blue-400">{allocationPct}% of Equity</div>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Devil's Advocate */}
          <div className="bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden shadow-xl">
            <div className="p-4 border-b border-neutral-800 bg-neutral-900/50 flex justify-between items-center">
              <h2 className="text-sm font-bold text-neutral-300 flex items-center gap-2 uppercase tracking-wider">
                <ShieldAlert className="h-4 w-4 text-rose-500" />
                Devil's Advocate Auditor
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
              <div className="grid grid-cols-2 gap-2 text-xs">
                {(signal?.agents || [])
                  .filter(a => a.name !== "DevilsAdvocate")
                  .map(a => {
                    const warn = (a.warnings || []).length > 0;
                    return (
                      <div key={a.name} className="flex justify-between p-2 bg-neutral-800/50 rounded">
                        <span className="text-neutral-500">{(AGENT_META[a.name]?.label || a.name).split(" ")[0]}</span>
                        <span className={warn ? "text-amber-400" : signalColor(a.decision)}>
                          {warn ? "WARN" : a.decision}
                        </span>
                      </div>
                    );
                  })}
                <div className="col-span-2 mt-2 pt-2 border-t border-neutral-800 text-neutral-400 text-xs">
                  {daVeto
                    ? <span>DA reasoning: <span className="text-white font-bold">{daVeto.reasoning?.slice(0, 80)}</span></span>
                    : <span>Veto Threshold: ≥85% SELL. Conviction: <span className="text-white font-bold">{conviction.toFixed(1)}%</span></span>
                  }
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ── Middle Column: Agent Cards ── */}
        <div className="lg:col-span-5 flex flex-col gap-4">
          <div className="flex items-center gap-2 mb-2">
            <Cpu className="h-5 w-5 text-neutral-400" />
            <h2 className="text-lg font-bold text-white tracking-tight">
              Active Agents ({activeAgents.length}/4)
            </h2>
          </div>

          {activeAgents.length > 0
            ? activeAgents.map(a => <AgentCard key={a.name} agent={a} />)
            : Object.keys(AGENT_META).map(name => (
                <div key={name} className="bg-neutral-900 border border-neutral-800 p-4 rounded-xl opacity-40">
                  <div className="flex items-center gap-3">
                    {React.createElement(AGENT_META[name].icon, { className: `h-5 w-5 text-neutral-500` })}
                    <span className="text-sm text-neutral-500">{AGENT_META[name].label}</span>
                    <span className="text-[10px] text-neutral-600 ml-auto">waiting…</span>
                  </div>
                </div>
              ))
          }
        </div>

        {/* ── Right Column: Infrastructure + Terminal ── */}
        <div className="lg:col-span-3 flex flex-col gap-6">

          <div className="bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden">
            <div className="p-4 border-b border-neutral-800 flex justify-between items-center">
              <h2 className="text-sm font-bold text-neutral-300 uppercase">Infrastructure</h2>
              <Server className="h-4 w-4 text-neutral-500" />
            </div>
            <div className="p-4 flex flex-col gap-4">
              <InfraRow icon={Database}  label="PostgreSQL 15"     status="OK"     statusCls="text-emerald-400" />
              <InfraRow icon={Zap}       label="Redis Cache"       status="OK"     statusCls="text-emerald-400" />
              <InfraRow icon={Globe}     label="Cloudflare Tunnel" status="SECURE" statusCls="text-emerald-400" />
              <InfraRow icon={Lock}      label="API Gateway"       status={apiOk ? "OK" : "DOWN"} statusCls={apiOk ? "text-emerald-400" : "text-rose-400"} />

              {/* RAM */}
              <div className="mt-2 pt-4 border-t border-neutral-800 space-y-2">
                <div className="flex justify-between items-end">
                  <span className="text-[10px] text-neutral-500 uppercase">MacBook Air M1 RAM</span>
                  <span className="text-[10px] text-neutral-300">
                    {sysMetrics.ram_used_gb.toFixed(1)} / {sysMetrics.ram_total_gb.toFixed(1)} GB
                  </span>
                </div>
                <ProgressBar pct={sysMetrics.ram_pct} color={sysMetrics.ram_pct > 85 ? "bg-rose-500" : "bg-blue-500"} />

                <div className="flex justify-between items-end pt-1">
                  <span className="text-[10px] text-neutral-500 uppercase">CPU</span>
                  <span className="text-[10px] text-neutral-300">{sysMetrics.cpu_pct?.toFixed(0) || 0}%</span>
                </div>
                <ProgressBar pct={sysMetrics.cpu_pct || 0} color={sysMetrics.cpu_pct > 80 ? "bg-amber-500" : "bg-emerald-500"} />

                <div className="flex justify-between items-end pt-1">
                  <span className="text-[10px] text-neutral-500 uppercase">Disk</span>
                  <span className="text-[10px] text-neutral-300">{sysMetrics.disk_pct?.toFixed(0) || 0}%</span>
                </div>
                <ProgressBar pct={sysMetrics.disk_pct || 0} color="bg-neutral-500" />
              </div>
            </div>
          </div>

          {/* Live Terminal */}
          <div className="bg-neutral-950 border border-neutral-800 rounded-xl flex flex-col overflow-hidden flex-grow" style={{ minHeight: "220px" }}>
            <div className="p-3 border-b border-neutral-800 flex items-center gap-2 bg-neutral-900">
              <Terminal className="h-4 w-4 text-neutral-500" />
              <h2 className="text-xs font-bold text-neutral-400 uppercase">Live Output</h2>
            </div>
            <div className="p-3 text-[10px] text-neutral-500 font-mono overflow-y-auto space-y-1 flex-grow">
              {events.length === 0 && (
                <p className="text-neutral-600">Waiting for events…</p>
              )}
              {events.slice(0, 30).map((e, i) => (
                <p key={i}>
                  <span className={tagColor(e.tag)}>[{e.tag.toUpperCase().slice(0, 12)}]</span>{" "}
                  <span className="text-neutral-400">{e.msg}</span>
                </p>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
