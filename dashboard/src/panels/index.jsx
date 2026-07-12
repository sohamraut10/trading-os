import React from "react";
import {
  TrendingUp, TrendingDown, AlertTriangle, ShieldAlert,
  Play, Activity, Layers, HelpCircle, UserCheck,
  Search, X, Zap, ChevronRight
} from "lucide-react";
import { fetchOptionExpiries, fetchOptionChain } from "../api";
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip } from "recharts";

// 1. Pipeline Ticker
export function PipelineTicker({ cycle }) {
  if (!cycle) return <div className="text-slate-500 font-mono text-xs">Awaiting cycle data...</div>;

  const stages = [
    { label: "Bar", key: "Bar" },
    { label: "Regime", key: "Regime" },
    { label: "Strategy", key: "Strategy" },
    { label: "Screening", key: "Screening" },
    { label: "Debate", key: "Debate" },
    { label: "Verdict", key: "Verdict" },
    { label: "Sanitize", key: "Sanitize" },
    { label: "Call", key: "Call" },
  ];

  const getStageStatus = (key) => {
    if (key === "Debate" && cycle.debate?.skipped) return "skipped";
    if (cycle.stages[key] > 0) return "completed";
    return "pending";
  };

  const getDuration = (key) => {
    const ts = cycle.stages[key];
    if (!ts || ts === 0) return "";
    const diff = (ts - cycle.start_ts) * 1000;
    return `${Math.round(diff)}ms`;
  };

  return (
    <div className="flex items-center gap-2 overflow-x-auto py-2 px-4 bg-slate-900/80 border-b border-slate-800 backdrop-blur-md">
      <span className="text-xs font-bold text-slate-400 uppercase tracking-widest flex items-center gap-1">
        <Activity size={14} className="text-indigo-400" /> Pipeline:
      </span>
      <div className="flex items-center gap-4">
        {stages.map((stg, idx) => {
          const status = getStageStatus(stg.key);
          const duration = getDuration(stg.key);

          return (
            <div key={stg.key} className="flex items-center gap-2">
              {idx > 0 && <span className="text-slate-700">→</span>}
              <div className="flex flex-col">
                <div className="flex items-center gap-1.5">
                  <span
                    className={`h-2 w-2 rounded-full ${
                      status === "completed"
                        ? "bg-emerald-400 shadow-lg shadow-emerald-400/50"
                        : status === "skipped"
                        ? "bg-amber-400 shadow-lg shadow-amber-400/50"
                        : "bg-slate-700"
                    }`}
                  />
                  <span
                    className={`text-xs font-mono font-medium ${
                      status === "completed"
                        ? "text-emerald-400"
                        : status === "skipped"
                        ? "text-amber-400"
                        : "text-slate-500"
                    }`}
                  >
                    {stg.label}
                  </span>
                </div>
                {duration && <span className="text-[10px] text-slate-500 font-mono pl-3">{duration}</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 2. Strategy Picker
export function StrategyPicker({ cycle, onPin }) {
  const strategies = [
    { value: "scalping", label: "Scalping" },
    { value: "swing", label: "Swing" },
    { value: "mean_reversion", label: "Mean Reversion" },
    { value: "trend_follow", label: "Trend Following" },
    { value: "arbitrage", label: "Stat Arbitrage" },
  ];

  return (
    <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-5 backdrop-blur-lg">
      <div className="flex justify-between items-center mb-3">
        <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Strategy Layer</span>
        {cycle?.strategy_reason === "User pinned override" && (
          <span className="bg-indigo-500/20 text-indigo-400 border border-indigo-500/30 text-[10px] font-bold px-2 py-0.5 rounded">
            PINNED
          </span>
        )}
      </div>

      <div className="space-y-4">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs text-slate-500">Selected Strategy</label>
          <select
            value={cycle?.strategy || ""}
            onChange={(e) => onPin(e.target.value || null)}
            className="bg-slate-950 border border-slate-800 text-slate-200 text-sm rounded-lg p-2.5 outline-none focus:border-indigo-500"
          >
            <option value="">Auto-Select Strategy</option>
            {strategies.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        {cycle && (
          <div className="bg-slate-950/60 border border-slate-800/50 rounded-lg p-3 space-y-2 text-xs font-mono">
            <div className="flex justify-between">
              <span className="text-slate-500">Hurst Exponent:</span>
              <span className="text-slate-300">{(cycle.hurst || 0.5).toFixed(2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Volatility Pct:</span>
              <span className="text-slate-300">{((cycle.vol_percentile || 0.5) * 100).toFixed(0)}%</span>
            </div>
            <div className="text-slate-400 italic text-[11px] mt-2 border-t border-slate-800/40 pt-2">
              Reason: {cycle.strategy_reason || "Evaluating..."}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// 3. Debate Theater
export function DebateTheater({ cycle }) {
  if (!cycle) return <div className="text-slate-500 font-mono text-center p-8 bg-slate-900/30 border border-slate-800/50 rounded-xl">Awaiting cycle data to run debate...</div>;

  const { debate, screening, veto } = cycle;

  if (debate?.skipped) {
    return (
      <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-8 backdrop-blur-lg flex flex-col items-center justify-center text-center">
        <ShieldAlert size={36} className="text-emerald-400 mb-2" />
        <h3 className="font-bold text-emerald-400 text-lg">Clear Consensus Established</h3>
        <p className="text-xs text-slate-400 mt-1 max-w-sm">
          All agents aligned on direction with high confidence. Skipping structured debate rounds to proceed with direct verification.
        </p>
      </div>
    );
  }

  if (!debate?.triggered) {
    return (
      <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-8 backdrop-blur-lg flex flex-col items-center justify-center text-center">
        <Activity size={36} className="text-slate-600 mb-2" />
        <h3 className="font-bold text-slate-500 text-lg">Awaiting Debate Trigger</h3>
        <p className="text-xs text-slate-500 mt-1 max-w-sm">
          Debate will trigger on contested 2-2 splits, borderline confidence metrics, or strategy selection mismatches.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-6 backdrop-blur-lg space-y-6">
      <div className="flex justify-between items-center border-b border-slate-800/80 pb-3">
        <h3 className="font-bold text-indigo-400 flex items-center gap-2">
          <Layers size={18} /> Structured Debate Theater
        </h3>
        <span className="bg-red-500/20 text-red-400 border border-red-500/30 text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider">
          ACTIVE DEBATE (3 ROUNDS)
        </span>
      </div>

      {/* Round 1: Opening Arguments */}
      <div className="space-y-3">
        <h4 className="text-xs font-bold text-slate-400 uppercase tracking-widest border-l-2 border-indigo-500 pl-2">
          Round 1: Opening Arguments
        </h4>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {debate.arguments.map((arg) => (
            <div key={arg.agent_name} className="bg-slate-950 border border-slate-800 rounded-lg p-3 space-y-2">
              <div className="flex justify-between items-center">
                <span className="text-xs font-bold text-slate-300">{arg.agent_name}</span>
                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                  arg.stance === "BUY" ? "bg-emerald-500/10 text-emerald-400" :
                  arg.stance === "SELL" ? "bg-red-500/10 text-red-400" : "bg-amber-500/10 text-amber-400"
                }`}>
                  {arg.stance}
                </span>
              </div>
              <div className="text-[10px] text-slate-500 font-mono">
                Evidence:
                <ul className="list-disc pl-3 mt-1 space-y-0.5">
                  {arg.evidence.map((ev, i) => (
                    <li key={i} className="truncate">
                      {ev.metric}: <span className="text-slate-300">{String(ev.value)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Round 2: Rebuttal & Adjustments */}
      <div className="space-y-3">
        <h4 className="text-xs font-bold text-slate-400 uppercase tracking-widest border-l-2 border-indigo-500 pl-2">
          Round 2: Rebuttals & Re-scoring
        </h4>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {debate.rebuttals.map((reb) => (
            <div key={reb.agent_name} className="bg-slate-950 border border-slate-800 rounded-lg p-3 space-y-2">
              <div className="flex justify-between items-center text-xs">
                <span className="font-bold text-slate-300">{reb.agent_name}</span>
                <span className={`font-mono font-bold ${reb.confidence_delta >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {reb.confidence_delta >= 0 ? "+" : ""}{reb.confidence_delta.toFixed(0)}%
                </span>
              </div>
              <div className="space-y-1">
                <div className="flex justify-between text-[10px] text-slate-500">
                  <span>Re-scored Confidence:</span>
                  <span className="text-slate-300">{reb.confidence.toFixed(0)}%</span>
                </div>
                <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
                  <div
                    className={`h-full ${reb.stance === "BUY" ? "bg-emerald-400" : "bg-red-400"}`}
                    style={{ width: `${reb.confidence}%` }}
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Round 3: Devils Advocate Challenge */}
      {debate.crossExam && (
        <div className="space-y-3">
          <h4 className="text-xs font-bold text-slate-400 uppercase tracking-widest border-l-2 border-indigo-500 pl-2">
            Round 3: DA Cross-Examination
          </h4>
          <div className="bg-red-950/20 border border-red-900/30 rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-2 text-xs font-bold text-red-400">
              <ShieldAlert size={14} /> Risk Audit Challenger Flags:
            </div>
            <div className="flex flex-wrap gap-1.5">
              {debate.crossExam.da_flags.map((flag) => (
                <span key={flag} className="bg-red-950/40 text-red-300 border border-red-900/50 text-[10px] font-mono px-2 py-0.5 rounded">
                  {flag}
                </span>
              ))}
            </div>
            {debate.crossExam.challenged_agents.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
                {debate.crossExam.challenged_agents.map((chg) => (
                  <div key={chg.agent_name} className="flex justify-between items-center text-xs font-mono bg-slate-950 p-2 rounded border border-slate-800/40">
                    <span className="text-slate-400">{chg.agent_name} challenged stance:</span>
                    <span className="text-red-400">{chg.confidence_delta.toFixed(0)}% ({chg.confidence.toFixed(0)}% final)</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-slate-500 font-mono italic">No majority stances met flag conditions to challenge.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// 4. Final Call Card (ConsensusBoard)
export function ConsensusBoard({ cycle }) {
  if (!cycle) return <div className="text-slate-500 font-mono text-center p-6 bg-slate-900/30 border border-slate-800/50 rounded-xl">Awaiting consensus evaluation...</div>;

  const { verdict, risk, call, veto } = cycle;
  const isApproved = risk?.status === "APPROVED" || risk?.status === "SCALED_DOWN";

  return (
    <div className={`bg-slate-900/60 border rounded-xl p-6 backdrop-blur-lg border-slate-800/80 ${
      veto ? "border-red-900/50 bg-red-950/5" : ""
    }`}>
      <div className="flex justify-between items-center border-b border-slate-800/80 pb-3 mb-4">
        <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Final Call Sanitization</span>
        <span className={`text-xs font-bold px-2 py-0.5 rounded ${
          veto ? "bg-red-500/20 text-red-400 border border-red-500/30" :
          isApproved ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30" :
          "bg-slate-500/20 text-slate-400 border border-slate-500/30"
        }`}>
          {veto ? "VETOED" : risk?.status || "PENDING"}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
        <div>
          <span className="text-[10px] text-slate-500 uppercase font-mono block">Consensus Verdict</span>
          <span className={`text-base font-bold font-mono ${
            veto ? "text-red-400 line-through" :
            call?.final_decision === "TRUE SIGNAL" ? "text-emerald-400" : "text-slate-400"
          }`}>
            {veto ? "SELL VETO" : call?.action || "REJECTED"}
          </span>
        </div>
        <div>
          <span className="text-[10px] text-slate-500 uppercase font-mono block">Approved Position Size</span>
          <span className="text-base font-bold font-mono text-indigo-400">
            {isApproved ? `${risk.approved_size_pct ? (risk.approved_size_pct * 100).toFixed(2) : "0.00"}%` : "0.00%"}
          </span>
        </div>
        <div>
          <span className="text-[10px] text-slate-500 uppercase font-mono block">Stop Loss (SL)</span>
          <span className="text-base font-bold font-mono text-red-400">
            {isApproved ? `₹${risk.stop_loss_price?.toLocaleString()}` : "N/A"}
          </span>
        </div>
        <div>
          <span className="text-[10px] text-slate-500 uppercase font-mono block">Take Profit (TP)</span>
          <span className="text-base font-bold font-mono text-emerald-400">
            {isApproved ? `₹${risk.take_profit_price?.toLocaleString()}` : "N/A"}
          </span>
        </div>
      </div>

      {risk?.sanitization_diff && risk.sanitization_diff.length > 0 && (
        <div className="bg-slate-950 border border-slate-800/80 rounded-lg p-3.5 space-y-2">
          <span className="text-[10px] text-slate-500 uppercase font-mono block">Sanitization adjustments (Risk Engine Diff)</span>
          <ul className="text-xs font-mono space-y-1">
            {risk.sanitization_diff.map((diff, i) => (
              <li key={i} className="text-amber-400 flex items-center gap-1.5">
                <span className="h-1 w-1 bg-amber-400 rounded-full" /> {diff}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// 5. Live Signal Feed
export function SignalFeed({ history, onReplay }) {
  return (
    <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-5 backdrop-blur-lg">
      <span className="text-xs font-bold text-slate-400 uppercase tracking-wider block mb-3">Live Signal history Feed</span>
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse text-xs font-mono">
          <thead>
            <tr className="border-b border-slate-800 text-slate-500">
              <th className="pb-2">Time</th>
              <th className="pb-2">Asset</th>
              <th className="pb-2">Strategy</th>
              <th className="pb-2">Stance</th>
              <th className="pb-2">Confidence</th>
              <th className="pb-2 text-right">Size</th>
            </tr>
          </thead>
          <tbody>
            {history.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-center text-slate-600 py-4">No historical signals. Cycles will register here.</td>
              </tr>
            ) : (
              history.map((cycle, i) => {
                const call = cycle.call;
                const isVetoed = !!cycle.veto;
                const isTrue = call?.final_decision === "TRUE SIGNAL";
                
                return (
                  <tr
                    key={i}
                    onClick={() => onReplay(cycle.cycle_id)}
                    className="border-b border-slate-800/40 hover:bg-slate-800/30 cursor-pointer transition"
                  >
                    <td className="py-2.5 text-slate-500">
                      {new Date(cycle.start_ts * 1000).toLocaleTimeString()}
                    </td>
                    <td className="py-2.5 font-bold text-slate-300">{cycle.asset}</td>
                    <td className="py-2.5 text-slate-400">{cycle.strategy}</td>
                    <td className={`py-2.5 font-bold ${
                      isVetoed ? "text-red-400 line-through" :
                      isTrue ? "text-emerald-400" : "text-slate-500"
                    }`}>
                      {isVetoed ? "VETOED" : call?.action || "REJECT"}
                    </td>
                    <td className="py-2.5 text-slate-300">{call?.confidence?.toFixed(0)}%</td>
                    <td className="py-2.5 text-right text-indigo-400">
                      {cycle.risk?.approved_size_pct ? `${(cycle.risk.approved_size_pct * 100).toFixed(1)}%` : "0.0%"}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// 6. Devil's Advocate Veto Log
export function VetoLog({ cycle }) {
  if (!cycle) return null;

  const da = cycle.screening?.DevilsAdvocate;
  const isVetoed = !!cycle.veto;

  return (
    <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-5 backdrop-blur-lg">
      <div className="flex justify-between items-center mb-3">
        <span className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
          <ShieldAlert size={14} className="text-red-400" /> Devil's Advocate Veto Log
        </span>
        {isVetoed && (
          <span className="bg-red-500/20 text-red-400 border border-red-500/30 text-[10px] font-bold px-2 py-0.5 rounded">
            TRIGGERED
          </span>
        )}
      </div>

      <div className="space-y-3">
        {da ? (
          <>
            <div className="flex justify-between items-center text-xs font-mono">
              <span className="text-slate-500">DA Confidence:</span>
              <span className={`font-bold ${isVetoed ? "text-red-400" : "text-slate-300"}`}>
                {da.confidence}%
              </span>
            </div>
            
            <div className="bg-slate-950/60 border border-slate-800/50 rounded-lg p-3 space-y-2">
              <span className="text-[10px] text-slate-500 font-mono uppercase block">Active Audited Flags:</span>
              {da.indicators?.active_risk_flags?.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {da.indicators.active_risk_flags.map((flag) => (
                    <span key={flag} className="bg-red-500/10 text-red-400 border border-red-500/20 text-[10px] font-mono px-2 py-0.5 rounded">
                      {flag}
                    </span>
                  ))}
                </div>
              ) : (
                <span className="text-slate-600 text-xs italic font-mono">No risks detected inside audit environment.</span>
              )}
            </div>
          </>
        ) : (
          <div className="text-slate-600 text-xs italic font-mono">Awaiting DA evaluation metrics...</div>
        )}
      </div>
    </div>
  );
}

// 7. Equity Curve
export function EquityCurve({ data, stats }) {
  return (
    <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-5 backdrop-blur-lg">
      <div className="flex justify-between items-start mb-4">
        <div>
          <span className="text-xs font-bold text-slate-400 uppercase tracking-wider block">Performance Curve</span>
          <span className="text-lg font-bold font-mono text-slate-200">₹{stats?.equity?.toLocaleString() || "1,00,000.00"}</span>
        </div>
        <div className="text-right">
          <span className="text-[10px] text-slate-500 uppercase font-mono block">Day Return</span>
          <span className={`text-xs font-bold font-mono ${stats?.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            {stats?.pnl >= 0 ? "+" : ""}{stats?.pnl?.toFixed(2) || "0.00"}%
          </span>
        </div>
      </div>

      <div className="h-44 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 5, right: 5, left: -25, bottom: 0 }}>
            <defs>
              <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#6366f1" stopOpacity={0.2}/>
                <stop offset="95%" stopColor="#6366f1" stopOpacity={0}/>
              </linearGradient>
            </defs>
            <XAxis dataKey="time" stroke="#475569" fontSize={9} tickLine={false} />
            <YAxis stroke="#475569" fontSize={9} tickLine={false} />
            <Tooltip
              contentStyle={{ backgroundColor: "#0f172a", borderColor: "#334155" }}
              labelStyle={{ color: "#94a3b8" }}
            />
            <Area type="monotone" dataKey="equity" stroke="#6366f1" strokeWidth={1.5} fillOpacity={1} fill="url(#equityGrad)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// 8. Open Positions
export function Positions({ positions }) {
  return (
    <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-5 backdrop-blur-lg">
      <span className="text-xs font-bold text-slate-400 uppercase tracking-wider block mb-3">Open Positions</span>
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse text-xs font-mono">
          <thead>
            <tr className="border-b border-slate-800 text-slate-500">
              <th className="pb-2">Asset</th>
              <th className="pb-2">Qty</th>
              <th className="pb-2">Entry Price</th>
              <th className="pb-2">Value</th>
              <th className="pb-2 text-right">PnL</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr>
                <td colSpan={5} className="text-center text-slate-600 py-4">No active open positions.</td>
              </tr>
            ) : (
              positions.map((pos, idx) => (
                <tr key={idx} className="border-b border-slate-800/40">
                  <td className="py-2.5 font-bold text-slate-300">{pos.asset}</td>
                  <td className="py-2.5 text-slate-400">{pos.qty.toFixed(4)}</td>
                  <td className="py-2.5 text-slate-400">₹{pos.avg_price?.toLocaleString()}</td>
                  <td className="py-2.5 text-slate-300">₹{pos.value?.toLocaleString()}</td>
                  <td className={`py-2.5 text-right font-bold ${pos.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                    {pos.pnl >= 0 ? "+" : ""}{pos.pnl?.toFixed(2)}%
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// 9. Agent Performance
export function AgentWeights({ performance }) {
  return (
    <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-5 backdrop-blur-lg">
      <span className="text-xs font-bold text-slate-400 uppercase tracking-wider block mb-3">Agent Performance & Weights</span>
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse text-xs font-mono">
          <thead>
            <tr className="border-b border-slate-800 text-slate-500">
              <th className="pb-2">Agent</th>
              <th className="pb-2">Accuracy</th>
              <th className="pb-2">Weight</th>
              <th className="pb-2 text-right">Challenge Δ</th>
            </tr>
          </thead>
          <tbody>
            {performance.length === 0 ? (
              <tr>
                <td colSpan={4} className="text-center text-slate-600 py-4">Calculating agent stats...</td>
              </tr>
            ) : (
              performance.map((agent, i) => (
                <tr key={i} className="border-b border-slate-800/40">
                  <td className="py-2.5 text-slate-300 font-bold">{agent.name}</td>
                  <td className="py-2.5 text-emerald-400 font-bold">{agent.accuracy ? `${(agent.accuracy * 100).toFixed(0)}%` : "N/A"}</td>
                  <td className="py-2.5 text-indigo-400">{(agent.weight * 100).toFixed(0)}%</td>
                  <td className="py-2.5 text-right text-slate-400">
                    {agent.avg_delta >= 0 ? "+" : ""}{agent.avg_delta?.toFixed(0)}%
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// 10. Regime Badge + Feed Status
export function RegimeBadge({ cycle, status, degraded, mode = "LIVE" }) {
  const isMock = mode === "MOCK";
  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2 px-3 py-1 bg-slate-950 border border-slate-850 rounded-full">
        <span className="text-[10px] text-slate-500 font-mono uppercase">Mode:</span>
        <span className={`text-xs font-bold uppercase ${isMock ? "text-amber-400" : "text-sky-400"}`}>
          {mode}
        </span>
      </div>

      <div className="flex items-center gap-2 px-3 py-1 bg-slate-950 border border-slate-850 rounded-full">
        <span className="text-[10px] text-slate-500 font-mono uppercase">Regime:</span>
        <span className="text-xs font-bold uppercase text-indigo-400">{cycle?.regime || "sideways"}</span>
      </div>

      <div className="flex items-center gap-2 px-3 py-1 bg-slate-950 border border-slate-850 rounded-full">
        <span className="text-[10px] text-slate-500 font-mono uppercase">Feed Status:</span>
        <span className={`h-2 w-2 rounded-full ${degraded ? "bg-amber-400 shadow-md shadow-amber-400/50" : "bg-emerald-400 shadow-md shadow-emerald-400/50"}`} />
        <span className={`text-[10px] font-mono uppercase font-bold ${degraded ? "text-amber-400" : "text-emerald-400"}`}>
          {degraded ? "Degraded" : "Normal"}
        </span>
      </div>

      <div className="flex items-center gap-2 px-3 py-1 bg-slate-950 border border-slate-850 rounded-full">
        <span className="text-[10px] text-slate-500 font-mono uppercase">WS:</span>
        <span className={`text-xs font-bold uppercase ${status === "connected" ? "text-emerald-400" : "text-red-400"}`}>
          {status}
        </span>
      </div>
    </div>
  );
}

// 11. Pair Selector
export function PairSelector({ suggestions, broker, selectedAsset, onSelect, onAnalyze }) {
  const [query, setQuery] = React.useState("");
  const [searchResults, setSearchResults] = React.useState(null);
  const [searching, setSearching] = React.useState(false);
  const [open, setOpen] = React.useState(false);
  const inputRef = React.useRef();
  const debounceRef = React.useRef();

  const handleQueryChange = (e) => {
    const val = e.target.value;
    setQuery(val);
    setOpen(true);
    clearTimeout(debounceRef.current);
    if (!val.trim()) {
      setSearchResults(null);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await fetch(`/pairs/search?q=${encodeURIComponent(val)}`);
        const data = await res.json();
        setSearchResults(data.pairs || []);
      } catch {
        setSearchResults([]);
      } finally {
        setSearching(false);
      }
    }, 300);
  };

  const clearSearch = () => {
    setQuery("");
    setSearchResults(null);
    setOpen(false);
    inputRef.current?.focus();
  };

  const displayPairs = searchResults !== null ? searchResults : (suggestions || []);

  const typeColor = (type) => {
    if (type === "crypto")  return "text-amber-400 bg-amber-500/10 border-amber-500/20";
    if (type === "index")   return "text-purple-400 bg-purple-500/10 border-purple-500/20";
    if (type === "etf")     return "text-sky-400 bg-sky-500/10 border-sky-500/20";
    if (type === "options") return "text-rose-400 bg-rose-500/10 border-rose-500/20";
    return "text-emerald-400 bg-emerald-500/10 border-emerald-500/20";
  };

  return (
    <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-5 backdrop-blur-lg">
      {/* Header */}
      <div className="flex justify-between items-center mb-4">
        <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Market Selector</span>
        {broker && (
          <span className="text-[10px] font-mono font-bold text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 px-2 py-0.5 rounded uppercase">
            {broker.replace("Broker", "").replace("Paper", "Paper")}
          </span>
        )}
      </div>

      {/* Search input */}
      <div className="relative mb-4">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={handleQueryChange}
          onFocus={() => setOpen(true)}
          placeholder="Search symbol or company…"
          className="w-full bg-slate-950 border border-slate-700 text-slate-200 text-sm rounded-lg pl-8 pr-8 py-2.5 outline-none focus:border-indigo-500 transition font-mono placeholder:text-slate-600"
        />
        {query && (
          <button onClick={clearSearch} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300">
            <X size={13} />
          </button>
        )}
        {searching && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-indigo-400 font-mono">…</span>
        )}
      </div>

      {/* Pair chips grid */}
      <div className="grid grid-cols-2 gap-1.5">
        {displayPairs.length === 0 && query ? (
          <div className="col-span-2 text-center text-slate-600 text-xs font-mono py-3">No results for "{query}"</div>
        ) : (
          displayPairs.slice(0, 10).map((pair) => {
            const isSelected = pair.symbol === selectedAsset;
            return (
              <button
                key={pair.symbol}
                onClick={() => onSelect(pair)}
                className={`flex items-center justify-between gap-1 rounded-lg px-2.5 py-2 border transition text-left ${
                  isSelected
                    ? "bg-indigo-600/20 border-indigo-500/60 text-indigo-300"
                    : "bg-slate-950/60 border-slate-800/60 text-slate-400 hover:border-slate-600 hover:text-slate-200"
                }`}
              >
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-bold font-mono truncate text-slate-200">{pair.symbol}</div>
                  <div className="text-[10px] text-slate-500 truncate">{pair.name}</div>
                </div>
                <div className="flex flex-col items-end gap-1 flex-shrink-0">
                  <span className={`text-[9px] font-mono border rounded px-1 ${typeColor(pair.type)}`}>
                    {pair.type?.toUpperCase() || "EQ"}
                  </span>
                  {isSelected && <span className="h-1.5 w-1.5 rounded-full bg-indigo-400 shadow shadow-indigo-400/60" />}
                </div>
              </button>
            );
          })
        )}
      </div>

      {/* Selected pair + analyze button */}
      {selectedAsset && (
        <div className="mt-4 flex items-center justify-between bg-slate-950/60 border border-indigo-500/20 rounded-lg px-3 py-2.5">
          <div>
            <span className="text-[10px] text-slate-500 font-mono uppercase block">Active pair</span>
            <span className="text-sm font-bold font-mono text-indigo-300">{selectedAsset}</span>
          </div>
          <button
            onClick={() => onAnalyze(selectedAsset)}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold px-3 py-1.5 rounded-lg transition"
          >
            <Zap size={12} /> Analyze
          </button>
        </div>
      )}
    </div>
  );
}


// 12. Options Chain (Zerodha-style)
function fmtOI(n) {
  if (!n || n === 0) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

function fmtExpiry(dateStr) {
  if (!dateStr) return "";
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const parts = dateStr.split("-");
  if (parts.length < 3) return dateStr;
  const day = parseInt(parts[2], 10);
  const mon = parseInt(parts[1], 10) - 1;
  return `${day} ${months[mon]}`;
}

export function OptionsChain({ symbol, onSelectContract }) {
  const [expiries, setExpiries] = React.useState([]);
  const [selectedExpiry, setSelectedExpiry] = React.useState("");
  const [chain, setChain] = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [selectedKey, setSelectedKey] = React.useState(null);
  const [selectedContract, setSelectedContract] = React.useState(null);

  // Load expiries when symbol changes
  React.useEffect(() => {
    if (!symbol) return;
    setExpiries([]);
    setSelectedExpiry("");
    setChain(null);
    setSelectedKey(null);
    setSelectedContract(null);
    fetchOptionExpiries(symbol).then((data) => {
      const list = data.expiries || [];
      setExpiries(list);
      if (list.length > 0) setSelectedExpiry(list[0]);
    });
  }, [symbol]);

  // Load chain when expiry is selected
  React.useEffect(() => {
    if (!symbol || !selectedExpiry) return;
    setLoading(true);
    setChain(null);
    fetchOptionChain(symbol, selectedExpiry).then((data) => {
      setChain(data);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [symbol, selectedExpiry]);

  const spot = chain?.spot || 0;
  const strikes = chain?.strikes || [];

  const handleSelect = (row, type) => {
    const leg = type === "CE" ? row.ce : row.pe;
    const key = `${row.strike}-${type}`;
    setSelectedKey(key);
    const contract = {
      symbol,
      strike: row.strike,
      type,
      security_id: leg.security_id,
      expiry: selectedExpiry,
    };
    setSelectedContract(contract);
    if (onSelectContract) onSelectContract(contract);
  };

  const clearSelection = () => {
    setSelectedKey(null);
    setSelectedContract(null);
  };

  return (
    <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-5 backdrop-blur-lg space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
          {symbol} Options
          {spot > 0 && (
            <span className="ml-3 text-indigo-400 font-mono">₹{spot.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</span>
          )}
        </span>
        {expiries.length > 0 && (
          <select
            value={selectedExpiry}
            onChange={(e) => setSelectedExpiry(e.target.value)}
            className="bg-slate-950 border border-slate-700 text-slate-200 text-xs rounded-lg px-2 py-1.5 outline-none focus:border-indigo-500 font-mono"
          >
            {expiries.map((exp) => (
              <option key={exp} value={exp}>{fmtExpiry(exp)}</option>
            ))}
          </select>
        )}
      </div>

      {/* Column Headers */}
      <div className="grid grid-cols-7 text-[10px] font-mono font-bold text-slate-500 uppercase tracking-wider border-b border-slate-800 pb-1">
        <div className="col-span-1 text-right pr-2">CE OI</div>
        <div className="col-span-1 text-right pr-2">IV%</div>
        <div className="col-span-1 text-right pr-2 text-emerald-500">LTP</div>
        <div className="col-span-1 text-center text-slate-400">Strike</div>
        <div className="col-span-1 text-left pl-2 text-red-500">LTP</div>
        <div className="col-span-1 text-left pl-2">IV%</div>
        <div className="col-span-1 text-left pl-2">PE OI</div>
      </div>

      {/* Rows */}
      <div className="space-y-0 overflow-y-auto max-h-[420px]">
        {loading ? (
          <div className="text-center text-slate-500 font-mono text-xs py-6">Loading chain…</div>
        ) : strikes.length === 0 ? (
          <div className="text-center text-slate-600 font-mono text-xs py-6">
            {selectedExpiry ? "No data available" : "Select an expiry"}
          </div>
        ) : (
          strikes.map((row) => {
            const isATM = spot > 0 && Math.abs(row.strike - spot) === strikes.reduce((min, s) => Math.min(min, Math.abs(s.strike - spot)), Infinity);
            const itmCE = spot > 0 && row.strike < spot;
            const itmPE = spot > 0 && row.strike > spot;
            const ceKey = `${row.strike}-CE`;
            const peKey = `${row.strike}-PE`;
            const ceSelected = selectedKey === ceKey;
            const peSelected = selectedKey === peKey;

            return (
              <div
                key={row.strike}
                className={`grid grid-cols-7 text-xs font-mono border-b border-slate-800/30 ${
                  isATM ? "bg-indigo-500/15 border border-indigo-500/30 rounded" : ""
                }`}
              >
                {/* CE side (3 cols) */}
                <div
                  onClick={() => handleSelect(row, "CE")}
                  className={`col-span-3 grid grid-cols-3 cursor-pointer py-1.5 rounded-l transition ${
                    ceSelected
                      ? "bg-emerald-500/20 ring-1 ring-emerald-500/50"
                      : itmCE
                      ? "bg-emerald-500/8 hover:bg-emerald-500/15"
                      : "hover:bg-slate-800/40"
                  }`}
                >
                  <div className="text-right pr-2 text-slate-300">{fmtOI(row.ce?.oi)}</div>
                  <div className="text-right pr-2 text-slate-400">
                    {row.ce?.iv ? row.ce.iv.toFixed(1) : "—"}
                  </div>
                  <div className="text-right pr-2 text-emerald-400 font-semibold">
                    {row.ce?.ltp ? row.ce.ltp.toFixed(2) : "—"}
                  </div>
                </div>

                {/* Strike (center) */}
                <div className="col-span-1 text-center py-1.5 font-bold text-slate-200">
                  {row.strike % 1 === 0 ? row.strike.toFixed(0) : row.strike.toFixed(1)}
                </div>

                {/* PE side (3 cols) */}
                <div
                  onClick={() => handleSelect(row, "PE")}
                  className={`col-span-3 grid grid-cols-3 cursor-pointer py-1.5 rounded-r transition ${
                    peSelected
                      ? "bg-red-500/20 ring-1 ring-red-500/50"
                      : itmPE
                      ? "bg-red-500/8 hover:bg-red-500/15"
                      : "hover:bg-slate-800/40"
                  }`}
                >
                  <div className="text-left pl-2 text-red-400 font-semibold">
                    {row.pe?.ltp ? row.pe.ltp.toFixed(2) : "—"}
                  </div>
                  <div className="text-left pl-2 text-slate-400">
                    {row.pe?.iv ? row.pe.iv.toFixed(1) : "—"}
                  </div>
                  <div className="text-left pl-2 text-slate-300">{fmtOI(row.pe?.oi)}</div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Selected contract footer */}
      {selectedContract && (
        <div className="flex items-center justify-between bg-slate-950/60 border border-indigo-500/20 rounded-lg px-3 py-2 mt-1">
          <span className="text-xs font-mono text-indigo-300">
            Selected: {selectedContract.symbol} {selectedContract.strike} {selectedContract.type}
            {selectedContract.type === "CE"
              ? chain?.strikes?.find((s) => s.strike === selectedContract.strike)?.ce?.ltp
                ? ` · ₹${chain.strikes.find((s) => s.strike === selectedContract.strike).ce.ltp.toFixed(2)}`
                : ""
              : chain?.strikes?.find((s) => s.strike === selectedContract.strike)?.pe?.ltp
              ? ` · ₹${chain.strikes.find((s) => s.strike === selectedContract.strike).pe.ltp.toFixed(2)}`
              : ""}
          </span>
          <button
            onClick={clearSelection}
            className="text-slate-500 hover:text-slate-300 ml-3"
          >
            <X size={13} />
          </button>
        </div>
      )}
    </div>
  );
}

// 13. TradingView Price Chart
import { createChart } from "lightweight-charts";

export function PriceChart({ cycle, candles }) {
  const chartContainerRef = React.useRef();
  const chartRef = React.useRef();
  const candlestickSeriesRef = React.useRef();

  React.useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth || 400,
      height: 300,
      layout: {
        background: { type: 'solid', color: '#0f172a' },
        textColor: "#94a3b8",
      },
      grid: {
        vertLines: { color: "#1e293b" },
        horzLines: { color: "#1e293b" },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const candlestickSeries = chart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });

    chartRef.current = chart;
    candlestickSeriesRef.current = candlestickSeries;

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth || 400 });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, []);

  React.useEffect(() => {
    if (!candlestickSeriesRef.current || !candles || candles.length === 0) return;

    const formatted = candles
      .filter((c) => c && (c.timestamp || c.time))
      .map((c) => ({
        time: Math.round(c.timestamp || c.time),
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
      }))
      .sort((a, b) => a.time - b.time);

    // Remove duplicates
    const unique = [];
    const seen = new Set();
    for (const item of formatted) {
      if (!seen.has(item.time)) {
        seen.add(item.time);
        unique.push(item);
      }
    }

    candlestickSeriesRef.current.setData(unique);
  }, [candles]);

  React.useEffect(() => {
    if (!candlestickSeriesRef.current || !cycle) return;

    const markers = [];
    if (cycle.call) {
      const isVetoed = !!cycle.veto;
      const isTrue = cycle.call.final_decision === "TRUE SIGNAL";
      const ts = Math.round(cycle.bar?.timestamp || cycle.start_ts);

      if (isVetoed) {
        markers.push({
          time: ts,
          position: "aboveBar",
          color: "#ef4444",
          shape: "arrowDown",
          text: "DA VETO",
        });
      } else if (isTrue && cycle.call.action === "BUY") {
        markers.push({
          time: ts,
          position: "belowBar",
          color: "#10b981",
          shape: "arrowUp",
          text: "BUY ENTRY",
        });
      } else if (isTrue && cycle.call.action === "SELL") {
        markers.push({
          time: ts,
          position: "aboveBar",
          color: "#ef4444",
          shape: "arrowDown",
          text: "SELL ENTRY",
        });
      }
    }
    candlestickSeriesRef.current.setMarkers(markers);
  }, [cycle]);

  return (
    <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl p-5 backdrop-blur-lg space-y-3">
      <span className="text-xs font-bold text-slate-400 uppercase tracking-wider block">
        Live Market Feed — {cycle?.asset || "BTCUSDT"}
      </span>
      <div ref={chartContainerRef} className="w-full h-[300px]" />
    </div>
  );
}
