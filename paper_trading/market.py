"""
Market data helpers for NSE stocks.
get_stock_price() first checks the shared PriceEngine cache; falls back to direct yfinance call.
search_stocks() does local substring matching first (fast), then supplements with yfinance.
"""
import logging
import yfinance as yf

logging.getLogger('yfinance').setLevel(logging.CRITICAL)

# ─── Local NSE stock list ────────────────────────────────────────────────────
# (symbol, company name) — searched first for instant results
_NSE_STOCKS = [
    # Nifty 50
    ('ADANIENT',    'Adani Enterprises'),
    ('ADANIPORTS',  'Adani Ports & SEZ'),
    ('APOLLOHOSP',  'Apollo Hospitals Enterprise'),
    ('ASIANPAINT',  'Asian Paints'),
    ('AXISBANK',    'Axis Bank'),
    ('BAJAJ-AUTO',  'Bajaj Auto'),
    ('BAJFINANCE',  'Bajaj Finance'),
    ('BAJAJFINSV',  'Bajaj Finserv'),
    ('BPCL',        'Bharat Petroleum Corporation'),
    ('BHARTIARTL',  'Bharti Airtel'),
    ('BRITANNIA',   'Britannia Industries'),
    ('CIPLA',       'Cipla'),
    ('COALINDIA',   'Coal India'),
    ('DIVISLAB',    'Divi\'s Laboratories'),
    ('DRREDDY',     'Dr. Reddy\'s Laboratories'),
    ('EICHERMOT',   'Eicher Motors'),
    ('GRASIM',      'Grasim Industries'),
    ('HCLTECH',     'HCL Technologies'),
    ('HDFCBANK',    'HDFC Bank'),
    ('HDFCLIFE',    'HDFC Life Insurance'),
    ('HEROMOTOCO',  'Hero MotoCorp'),
    ('HINDALCO',    'Hindalco Industries'),
    ('HINDUNILVR',  'Hindustan Unilever'),
    ('ICICIBANK',   'ICICI Bank'),
    ('ITC',         'ITC'),
    ('INDUSINDBK',  'IndusInd Bank'),
    ('INFY',        'Infosys'),
    ('JSWSTEEL',    'JSW Steel'),
    ('KOTAKBANK',   'Kotak Mahindra Bank'),
    ('LT',          'Larsen & Toubro'),
    ('LTIM',        'LTIMindtree'),
    ('M&M',         'Mahindra & Mahindra'),
    ('MARUTI',      'Maruti Suzuki India'),
    ('NTPC',        'NTPC'),
    ('NESTLEIND',   'Nestle India'),
    ('ONGC',        'Oil & Natural Gas Corporation'),
    ('POWERGRID',   'Power Grid Corporation'),
    ('RELIANCE',    'Reliance Industries'),
    ('SBILIFE',     'SBI Life Insurance'),
    ('SBIN',        'State Bank of India'),
    ('SUNPHARMA',   'Sun Pharmaceutical Industries'),
    ('TCS',         'Tata Consultancy Services'),
    ('TATACONSUM',  'Tata Consumer Products'),
    ('TATAMOTORS',  'Tata Motors'),
    ('TATASTEEL',   'Tata Steel'),
    ('TECHM',       'Tech Mahindra'),
    ('TITAN',       'Titan Company'),
    ('ULTRACEMCO',  'UltraTech Cement'),
    ('UPL',         'UPL'),
    ('WIPRO',       'Wipro'),
    # Nifty Next 50
    ('ADANIGREEN',  'Adani Green Energy'),
    ('ADANITRANS',  'Adani Transmission'),
    ('AMBUJACEM',   'Ambuja Cements'),
    ('AUROPHARMA',  'Aurobindo Pharma'),
    ('BANDHANBNK',  'Bandhan Bank'),
    ('BANKBARODA',  'Bank of Baroda'),
    ('BEL',         'Bharat Electronics'),
    ('BERGEPAINT',  'Berger Paints India'),
    ('BOSCHLTD',    'Bosch'),
    ('CANBK',       'Canara Bank'),
    ('CHOLAFIN',    'Cholamandalam Investment & Finance'),
    ('COLPAL',      'Colgate-Palmolive India'),
    ('CONCOR',      'Container Corporation of India'),
    ('CUMMINSIND',  'Cummins India'),
    ('DLF',         'DLF'),
    ('DABUR',       'Dabur India'),
    ('DMART',       'Avenue Supermarts'),
    ('GAIL',        'GAIL India'),
    ('GODREJCP',    'Godrej Consumer Products'),
    ('GODREJPROP',  'Godrej Properties'),
    ('HAL',         'Hindustan Aeronautics'),
    ('HAVELLS',     'Havells India'),
    ('ICICIPRULI',  'ICICI Prudential Life Insurance'),
    ('ICICIGI',     'ICICI Lombard General Insurance'),
    ('IDFCFIRSTB',  'IDFC First Bank'),
    ('INDUSTOWER',  'Indus Towers'),
    ('IOC',         'Indian Oil Corporation'),
    ('IRCTC',       'Indian Railway Catering & Tourism'),
    ('JINDALSTEL',  'Jindal Steel & Power'),
    ('LUPIN',       'Lupin'),
    ('MRF',         'MRF'),
    ('MARICO',      'Marico'),
    ('MFSL',        'Max Financial Services'),
    ('MUTHOOTFIN',  'Muthoot Finance'),
    ('NAUKRI',      'Info Edge India'),
    ('OFSS',        'Oracle Financial Services'),
    ('PIDILITIND',  'Pidilite Industries'),
    ('PIIND',       'PI Industries'),
    ('PNB',         'Punjab National Bank'),
    ('RECLTD',      'REC'),
    ('SRF',         'SRF'),
    ('SAIL',        'Steel Authority of India'),
    ('SIEMENS',     'Siemens India'),
    ('TATAPOWER',   'Tata Power'),
    ('TORNTPHARM',  'Torrent Pharmaceuticals'),
    ('TRENT',       'Trent'),
    ('VEDL',        'Vedanta'),
    ('VOLTAS',      'Voltas'),
    ('ZOMATO',      'Zomato'),
    # Banking & Finance
    ('FEDERALBNK',  'Federal Bank'),
    ('HDFCAMC',     'HDFC Asset Management'),
    ('IIFL',        'IIFL Finance'),
    ('INDHOTEL',    'Indian Hotels Company'),
    ('KAJAKBANK',   'Karur Vysya Bank'),
    ('KTKBANK',     'Karnataka Bank'),
    ('LICIHSGFIN',  'LIC Housing Finance'),
    ('LICI',        'Life Insurance Corporation of India'),
    ('MANAPPURAM', 'Manappuram Finance'),
    ('MOTHERSON',   'Samvardhana Motherson International'),
    ('PNBHOUSING',  'PNB Housing Finance'),
    ('RBLBANK',     'RBL Bank'),
    ('SHRIRAMFIN',  'Shriram Finance'),
    ('SBICARD',     'SBI Cards & Payment Services'),
    ('UCOBANK',     'UCO Bank'),
    ('YESBANK',     'Yes Bank'),
    ('ANGELONE',    'Angel One'),
    ('MOTILALOFS',  'Motilal Oswal Financial Services'),
    # IT & Tech
    ('COFORGE',     'Coforge'),
    ('CYIENT',      'Cyient'),
    ('ECLERX',      'eClerx Services'),
    ('HEXAWARE',    'Hexaware Technologies'),
    ('KPITTECH',    'KPIT Technologies'),
    ('LTTS',        'L&T Technology Services'),
    ('MPHASIS',     'Mphasis'),
    ('NIITTECH',    'NIIT Technologies'),
    ('PERSISTENT',  'Persistent Systems'),
    ('SONACOMS',    'Sona BLW Precision Forgings'),
    ('TATAELXSI',   'Tata Elxsi'),
    ('TANLA',       'Tanla Platforms'),
    ('ZENSARTECH',  'Zensar Technologies'),
    # Pharma & Healthcare
    ('ABBOTINDIA',  'Abbott India'),
    ('ALKEM',       'Alkem Laboratories'),
    ('BIOCON',      'Biocon'),
    ('FORTIS',      'Fortis Healthcare'),
    ('GLENMARK',    'Glenmark Pharmaceuticals'),
    ('IPCA',        'IPCA Laboratories'),
    ('LAURUSLABS',  'Laurus Labs'),
    ('METROPOLIS',  'Metropolis Healthcare'),
    ('NATCOPHARM',  'Natco Pharma'),
    ('PGHH',        'Procter & Gamble Health'),
    ('PFIZER',      'Pfizer India'),
    ('SUNPHARMA',   'Sun Pharmaceutical Industries'),
    ('TORNTPHARM',  'Torrent Pharmaceuticals'),
    ('ZYDUSLIFE',   'Zydus Lifesciences'),
    # Auto & Auto Ancillary
    ('AMARAJABAT',  'Amara Raja Batteries'),
    ('APOLLOTYRE',  'Apollo Tyres'),
    ('ASHOKLEY',    'Ashok Leyland'),
    ('BALKRISIND',  'Balkrishna Industries'),
    ('BHARATFORG',  'Bharat Forge'),
    ('CEATLTD',     'CEAT'),
    ('ENDURANCE',   'Endurance Technologies'),
    ('ESCORTS',     'Escorts Kubota'),
    ('EXIDEIND',    'Exide Industries'),
    ('FORCEMOT',    'Force Motors'),
    ('MAHINDCIE',   'Mahindra CIE Automotive'),
    ('MAHSCOOTER',  'Maharashtra Scooters'),
    ('MOTHERSUMI',  'Motherson Sumi Wiring India'),
    ('SUNDRMFAST',  'Sundram Fasteners'),
    ('SUPRAJIT',    'Suprajit Engineering'),
    ('TVSMOTOR',    'TVS Motor Company'),
    ('WABCOINDIA',  'Wabco India'),
    # Energy & Utilities
    ('ADANIPOWER',  'Adani Power'),
    ('CESC',        'CESC'),
    ('HINDPETRO',   'Hindustan Petroleum Corporation'),
    ('IGL',         'Indraprastha Gas'),
    ('JSL',         'Jindal Stainless'),
    ('MGL',         'Mahanagar Gas'),
    ('MRPL',        'Mangalore Refinery & Petrochemicals'),
    ('PETRONET',    'Petronet LNG'),
    ('TATAPOWER',   'Tata Power'),
    ('TORNTPOWER',  'Torrent Power'),
    # Consumer & Retail
    ('BATAINDIA',   'Bata India'),
    ('CENTRALBK',   'Central Bank of India'),
    ('DBREALTY',    'D B Realty'),
    ('EMAMILTD',    'Emami'),
    ('GILLETTE',    'Gillette India'),
    ('GODFRYPHLP',  'Godfrey Phillips India'),
    ('JUBLPHARM',   'Jubilant Pharmova'),
    ('JYOTHYLAB',   'Jyothy Labs'),
    ('MANYAVAR',    'Vedant Fashions'),
    ('PAGEIND',     'Page Industries'),
    ('RAYMOND',     'Raymond'),
    ('SHOPERSTOP',  'Shoppers Stop'),
    ('TATACOMM',    'Tata Communications'),
    ('UFLEX',       'UFlex'),
    ('VBL',         'Varun Beverages'),
    ('VSTIND',      'VST Industries'),
    # Infrastructure & Real Estate
    ('ASHIANA',     'Ashiana Housing'),
    ('BRIGADE',     'Brigade Enterprises'),
    ('DHLFINANCE',  'Dhanvarsha Finvest'),
    ('GMRINFRA',    'GMR Airports Infrastructure'),
    ('HUDCO',       'Housing & Urban Development Corporation'),
    ('IRBINFRA',    'IRB Infrastructure Developers'),
    ('NCC',         'NCC'),
    ('NBCC',        'NBCC India'),
    ('PRESTIGE',    'Prestige Estates Projects'),
    ('SOBHA',       'Sobha'),
    ('SUNTECK',     'Sunteck Realty'),
    # Metals & Mining
    ('APLAPOLLO',   'APL Apollo Tubes'),
    ('HIKAL',       'Hikal'),
    ('HINDCOPPER',  'Hindustan Copper'),
    ('JSP',         'Jindal Stainless (Hisar)'),
    ('MOIL',        'MOIL'),
    ('NATIONALUM',  'National Aluminium Company'),
    ('NMDC',        'NMDC'),
    ('RATNAMANI',   'Ratnamani Metals & Tubes'),
    ('TINPLATE',    'Tinplate Company of India'),
    # New Age / Startups
    ('CARTRADE',    'CarTrade Tech'),
    ('DELHIVERY',   'Delhivery'),
    ('FSN',         'Nykaa (FSN E-Commerce)'),
    ('NYKAA',       'FSN E-Commerce Ventures (Nykaa)'),
    ('PAYTM',       'One 97 Communications (Paytm)'),
    ('POLICYBZR',   'PB Fintech (PolicyBazaar)'),
    ('ZOMATO',      'Zomato'),
    ('IXIGO',       'Le Travenues Technology (ixigo)'),
    ('MAPMYINDIA',  'C.E. Info Systems (MapmyIndia)'),
    # Chemicals
    ('AAPL',        'Astec Lifesciences'),
    ('ATUL',        'Atul'),
    ('BALAMINES',   'Balaji Amines'),
    ('CLEAN',       'Clean Science & Technology'),
    ('DEEPAKNTR',   'Deepak Nitrite'),
    ('FINEORG',     'Fine Organic Industries'),
    ('GNFC',        'Gujarat Narmada Valley Fertilizers & Chemicals'),
    ('NAVINFLUOR',  'Navin Fluorine International'),
    ('TATACHEM',    'Tata Chemicals'),
    # Defence
    ('BDL',         'Bharat Dynamics'),
    ('BEML',        'BEML'),
    ('COCHINSHIP',  'Cochin Shipyard'),
    ('DATAPATTNS',  'Data Patterns India'),
    ('GRSE',        'Garden Reach Shipbuilders & Engineers'),
    ('MAZDOCK',     'Mazagon Dock Shipbuilders'),
    ('PARAS',       'Paras Defence & Space Technologies'),
]

