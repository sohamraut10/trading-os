import pandas as pd
from typing import List, Dict, Any
from core.backtest.costs import OptionsFrictions

class SingleLegOptionsEngine:
    def __init__(self, frictions: OptionsFrictions, initial_capital=1000000.0):
        self.frictions = frictions
        self.initial_capital = initial_capital
        self.capital = initial_capital
        
        self.open_positions: List[Dict[str, Any]] = []
        self.trade_log: List[Dict[str, Any]] = []
        self.equity_curve = []
        
    def execute_trade(self, ts, symbol, expiry, strike, opt_type, side, qty, mid_price, iv=0, delta=0):
        """
        Executes a trade, applying slippage and recording statuatory costs.
        """
        fill_price = self.frictions.apply_slippage(mid_price, side)
        fees = self.frictions.compute_trade_costs(fill_price, qty, side)
        
        self.capital -= fees
        
        position = {
            'ts': ts,
            'symbol': symbol,
            'expiry': expiry,
            'strike': strike,
            'opt_type': opt_type,
            'side': side,
            'qty': qty,
            'entry_price': fill_price,
            'entry_iv': iv,
            'entry_delta': delta,
            'fees_paid': fees
        }
        self.open_positions.append(position)
        return position
        
    def close_position(self, position, ts, mid_price, reason="Exit Signal"):
        """
        Closes an open leg.
        """
        side_to_close = 'sell' if position['side'] == 'buy' else 'buy'
        fill_price = self.frictions.apply_slippage(mid_price, side_to_close)
        
        fees = self.frictions.compute_trade_costs(fill_price, position['qty'], side_to_close)
        self.capital -= fees
        
        if position['side'] == 'buy':
            pnl = (fill_price - position['entry_price']) * position['qty']
        else:
            pnl = (position['entry_price'] - fill_price) * position['qty']
            
        self.capital += pnl
        
        closed_trade = {
            **position,
            'exit_ts': ts,
            'exit_price': fill_price,
            'pnl': pnl,
            'total_fees': position['fees_paid'] + fees,
            'net_pnl': pnl - (position['fees_paid'] + fees),
            'reason': reason
        }
        self.trade_log.append(closed_trade)
        self.open_positions.remove(position)
        
        return closed_trade
        
    def handle_expiry_settlement(self, position, settlement_spot):
        """
        Settles positions on expiry. ITM options settle at intrinsic, OTM expire worthless.
        """
        if position['opt_type'] == 'CE':
            intrinsic = max(0.0, settlement_spot - position['strike'])
        else:
            intrinsic = max(0.0, position['strike'] - settlement_spot)
            
        # Settlement typically has exchange STT on ITM longs, but we abstract heavily here
        # For simplicity, settlement price has zero slippage.
        fees = self.frictions.compute_trade_costs(intrinsic, position['qty'], 'sell' if position['side']=='buy' else 'buy')
        self.capital -= fees
        
        if position['side'] == 'buy':
            pnl = (intrinsic - position['entry_price']) * position['qty']
        else:
            pnl = (position['entry_price'] - intrinsic) * position['qty']
            
        self.capital += pnl
        
        closed_trade = {
            **position,
            'exit_ts': 'EXPIRY',
            'exit_price': intrinsic,
            'pnl': pnl,
            'total_fees': position['fees_paid'] + fees,
            'net_pnl': pnl - (position['fees_paid'] + fees),
            'reason': 'Expiry Settlement'
        }
        self.trade_log.append(closed_trade)
        self.open_positions.remove(position)
        
    def mark_to_market(self, ts, options_snapshot: pd.DataFrame):
        """
        Updates equity curve by MTM all open positions against the latest mid price.
        """
        floating_pnl = 0
        for pos in self.open_positions:
            # Match the contract in the current bar's snapshot
            match = options_snapshot[
                (options_snapshot['strike'] == pos['strike']) & 
                (options_snapshot['opt_type'] == pos['opt_type'])
            ]
            
            if not match.empty:
                current_mid = match.iloc[0]['ltp']  # Proxying mid as LTP
                if pos['side'] == 'buy':
                    floating_pnl += (current_mid - pos['entry_price']) * pos['qty']
                else:
                    floating_pnl += (pos['entry_price'] - current_mid) * pos['qty']
                    
        self.equity_curve.append({
            'ts': ts,
            'equity': self.capital + floating_pnl
        })
