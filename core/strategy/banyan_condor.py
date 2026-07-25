class BanyanCondorSpec:
    """
    Reference Options Specification: Banyan Condor.
    A short iron condor relying on Delta-based strike selection and ATR logic.
    """
    
    def __init__(self, lot_size=50):
        self.lot_size = lot_size
        
        # Strategy Definition
        self.entry_time = "10:00"  # Execute at 10 AM on specific days
        self.dte_target = 3        # Target Days To Expiry for entry
        
        # Leg Definition (Delta-based)
        self.legs = [
            {'side': 'sell', 'qty': self.lot_size, 'opt_type': 'PE', 'strike_selector': '15_delta'},
            {'side': 'sell', 'qty': self.lot_size, 'opt_type': 'CE', 'strike_selector': '15_delta'},
            {'side': 'buy',  'qty': self.lot_size, 'opt_type': 'PE', 'strike_selector': '5_delta'},
            {'side': 'buy',  'qty': self.lot_size, 'opt_type': 'CE', 'strike_selector': '5_delta'}
        ]
        
        # Risk Management Rules
        self.stop_loss_credit_multiplier = 2.0  # Max loss is 2x the net credit received
        self.profit_target_pct = 0.60           # Exit when 60% of max credit is captured
        
    def check_adjustment_trigger(self, net_delta, current_credit, entry_credit, atr):
        """
        Evaluates if the position requires adjustment based on current greeks/price.
        Returns: 'ROLL_UNTESTED', 'CLOSE_ALL', or 'HOLD'
        """
        # Stop loss check
        if current_credit <= -(entry_credit * self.stop_loss_credit_multiplier):
            return 'CLOSE_ALL'
            
        # Take profit check
        if current_credit >= (entry_credit * self.profit_target_pct):
            return 'CLOSE_ALL'
            
        # Net Delta skew check (e.g. if net delta exceeds a threshold, roll the untested side)
        if abs(net_delta) > (10 * self.lot_size): # highly arbitrary threshold for example
            return 'ROLL_UNTESTED'
            
        return 'HOLD'