# Deduplicate preserving order
_seen = set()
_NSE_STOCKS_DEDUP = []
for _s, _n in _NSE_STOCKS:
    if _s not in _seen:
        _seen.add(_s)
        _NSE_STOCKS_DEDUP.append((_s, _n))
_NSE_STOCKS = _NSE_STOCKS_DEDUP


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
        return cached

    try:
        import math, pandas as pd
        yf_sym = get_nse_ticker(symbol)
        hist = yf.download(yf_sym, period='5d', interval='1d',
                           progress=False, auto_adjust=True)
        if hist.empty:
            return None
        if isinstance(hist.columns, pd.MultiIndex):
            hist = hist.droplevel(1, axis=1)
        price      = float(hist['Close'].iloc[-1])
        prev_close = float(hist['Close'].iloc[-2]) if len(hist) >= 2 else price
        day_high   = float(hist['High'].iloc[-1])
        day_low    = float(hist['Low'].iloc[-1])
        if any(math.isnan(v) for v in (price, prev_close)):
            return None
        change     = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0

        # Look up name from local stock list first (instant, no API call)
        name = next((n for s, n in _NSE_STOCKS if s == symbol), symbol)

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
        price_engine.watch(symbol)
        return data
    except Exception:
        return None


def search_stocks(query: str) -> list[dict]:
    """
    Search NSE stocks by symbol or company name.
    1. Local list: instant substring match on symbol + name, sorted (prefix > contains).
    2. yfinance: supplement with any results not already in local matches.
    Combined results, deduplicated, capped at 15.
    """
    q      = query.upper().strip()
    q_low  = query.lower().strip()
    if not q:
        return []

    # ── 1. Local substring search ──────────────────────────────────────────
    prefix_hits   = []   # symbol starts with query (highest priority)
    sym_hits      = []   # symbol contains query
    name_hits     = []   # name contains query (any word)

    words = q_low.split()   # for multi-word queries like "tata motors"

    for symbol, name in _NSE_STOCKS:
        sym_up  = symbol.upper()
        name_lo = name.lower()

        if sym_up.startswith(q):
            prefix_hits.append({'symbol': symbol, 'name': name, 'exchange': 'NSE'})
        elif q in sym_up:
            sym_hits.append({'symbol': symbol, 'name': name, 'exchange': 'NSE'})
        elif all(w in name_lo for w in words):
            name_hits.append({'symbol': symbol, 'name': name, 'exchange': 'NSE'})

    local_results = prefix_hits + sym_hits + name_hits
    local_symbols = {r['symbol'] for r in local_results}

    # ── 2. yfinance supplement ─────────────────────────────────────────────
    yf_results = []
    try:
        yf_search = yf.Search(query, max_results=20, enable_fuzzy_query=True)
        for item in yf_search.quotes:
            raw      = item.get('symbol', '')
            type_d   = item.get('typeDisp', '').lower()
            is_nse   = raw.endswith('.NS')
            is_bse   = raw.endswith('.BO')
            if not (is_nse or is_bse):
                continue
            if type_d not in ('equity', ''):
                continue
            clean = raw.replace('.NS', '').replace('.BO', '')
            if clean in local_symbols:
                continue
            local_symbols.add(clean)
            yf_results.append({
                'symbol':   clean,
                'name':     item.get('longname') or item.get('shortname') or clean,
                'exchange': 'NSE' if is_nse else 'BSE',
            })
    except Exception:
        pass

    return (local_results + yf_results)[:15]
