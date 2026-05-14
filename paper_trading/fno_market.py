"""
F&O pricing engine — no external dependencies beyond stdlib.

Options:  Black-Scholes with assumed IV
Futures:  Cost-of-carry  F = S × e^(r × T)
Greeks:   Delta, Gamma, Theta (per day), Vega (per 1 % IV move)
"""
import math
import datetime

RISK_FREE_RATE = 0.07          # 7 % annualised (approx RBI repo)
FUTURES_MARGIN_PCT = 0.15      # 15 % SPAN margin on notional
OPTIONS_SELL_MARGIN_PCT = 0.15 # margin for option writers

# Default implied volatility — rough NSE estimates
_IV = {
    'NIFTY': 0.14, 'BANKNIFTY': 0.18, 'FINNIFTY': 0.20,
    'MIDCPNIFTY': 0.22, 'SENSEX': 0.14, 'BANKEX': 0.18,
    'RELIANCE': 0.28, 'TCS': 0.26, 'INFY': 0.28, 'HDFCBANK': 0.30,
    'ICICIBANK': 0.30, 'SBIN': 0.32, 'AXISBANK': 0.30, 'TATAMOTORS': 0.38,
    'BAJFINANCE': 0.32, 'BHARTIARTL': 0.28, 'WIPRO': 0.30, 'HCLTECH': 0.28,
    'KOTAKBANK': 0.28, 'LT': 0.26, 'MARUTI': 0.26,
}
DEFAULT_IV = 0.35  # for unknown single stocks


def get_iv(underlying: str) -> float:
    return _IV.get(underlying.upper(), DEFAULT_IV)


# ── Normal CDF (Horner's method, no scipy needed) ─────────────────────────────

def _ncdf(x: float) -> float:
    """Cumulative standard normal distribution."""
    a = (0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429)
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    poly = t * (a[0] + t * (a[1] + t * (a[2] + t * (a[3] + t * a[4]))))
    return 0.5 * (1.0 + sign * (1.0 - poly * math.exp(-x * x)))


def _npdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


# ── Black-Scholes ─────────────────────────────────────────────────────────────

def black_scholes(S: float, K: float, T: float, r: float, sigma: float,
                  option_type: str) -> float:
    """
    Returns theoretical option price.
    S: spot, K: strike, T: years to expiry, r: risk-free rate, sigma: IV
    option_type: 'CE' or 'PE'
    """
    if T <= 0:
        return max(S - K, 0) if option_type == 'CE' else max(K - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == 'CE':
        price = S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    else:
        price = K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)
    return max(0.05, round(price, 2))


def greeks(S: float, K: float, T: float, r: float, sigma: float,
           option_type: str) -> dict:
    if T <= 0:
        return {'delta': (1.0 if option_type == 'CE' else -1.0),
                'gamma': 0.0, 'theta': 0.0, 'vega': 0.0}
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    delta = _ncdf(d1) if option_type == 'CE' else _ncdf(d1) - 1.0
    gamma = _npdf(d1) / (S * sigma * math.sqrt(T))
    # Theta per calendar day
    theta_base = -(S * _npdf(d1) * sigma) / (2 * math.sqrt(T))
    if option_type == 'CE':
        theta = (theta_base - r * K * math.exp(-r * T) * _ncdf(d2)) / 365
    else:
        theta = (theta_base + r * K * math.exp(-r * T) * _ncdf(-d2)) / 365
    vega = S * math.sqrt(T) * _npdf(d1) * 0.01  # per 1 % IV change

    return {
        'delta': round(delta, 4),
        'gamma': round(gamma, 6),
        'theta': round(theta, 4),
        'vega':  round(vega, 4),
    }


# ── Main pricing entry-point ───────────────────────────────────────────────────

def get_fno_price(underlying: str, instrument_type: str,
                  strike: float | None, expiry: datetime.date,
                  spot: float) -> dict:
    """
    Returns a pricing dict for any F&O instrument.
    instrument_type: 'CE' | 'PE' | 'FUT'
    """
    today = datetime.date.today()
    days  = max((expiry - today).days, 0)
    T     = days / 365.0

    if instrument_type == 'FUT':
        price = round(spot * math.exp(RISK_FREE_RATE * T), 2)
        return {
            'price': price,
            'spot':  round(spot, 2),
            'days':  days,
            'type':  'FUT',
        }

    sigma = get_iv(underlying)
    price = black_scholes(spot, strike, T, RISK_FREE_RATE, sigma, instrument_type)
    g     = greeks(spot, strike, T, RISK_FREE_RATE, sigma, instrument_type)
    return {
        'price': price,
        'spot':  round(spot, 2),
        'days':  days,
        'type':  instrument_type,
        'iv':    round(sigma * 100, 1),
        **g,
    }


def get_margin(instrument_type: str, order_type: str,
               spot: float, price: float,
               lots: int, lot_size: int) -> float:
    """Capital blocked for entering the position."""
    if instrument_type == 'FUT':
        return round(price * lot_size * lots * FUTURES_MARGIN_PCT, 2)
    if order_type == 'BUY':
        return round(price * lot_size * lots, 2)   # full premium
    return round(spot * lot_size * lots * OPTIONS_SELL_MARGIN_PCT, 2)


# ── Expiry date helpers ────────────────────────────────────────────────────────

def _last_thursday(year: int, month: int) -> datetime.date:
    """Last Thursday of the given month."""
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    d = datetime.date(year, month, last_day)
    # Thursday = 3
    d -= datetime.timedelta(days=(d.weekday() - 3) % 7)
    return d


def get_upcoming_expiries(n_weekly: int = 4, n_monthly: int = 3) -> list[datetime.date]:
    """
    Returns upcoming NSE expiry dates:
    - next n_weekly Thursdays (weekly series for NIFTY/BANKNIFTY etc.)
    - next n_monthly last-Thursdays of the month
    Combined, de-duped, sorted.
    """
    today = datetime.date.today()
    expiries: set[datetime.date] = set()

    # weekly: next n_weekly Thursdays
    d = today
    count = 0
    while count < n_weekly:
        days_ahead = (3 - d.weekday()) % 7  # 3 = Thursday
        if days_ahead == 0:
            days_ahead = 7
        d = d + datetime.timedelta(days=days_ahead)
        expiries.add(d)
        count += 1

    # monthly: last Thursday of next n_monthly months
    year, month = today.year, today.month
    for _ in range(n_monthly + 1):
        lt = _last_thursday(year, month)
        if lt > today:
            expiries.add(lt)
        month += 1
        if month > 12:
            month = 1
            year += 1

    return sorted(expiries)


def get_atm_strikes(spot: float, n: int = 8) -> list[int]:
    """
    Return 2n+1 strike prices centred on ATM.
    Strike interval = nearest round number to 1 % of spot.
    """
    raw_step = spot * 0.01
    # round to a "nice" number
    for step in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if raw_step <= step:
            break
    atm = round(spot / step) * step
    return [int(atm + i * step) for i in range(-n, n + 1)]
