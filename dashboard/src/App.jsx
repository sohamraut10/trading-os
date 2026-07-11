import React, { useReducer, useEffect, useState } from "react";
import { 
  initialState, eventReducer 
} from "./eventReducer";
import { connectWS } from "./ws";
import { 
  fetchPortfolio, fetchAgentPerformance, pinStrategy, fetchCycleEvents, fetchCandles 
} from "./api";
import {
  PipelineTicker, StrategyPicker, DebateTheater, ConsensusBoard,
  SignalFeed, VetoLog, EquityCurve, Positions, AgentWeights, RegimeBadge, PriceChart
} from "./panels";

export default function App() {
  const [state, dispatch] = useReducer(eventReducer, initialState);
  const [selectedCycleId, setSelectedCycleId] = useState(null);
  
  const [portfolioStats, setPortfolioStats] = useState({ equity: 100000.0, cash: 100000.0, pnl: 0.0 });
  const [openPositions, setOpenPositions] = useState([]);
  const [agentStats, setAgentStats] = useState([]);
  const [equityCurveData, setEquityCurveData] = useState([{ time: "0", equity: 1.0 }]);
  const [candles, setCandles] = useState([]);
  const [mode, setMode] = useState("LIVE");

  // Active displayed cycle is either the selected one or the latest current cycle
  const activeCycleId = selectedCycleId || state.currentCycleId;
  const activeCycle = state.cycles[activeCycleId];

  useEffect(() => {
    const asset = activeCycle?.asset || "BTCUSDT";
    async function loadCandles() {
      try {
        const list = await fetchCandles(asset);
        setCandles(list);
      } catch (err) {
        console.error("Failed to load candles:", err);
      }
    }
    loadCandles();
  }, [activeCycle?.asset]);

  // 1. WebSocket Event Stream Connection
  useEffect(() => {
    const wsManager = connectWS(
      (event) => {
        dispatch({ type: "ADD_EVENT", payload: event });
      },
      (status) => {
        dispatch({ type: "SET_STATUS", payload: status });
      }
    );
    return () => wsManager.close();
  }, []);

  // 2. Poll Portfolio, Positions, and Agent Performance every 5 seconds
  useEffect(() => {
    async function loadData() {
      try {
        const port = await fetchPortfolio();
        setPortfolioStats({
          equity: port.equity,
          cash: port.cash,
          pnl: port.daily_pnl_pct * 100,
        });
        if (port.mode) {
          setMode(port.mode);
        }

        // Convert open positions mapping to array
        const posArr = Object.entries(port.positions).map(([symbol, data]) => ({
          asset: symbol,
          qty: data.qty,
          avg_price: data.avg_price,
          value: data.value,
          pnl: data.unrealized_pnl || 0.0,
        }));
        setOpenPositions(posArr);

        // Fetch agent accuracy & weights
        const agents = await fetchAgentPerformance();
        const agentArr = Object.entries(agents).map(([name, data]) => ({
          name,
          accuracy: data.accuracy,
          weight: data.weight,
          avg_delta: data.avg_confidence_delta || 0.0,
        }));
        setAgentStats(agentArr);

      } catch (err) {
        console.error("Error fetching status updates:", err);
      }
    }

    loadData();
    const interval = setInterval(loadData, 5000);
    return () => clearInterval(interval);
  }, []);

  // 3. Track equity curve historically when cycles complete
  useEffect(() => {
    const allCycles = Object.values(state.cycles).sort((a, b) => a.start_ts - b.start_ts);
    if (allCycles.length > 0) {
      const curve = allCycles.map((cycle, i) => ({
        time: new Date(cycle.start_ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        equity: cycle.risk?.approved_size_pct ? 1.0 + (cycle.risk.approved_size_pct * (cycle.call?.action === "BUY" ? 0.02 : -0.02)) : 1.0
      }));
      setEquityCurveData(curve);
    }
  }, [state.cycles]);

  // 4. Handle Replaying/Hydrating Historical Cycles
  const handleReplayCycle = async (cycleId) => {
    setSelectedCycleId(cycleId);
    try {
      const events = await fetchCycleEvents(cycleId);
      dispatch({
        type: "HYDRATE_CYCLE_EVENTS",
        payload: { cycle_id: cycleId, events },
      });
    } catch (err) {
      console.error("Replay hydration failed:", err);
    }
  };

  // 5. Pin strategy handler
  const handlePinStrategy = async (strategyVal) => {
    try {
      await pinStrategy(strategyVal);
    } catch (err) {
      console.error("Failed to pin strategy:", err);
    }
  };

  // Convert cycles history to array
  const signalHistory = Object.values(state.cycles)
    .filter((c) => c.call !== null)
    .sort((a, b) => b.start_ts - a.start_ts);

  return (
    <div className="min-h-screen bg-[#030712] text-slate-100 flex flex-col antialiased">
      {/* Header Bar */}
      <header className="flex justify-between items-center px-6 py-4 bg-slate-950 border-b border-slate-900 sticky top-0 z-50">
        <div className="flex items-center gap-3">
          <div className="bg-indigo-600 text-white h-8 w-8 rounded-lg flex items-center justify-center font-extrabold text-sm tracking-wider">
            T
          </div>
          <div>
            <h1 className="text-base font-bold text-slate-200 uppercase tracking-widest">Trading OS</h1>
            <span className="text-[10px] text-slate-500 font-mono">Consensus Decision Theater</span>
          </div>
        </div>

        {/* Global Badges */}
        <RegimeBadge 
          cycle={activeCycle} 
          status={state.status} 
          degraded={state.feedDegraded} 
          mode={mode}
        />
      </header>

      {/* Topstrip stage tracker */}
      <PipelineTicker cycle={activeCycle} />

      {/* Primary Workspace Grid */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Left Column: Strategy controls & stats */}
        <div className="space-y-6 lg:col-span-1">
          <StrategyPicker 
            cycle={activeCycle} 
            onPin={handlePinStrategy} 
          />

          <EquityCurve 
            data={equityCurveData} 
            stats={portfolioStats} 
          />

          <Positions positions={openPositions} />

          <AgentWeights performance={agentStats} />
        </div>

        {/* Center/Right Columns: Core Decision Theater */}
        <div className="space-y-6 lg:col-span-2">
          {/* Live Price Chart */}
          <PriceChart cycle={activeCycle} candles={candles} />

          {/* Verdict output */}
          <ConsensusBoard cycle={activeCycle} />

          {/* Structured Debate Rounds */}
          <DebateTheater cycle={activeCycle} />

          {/* Veto audit logs */}
          <VetoLog cycle={activeCycle} />

          {/* Signal history logs */}
          <SignalFeed 
            history={signalHistory} 
            onReplay={handleReplayCycle} 
          />
        </div>
      </main>
    </div>
  );
}
