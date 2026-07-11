export const initialState = {
  currentCycleId: null,
  cycles: {},
  feedDegraded: false,
  status: "disconnected",
};

export function eventReducer(state, action) {
  switch (action.type) {
    case "SET_STATUS":
      return { ...state, status: action.payload };

    case "ADD_EVENT": {
      const event = action.payload;
      const { cycle_id, type, ts, payload } = event;
      
      const updatedCycles = { ...state.cycles };
      
      // Initialize cycle if not exists
      if (!updatedCycles[cycle_id]) {
        updatedCycles[cycle_id] = {
          cycle_id,
          asset: payload?.asset || "UNKNOWN",
          stages: { Bar: ts, Regime: 0, Strategy: 0, Screening: 0, Debate: 0, Verdict: 0, Sanitize: 0, Call: 0 },
          start_ts: ts,
          screening: {},
          debate: { triggered: false, skipped: false, arguments: [], rebuttals: [], crossExam: null },
          verdict: null,
          risk: null,
          call: null,
          order: null,
        };
      }

      const cycle = { ...updatedCycles[cycle_id] };

      switch (type) {
        case "BarClosed":
          cycle.asset = payload.asset;
          cycle.bar = payload.bar;
          cycle.stages.Bar = ts;
          break;

        case "RegimeUpdated":
          cycle.regime = payload.regime;
          cycle.stages.Regime = ts;
          break;

        case "StrategySelected":
          cycle.strategy = payload.strategy;
          cycle.strategy_reason = payload.reason;
          cycle.hurst = payload.hurst;
          cycle.vol_percentile = payload.vol_percentile;
          cycle.stages.Strategy = ts;
          break;

        case "HypothesisEmitted":
          cycle.hypothesis = payload;
          break;

        case "ScreeningResult":
          cycle.screening = { ...cycle.screening, [payload.name]: payload };
          cycle.stages.Screening = ts;
          break;

        case "DebateTriggered":
          cycle.debate = { ...cycle.debate, triggered: true, reasons: payload.reasons };
          cycle.stages.Debate = ts;
          break;

        case "DebateSkipped":
          cycle.debate = { ...cycle.debate, skipped: true };
          cycle.stages.Debate = ts;
          break;

        case "ArgumentPosted":
          cycle.debate = {
            ...cycle.debate,
            arguments: [...cycle.debate.arguments, payload],
          };
          break;

        case "RebuttalPosted":
          cycle.debate = {
            ...cycle.debate,
            rebuttals: [...cycle.debate.rebuttals, payload],
          };
          break;

        case "CrossExam":
          cycle.debate = { ...cycle.debate, crossExam: payload };
          break;

        case "VerdictReached":
          cycle.verdict = payload;
          cycle.stages.Verdict = ts;
          break;

        case "VetoRaised":
          cycle.veto = payload;
          break;

        case "SanitizationApplied":
          cycle.risk = payload;
          cycle.stages.Sanitize = ts;
          break;

        case "FinalCall":
          cycle.call = payload;
          cycle.stages.Call = ts;
          break;

        case "OrderPlaced":
          cycle.order = payload;
          break;

        case "FeedDegraded":
          return { ...state, feedDegraded: true };

        default:
          break;
      }

      updatedCycles[cycle_id] = cycle;

      return {
        ...state,
        currentCycleId: cycle_id,
        cycles: updatedCycles,
        feedDegraded: type === "BarClosed" ? false : state.feedDegraded,
      };
    }

    case "HYDRATE_CYCLE_EVENTS": {
      const { cycle_id, events } = action.payload;
      const updatedCycles = { ...state.cycles };
      
      // Delete old cycle state to clean replay
      delete updatedCycles[cycle_id];
      
      let tempState = { ...state, cycles: updatedCycles };
      for (const ev of events) {
        tempState = eventReducer(tempState, { type: "ADD_EVENT", payload: ev });
      }
      return { ...tempState, currentCycleId: cycle_id };
    }

    default:
      return state;
  }
}
