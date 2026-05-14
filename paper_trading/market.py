"""
Market data helpers for NSE stocks.
get_stock_price() first checks the shared PriceEngine cache; falls back to direct yfinance call.
"""
import logging
import yfinance as yf

logging.getLogger('yfinance').setLevel(logging.CRITICAL)


def get_nse_ticker(symbol: str) -> str:
    symbol = symbol.upper().strip()
    if not symbol.endswith('.NS') and not symbol.endswith('.BO'):
        return symbol + '.NS'
    return symbol


def get_stock_price(symbol: str) -> dict | None:
    """
    Returns full price detail dict or None.
    Checks PriceEngine cache first; direct yfinance call if not cached.
    """
    from .price_engine import price_engine

    symbol = symbol.upper().strip()
    cached = price_engine.get(symbol)
    if cached:
        return cached  # already has price, prev_close, day_high, day_low, change_pct

    # Direct fetch (also populates cache via watch)
    try:
        ticker = yf.Ticker(get_nse_ticker(symbol))
        fi = ticker.fast_info
        price = fi.last_price
        if not price:
            return None
        prev_close  = fi.previous_close or price
        day_high    = fi.day_high or price
        day_low     = fi.day_low or price
        change      = price - prev_close
        change_pct  = (change / prev_close * 100) if prev_close else 0.0

        # Get company name cheaply
        name = symbol
        try:
            results = yf.Search(symbol, max_results=1)
            if results.quotes:
                q = results.quotes[0]
                name = q.get('longname') or q.get('shortname') or symbol
        except Exception:
            pass

        data = {
            'symbol':     symbol,
            'price':      round(price, 2),
            'prev_close': round(prev_close, 2),
            'day_high':   round(day_high, 2),
            'day_low':    round(day_low, 2),
            'change':     round(change, 2),
            'change_pct': round(change_pct, 2),
            'name':       name,
            'exchange':   'NSE',
        }
        # Register in engine for future background refreshes
        price_engine.watch(symbol)
        return data
    except Exception:
        return None


def search_stocks(query: str) -> list[dict]:
    """Search Indian stocks (NSE/BSE) by symbol or company name keyword."""
    try:
        results = yf.Search(query, max_results=20, enable_fuzzy_query=True)
        stocks = []
        seen = set()
        for item in results.quotes:
            raw_symbol = item.get('symbol', '')
            type_disp  = item.get('typeDisp', '').lower()
            is_nse = raw_symbol.endswith('.NS')
            is_bse = raw_symbol.endswith('.BO')
            if not (is_nse or is_bse):
                continue
            if type_disp not in ('equity', ''):
                continue
            clean = raw_symbol.replace('.NS', '').replace('.BO', '')
            if clean in seen:
                continue
            seen.add(clean)
            stocks.append({
                'symbol':   clean,
                'name':     item.get('longname') or item.get('shortname') or clean,
                'exchange': 'NSE' if is_nse else 'BSE',
            })
        return stocks
    except Exception:
        return []
