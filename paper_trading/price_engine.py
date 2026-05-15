"""
Shared in-memory price cache with a background refresh thread.

Fetches LTP, prev_close, day_high, day_low, change_pct every REFRESH_INTERVAL seconds
for all watched symbols — so every view reads from cache, not from Yahoo directly.

Usage:
    from .price_engine import price_engine
    price_engine.watch('RELIANCE')
    data = price_engine.get('RELIANCE')
    # {'symbol', 'price', 'prev_close', 'day_high', 'day_low', 'change', 'change_pct'}
"""
import threading
import time
import logging
import random
import yfinance as yf

logging.getLogger('yfinance').setLevel(logging.CRITICAL)

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = 6   # seconds between full refresh cycles
SLIPPAGE_MIN     = 0.001   # 0.1 %
SLIPPAGE_MAX     = 0.003   # 0.3 %


def simulate_slippage(price: float, side: str) -> float:
    """Apply random slippage: BUY pays more, SELL receives less."""
    pct = random.uniform(SLIPPAGE_MIN, SLIPPAGE_MAX)
    if side == 'BUY':
        return round(price * (1 + pct), 2)
    else:
        return round(price * (1 - pct), 2)


class PriceEngine:
    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._watched: set[str] = set()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None

    # ── public API ──────────────────────────────────────────────────────────

    def watch(self, symbol: str):
        """Register a symbol for background tracking; fetches immediately on first watch."""
        symbol = symbol.upper()
        with self._lock:
            already = symbol in self._watched
            self._watched.add(symbol)
        if not already:
            self._fetch_one(symbol)
        self._ensure_running()

    def watch_many(self, symbols):
        for s in symbols:
            self.watch(s)

    def get(self, symbol: str) -> dict | None:
        return self._cache.get(symbol.upper())

    def get_all_watched(self) -> dict:
        with self._lock:
            keys = list(self._watched)
        return {k: self._cache[k] for k in keys if k in self._cache}

    # ── internals ───────────────────────────────────────────────────────────

    def _ensure_running(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='price-engine'
        )
        self._thread.start()

    def _fetch_one(self, symbol: str):
        import math, pandas as pd
        try:
            hist = yf.download(symbol + '.NS', period='5d', interval='1d',
                               progress=False, auto_adjust=True)
            if hist.empty:
                return
            if isinstance(hist.columns, pd.MultiIndex):
                hist = hist.droplevel(1, axis=1)
            price      = float(hist['Close'].iloc[-1])
            prev_close = float(hist['Close'].iloc[-2]) if len(hist) >= 2 else price
            day_high   = float(hist['High'].iloc[-1])
            day_low    = float(hist['Low'].iloc[-1])
            if math.isnan(price):
                return
            prev_close = prev_close if not math.isnan(prev_close) else price
            change     = price - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0
            with self._lock:
                self._cache[symbol] = {
                    'symbol':     symbol,
                    'price':      round(price, 2),
                    'prev_close': round(prev_close, 2),
                    'day_high':   round(day_high, 2),
                    'day_low':    round(day_low, 2),
                    'change':     round(change, 2),
                    'change_pct': round(change_pct, 2),
                }
        except Exception as exc:
            logger.debug('PriceEngine: error fetching %s: %s', symbol, exc)

    def _loop(self):
        while True:
            time.sleep(REFRESH_INTERVAL)
            with self._lock:
                symbols = list(self._watched)
            for sym in symbols:
                self._fetch_one(sym)


# Singleton — import this everywhere
price_engine = PriceEngine()
