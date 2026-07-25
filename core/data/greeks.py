import numpy as np
from scipy.stats import norm

def black_76_greeks(F, K, T, r, sigma, opt_type='CE'):
    """
    Computes Option Price, IV, and Greeks using the Black-76 model for futures.
    F: Forward/Futures price
    K: Strike
    T: Time to expiry in years
    r: Risk-free rate
    sigma: Implied Volatility
    opt_type: 'CE' (Call) or 'PE' (Put)
    """
    if T <= 0:
        if opt_type == 'CE':
            return max(F - K, 0.0), (1.0 if F > K else 0.0), 0.0, 0.0, 0.0
        else:
            return max(K - F, 0.0), (-1.0 if K > F else 0.0), 0.0, 0.0, 0.0

    d1 = (np.log(F / K) + (0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    discount = np.exp(-r * T)
    
    # PDF for Greeks
    N_prime_d1 = norm.pdf(d1)
    
    if opt_type == 'CE':
        price = discount * (F * norm.cdf(d1) - K * norm.cdf(d2))
        delta = discount * norm.cdf(d1)
        theta = - (discount * F * N_prime_d1 * sigma) / (2 * np.sqrt(T)) + r * discount * (F * norm.cdf(d1) - K * norm.cdf(d2))
    else:
        price = discount * (K * norm.cdf(-d2) - F * norm.cdf(-d1))
        delta = -discount * norm.cdf(-d1)
        theta = - (discount * F * N_prime_d1 * sigma) / (2 * np.sqrt(T)) - r * discount * (K * norm.cdf(-d2) - F * norm.cdf(-d1))

    # Gamma and Vega are identical for CE and PE
    gamma = (discount * N_prime_d1) / (F * sigma * np.sqrt(T))
    vega = discount * F * N_prime_d1 * np.sqrt(T)

    # Convert theta to per-day, vega to 1% change
    theta_per_day = theta / 365.0
    vega_per_percent = vega / 100.0

    return price, delta, gamma, theta_per_day, vega_per_percent

def approximate_iv(F, K, T, r, market_price, opt_type='CE', tol=1e-5, max_iter=100):
    """
    Newton-Raphson to solve for Implied Volatility given the market price.
    """
    if T <= 0:
        return 0.0
        
    sigma = 0.20 # Initial guess: 20%
    for i in range(max_iter):
        price, delta, gamma, theta, vega = black_76_greeks(F, K, T, r, sigma, opt_type)
        diff = price - market_price
        
        if abs(diff) < tol:
            return sigma
            
        if vega == 0.0:
            break
            
        sigma -= diff / (vega * 100) # Since vega returned is /100
        
        # Keep sigma bounded
        if sigma <= 0.001:
            sigma = 0.001
            break
            
    return sigma
