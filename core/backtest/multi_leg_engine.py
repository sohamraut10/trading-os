import pandas as pd
from typing import List, Dict, Any
from core.backtest.options_engine import SingleLegOptionsEngine
from core.backtest.costs import OptionsFrictions

class MultiLegEngine(SingleLegOptionsEngine):
    def __init__(self, frictions: OptionsFrictions, initial_capital=1000000.0, margin_per_lot=150000.0, min_oi=10000):
        super().__init__(frictions, initial_capital)
        self.margin_per_lot = margin_per_lot  # Highly simplified SPAN+exposure approximation
        self.min_oi = min_oi
        
        self.multi_leg_positions: List[Dict[str, Any]] = []
        
    def execute_multi_leg(self, ts, legs: List[Dict[str, Any]], snapshot: pd.DataFrame):
        """
        Executes a complex options strategy (e.g., Iron Condor).
        legs format: [{'side': 'buy', 'qty': 50, 'opt_type': 'CE', 'strike_selector': '20_delta'}]
        """
        # 1. Resolve Strikes and Check Liquidity
        resolved_legs = []
        for leg in legs:
            target = self._resolve_strike(leg['strike_selector'], leg['opt_type'], snapshot)
            if not target:
                print(f"[{ts}] Rejected: Could not resolve strike for {leg['strike_selector']}")
                return None
                
            if target['oi'] < self.min_oi:
                print(f"[{ts}] Rejected: Strike {target['strike']} failed liquidity filter (OI: {target['oi']})")
                return None
                
            resolved_legs.append({**leg, 'target': target})
            
        # 2. Margin Check
        total_short_lots = sum(l['qty'] for l in resolved_legs if l['side'] == 'sell') / 50.0  # Assuming 50 lot size
        req_margin = total_short_lots * self.margin_per_lot
        if req_margin > self.capital:
            print(f"[{ts}] Rejected: Margin exceeded. Required: {req_margin}, Available: {self.capital}")
            return None
            
        # 3. Execute
        executed_legs = []
        net_credit = 0.0
        
        for leg in resolved_legs:
            t = leg['target']
            mid_price = (t['bid'] + t['ask']) / 2.0
            
            # Use parent engine's execution to handle frictions natively
            pos = self.execute_trade(ts, t['symbol'], t['expiry'], t['strike'], 
                                     t['opt_type'], leg['side'], leg['qty'], mid_price, 
                                     t.get('iv', 0), t.get('delta', 0))
            executed_legs.append(pos)
            
            # Net credit math
            if leg['side'] == 'sell':
                net_credit += (pos['entry_price'] * pos['qty']) - pos['fees_paid']
            else:
                net_credit -= (pos['entry_price'] * pos['qty']) + pos['fees_paid']
                
        multi_pos = {
            'id': f"condor_{ts.timestamp()}",
            'entry_ts': ts,
            'legs': executed_legs,
            'net_credit': net_credit,
            'status': 'open'
        }
        self.multi_leg_positions.append(multi_pos)
        return multi_pos
        
    def _resolve_strike(self, selector: str, opt_type: str, snapshot: pd.DataFrame):
        """
        Resolves a string selector into an actual chain row.
        Example selectors: '15_delta', 'ATM+300', '~40_prem'
        """
        df = snapshot[snapshot['opt_type'] == opt_type].copy()
        if df.empty: return None
        
        if selector.endswith('_delta'):
            target_delta = float(selector.split('_')[0]) / 100.0
            # Assuming dataframe has a 'delta' column from greeks.py
            if 'delta' in df.columns:
                df['delta_dist'] = abs(abs(df['delta']) - target_delta)
                return df.loc[df['delta_dist'].idxmin()].to_dict()
        
        elif selector.endswith('_prem'):
            target_prem = float(selector.split('_')[0].replace('~', ''))
            df['prem_dist'] = abs(df['ltp'] - target_prem)
            return df.loc[df['prem_dist'].idxmin()].to_dict()
            
        # Fallback naive logic for demo
        return df.iloc[0].to_dict()

    def mark_multi_leg(self, ts, snapshot: pd.DataFrame):
        """
        Computes net greeks and updates MTM for the multi-leg portfolio.
        """
        # Under the hood, the parent single-leg engine tracks total equity.
        self.mark_to_market(ts, snapshot)
        
        # We can also yield net greeks for the strategy
        net_greeks = []
        for pos in self.multi_leg_positions:
            net_delta = 0
            for leg in pos['legs']:
                match = snapshot[(snapshot['strike'] == leg['strike']) & (snapshot['opt_type'] == leg['opt_type'])]
                if not match.empty and 'delta' in match.columns:
                    sign = 1 if leg['side'] == 'buy' else -1
                    net_delta += match.iloc[0]['delta'] * leg['qty'] * sign
            net_greeks.append({'id': pos['id'], 'net_delta': net_delta})
            
        return net_greeks
