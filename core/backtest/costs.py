class InvalidRunError(Exception):
    pass

class OptionsFrictions:
    def __init__(self, brokerage_per_order=20.0, exchange_tx_bps=5.0, 
                 stt_sell_bps=12.5, stamp_bps=0.3, gst_pct=18.0, 
                 slippage_ticks=1, tick_size=0.05):
        
        # Zero-friction runs are marked INVALID
        if brokerage_per_order == 0 and exchange_tx_bps == 0 and stt_sell_bps == 0 and slippage_ticks == 0:
            raise InvalidRunError("Costs and slippage cannot be zero; zero-friction runs are marked INVALID.")
            
        self.brokerage = brokerage_per_order
        self.exchange_tx_bps = exchange_tx_bps
        self.stt_sell_bps = stt_sell_bps
        self.stamp_bps = stamp_bps
        self.gst_pct = gst_pct
        
        self.slippage_ticks = slippage_ticks
        self.tick_size = tick_size

    def compute_trade_costs(self, price, qty, side):
        """
        Computes statutory and brokerage costs for a single options trade.
        price: Execution price
        qty: Lot size * lots
        side: 'buy' or 'sell'
        """
        turnover = price * qty
        
        exchange_fee = turnover * (self.exchange_tx_bps / 10000.0)
        brokerage = self.brokerage
        
        # GST applies on brokerage + exchange fee
        gst = (brokerage + exchange_fee) * (self.gst_pct / 100.0)
        
        # STT applies only on SELL side for options premium
        stt = turnover * (self.stt_sell_bps / 10000.0) if side == 'sell' else 0.0
        
        # Stamp duty applies only on BUY side
        stamp = turnover * (self.stamp_bps / 10000.0) if side == 'buy' else 0.0
        
        total_fees = exchange_fee + brokerage + gst + stt + stamp
        return total_fees

    def apply_slippage(self, mid_price, side):
        """
        Applies slippage penalty to the execution price based on side.
        """
        penalty = self.slippage_ticks * self.tick_size
        return mid_price + penalty if side == 'buy' else max(0.05, mid_price - penalty)
