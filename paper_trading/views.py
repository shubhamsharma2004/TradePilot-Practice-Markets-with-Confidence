import json
from datetime import timedelta
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.http import JsonResponse

from .models import (PaperPortfolio, PaperPosition, PaperOrder,
                     PaperPortfolioSnapshot, STARTING_BALANCE,
                     FnOInstrument, FnOPosition, FnOOrder, LOT_SIZES, DEFAULT_LOT_SIZE)
from .market import get_stock_price, search_stocks
from .price_engine import price_engine, simulate_slippage
from .fno_market import get_fno_price, get_margin, get_upcoming_expiries, get_atm_strikes


# ─── helpers ────────────────────────────────────────────────────────────────

def get_or_create_portfolio(request):
    if request.user.is_authenticated:
        portfolio, _ = PaperPortfolio.objects.get_or_create(
            user=request.user,
            defaults={'session_key': None},
        )
        return portfolio
    # Anonymous fallback
    if not request.session.session_key:
        request.session.create()
    key = request.session.session_key
    portfolio, _ = PaperPortfolio.objects.get_or_create(session_key=key)
    return portfolio


# ─── dashboard ──────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    portfolio = get_or_create_portfolio(request)
    positions = list(portfolio.positions.all())

    # Refresh last prices — use cache first, fall back to direct fetch
    for pos in positions:
        cached = price_engine.get(pos.symbol)
        if cached:
            pos.last_price = Decimal(str(cached['price']))
            pos.save(update_fields=['last_price', 'updated_at'])
        else:
            data = get_stock_price(pos.symbol)
            if data:
                pos.last_price = Decimal(str(data['price']))
                pos.save(update_fields=['last_price', 'updated_at'])

    # Watch all position symbols in the price engine
    price_engine.watch_many([p.symbol for p in positions])

    # Daily NAV snapshot (once per day)
    today = timezone.localdate()
    if not portfolio.snapshots.filter(recorded_at__date=today).exists():
        PaperPortfolioSnapshot.objects.create(
            portfolio=portfolio,
            portfolio_value=portfolio.total_portfolio_value,
            cash_balance=portfolio.cash_balance,
            total_invested=portfolio.total_invested,
            total_pnl=portfolio.unrealized_pnl,
        )

    recent_orders = portfolio.orders.all()[:10]

    total = portfolio.total_portfolio_value or 1
    alloc = {
        'cash_pct':     round(float(portfolio.cash_balance) / total * 100, 1),
        'invested_pct': round(portfolio.total_invested / total * 100, 1),
        'pnl_pct':      round(max(portfolio.unrealized_pnl, 0) / total * 100, 1),
    }

    donut_labels = json.dumps([p.symbol for p in positions])
    donut_values = json.dumps([round(p.current_value, 2) for p in positions])

    context = {
        'portfolio':        portfolio,
        'positions':        positions,
        'recent_orders':    recent_orders,
        'starting_balance': STARTING_BALANCE,
        'alloc':            alloc,
        'donut_labels':     donut_labels,
        'donut_values':     donut_values,
        'first_symbol':     positions[0].symbol if positions else '',
    }
    return render(request, 'paper_trading/dashboard.html', context)


# ─── place order ────────────────────────────────────────────────────────────

@login_required
def place_order(request):
    portfolio = get_or_create_portfolio(request)

    if request.method == 'POST':
        symbol     = request.POST.get('symbol', '').upper().strip()
        order_type = request.POST.get('order_type')       # BUY | SELL
        price_type = request.POST.get('price_type', 'MARKET')  # MARKET | LIMIT | SL

        try:
            quantity    = int(request.POST.get('quantity', 0))
            limit_price = request.POST.get('limit_price')
            stop_price  = request.POST.get('stop_price')
            limit_price = float(limit_price) if limit_price else None
            stop_price  = float(stop_price)  if stop_price  else None
        except ValueError:
            messages.error(request, 'Invalid quantity or price.')
            return redirect('paper_trading:place_order')

        if quantity <= 0:
            messages.error(request, 'Quantity must be greater than 0.')
            return redirect('paper_trading:place_order')

        # Fetch live price
        stock_data = get_stock_price(symbol)
        if not stock_data:
            messages.error(request, f'Could not fetch price for {symbol}.')
            return redirect('paper_trading:place_order')

        live_price   = stock_data['price']
        company_name = stock_data.get('name', symbol)

        # ── MARKET order: execute immediately with slippage ──
        if price_type == PaperOrder.MARKET:
            executed_price = simulate_slippage(live_price, order_type)
            total_cost     = executed_price * quantity

            order = PaperOrder(
                portfolio=portfolio, symbol=symbol, company_name=company_name,
                order_type=order_type, price_type=price_type, quantity=quantity,
                executed_price=executed_price,
            )

            if order_type == PaperOrder.BUY:
                if float(portfolio.cash_balance) < total_cost:
                    order.status = PaperOrder.REJECTED
                    order.rejection_reason = (
                        f'Insufficient funds. Required ₹{total_cost:,.2f}, '
                        f'available ₹{portfolio.cash_balance:,.2f}'
                    )
                    order.save()
                    messages.error(request, order.rejection_reason)
                    return redirect('paper_trading:place_order')

                portfolio.cash_balance = float(portfolio.cash_balance) - total_cost
                portfolio.save()
                _update_position_buy(portfolio, symbol, company_name, quantity, executed_price)

            elif order_type == PaperOrder.SELL:
                try:
                    position = PaperPosition.objects.get(portfolio=portfolio, symbol=symbol)
                except PaperPosition.DoesNotExist:
                    order.status = PaperOrder.REJECTED
                    order.rejection_reason = f'You do not hold any {symbol} shares.'
                    order.save()
                    messages.error(request, order.rejection_reason)
                    return redirect('paper_trading:place_order')

                if position.quantity < quantity:
                    order.status = PaperOrder.REJECTED
                    order.rejection_reason = (
                        f'Insufficient shares. Hold {position.quantity}, selling {quantity}.'
                    )
                    order.save()
                    messages.error(request, order.rejection_reason)
                    return redirect('paper_trading:place_order')

                realized = (executed_price - float(position.avg_buy_price)) * quantity
                portfolio.realized_pnl = float(portfolio.realized_pnl) + realized
                portfolio.cash_balance = float(portfolio.cash_balance) + total_cost
                portfolio.save()
                _update_position_sell(portfolio, symbol, quantity, executed_price)

            order.status      = PaperOrder.EXECUTED
            order.executed_at = timezone.now()
            order.save()
            messages.success(
                request,
                f'{order_type} executed: {quantity} × {symbol} @ ₹{executed_price:,.2f} '
                f'(incl. slippage)'
            )

        # ── LIMIT / STOP-LOSS order: store as PENDING ──
        else:
            ref_price = limit_price if price_type == PaperOrder.LIMIT else stop_price
            if not ref_price:
                messages.error(request, 'Price required for Limit / Stop-Loss orders.')
                return redirect('paper_trading:place_order')

            # For LIMIT BUY, reserve cash upfront
            if price_type == PaperOrder.LIMIT and order_type == PaperOrder.BUY:
                reserved = ref_price * quantity
                if float(portfolio.cash_balance) < reserved:
                    messages.error(
                        request,
                        f'Insufficient funds to reserve ₹{reserved:,.2f} for limit order.'
                    )
                    return redirect('paper_trading:place_order')
                portfolio.cash_balance = float(portfolio.cash_balance) - reserved
                portfolio.save()

            order = PaperOrder(
                portfolio=portfolio, symbol=symbol, company_name=company_name,
                order_type=order_type, price_type=price_type, quantity=quantity,
                limit_price=limit_price if price_type == PaperOrder.LIMIT else None,
                stop_price=stop_price   if price_type == PaperOrder.STOP_LOSS else None,
                status=PaperOrder.PENDING,
            )
            order.save()
            price_engine.watch(symbol)   # ensure engine tracks this symbol
            messages.success(
                request,
                f'{price_type} {order_type} order placed for {quantity} × {symbol} — '
                f'will execute when price condition is met.'
            )

        return redirect('paper_trading:dashboard')

    # GET
    symbol     = request.GET.get('symbol', '')
    stock_data = get_stock_price(symbol) if symbol else None

    context = {
        'portfolio':  portfolio,
        'symbol':     symbol,
        'stock_data': stock_data,
    }
    return render(request, 'paper_trading/place_order.html', context)


# ─── order helpers ───────────────────────────────────────────────────────────

def _update_position_buy(portfolio, symbol, company_name, quantity, price):
    position, created = PaperPosition.objects.get_or_create(
        portfolio=portfolio, symbol=symbol,
        defaults={'company_name': company_name, 'quantity': 0,
                  'avg_buy_price': price, 'last_price': price}
    )
    if not created:
        total_qty = position.quantity + quantity
        position.avg_buy_price = Decimal(str(
            (float(position.avg_buy_price) * position.quantity + price * quantity) / total_qty
        ))
        position.quantity = total_qty
    else:
        position.quantity = quantity
    position.last_price   = Decimal(str(price))
    position.company_name = company_name
    position.save()


def _update_position_sell(portfolio, symbol, quantity, price):
    try:
        position = PaperPosition.objects.get(portfolio=portfolio, symbol=symbol)
        position.quantity  -= quantity
        position.last_price = Decimal(str(price))
        if position.quantity == 0:
            position.delete()
        else:
            position.save()
    except PaperPosition.DoesNotExist:
        pass


# ─── cancel order ────────────────────────────────────────────────────────────

@login_required
def cancel_order(request, order_id):
    portfolio = get_or_create_portfolio(request)
    order = get_object_or_404(PaperOrder, id=order_id, portfolio=portfolio, status=PaperOrder.PENDING)

    # Refund reserved cash for LIMIT BUY
    if order.price_type == PaperOrder.LIMIT and order.order_type == PaperOrder.BUY:
        reserved = float(order.limit_price) * order.quantity
        portfolio.cash_balance = float(portfolio.cash_balance) + reserved
        portfolio.save()

    order.status = PaperOrder.CANCELLED
    order.save()
    messages.success(request, f'Order for {order.symbol} cancelled.')
    return redirect('paper_trading:order_history')


# ─── order history ───────────────────────────────────────────────────────────

@login_required
def order_history(request):
    portfolio = get_or_create_portfolio(request)
    orders    = portfolio.orders.all()
    return render(request, 'paper_trading/order_history.html', {
        'portfolio': portfolio,
        'orders':    orders,
    })


# ─── analytics ───────────────────────────────────────────────────────────────

@login_required
def analytics(request):
    portfolio = get_or_create_portfolio(request)
    executed  = portfolio.orders.filter(status=PaperOrder.EXECUTED)

    sells = executed.filter(order_type=PaperOrder.SELL)
    buys  = executed.filter(order_type=PaperOrder.BUY)

    # Per-trade realized P&L approximation using current avg_buy_price
    trade_pnls = []
    for sell in sells:
        ep = float(sell.executed_price or 0)
        # Find the buy price at time of sell — use avg_buy_price from position if still open
        try:
            pos = PaperPosition.objects.get(portfolio=portfolio, symbol=sell.symbol)
            cost_basis = float(pos.avg_buy_price)
        except PaperPosition.DoesNotExist:
            cost_basis = ep   # fully sold; can't recover exact basis
        trade_pnls.append({
            'symbol':    sell.symbol,
            'pnl':       round((ep - cost_basis) * sell.quantity, 2),
            'date':      sell.executed_at or sell.created_at,
            'qty':       sell.quantity,
            'sell_price': ep,
        })

    wins        = [t for t in trade_pnls if t['pnl'] > 0]
    losses      = [t for t in trade_pnls if t['pnl'] <= 0]
    win_rate    = round(len(wins) / len(trade_pnls) * 100, 1) if trade_pnls else 0
    avg_profit  = round(sum(t['pnl'] for t in wins)   / len(wins),   2) if wins   else 0
    avg_loss    = round(sum(t['pnl'] for t in losses) / len(losses), 2) if losses else 0
    best_trade  = max(trade_pnls, key=lambda t: t['pnl'], default=None)
    worst_trade = min(trade_pnls, key=lambda t: t['pnl'], default=None)

    # ── Learning Insights ────────────────────────────────────────────────────
    insights = []
    positions = list(portfolio.positions.all())
    total_invested = portfolio.total_invested or 1
    cash_pct = float(portfolio.cash_balance) / max(portfolio.total_portfolio_value, 1) * 100

    # 1. Single-stock concentration > 50 %
    for pos in positions:
        pct = pos.invested_value / total_invested * 100
        if pct > 50:
            insights.append({
                'icon': '⚠️', 'type': 'warning',
                'title': 'High Concentration Risk',
                'body': f'You have {pct:.0f}% of your invested capital in {pos.symbol}. '
                        f'Consider spreading across at least 5–6 stocks to reduce single-stock risk.',
            })

    # 2. No stop-loss orders used at all
    if not any(o.price_type == PaperOrder.STOP_LOSS for o in executed):
        insights.append({
            'icon': '🛡️', 'type': 'tip',
            'title': 'No Stop-Loss Orders Used',
            'body': 'You have never placed a Stop-Loss order. A stop-loss automatically sells '
                    'your position if the price falls to a trigger level — protecting you from big losses.',
        })

    # 3. Under-diversified (1–2 stocks only)
    if 0 < len(positions) <= 2:
        insights.append({
            'icon': '📌', 'type': 'tip',
            'title': 'Low Diversification',
            'body': f'You hold only {len(positions)} stock(s). '
                    f'Spreading across 5–8 uncorrelated stocks significantly reduces portfolio volatility.',
        })

    # 4. Unrealized loss > 5 % of starting capital
    if portfolio.unrealized_pnl < 0 and abs(portfolio.unrealized_pnl) > 0.05 * STARTING_BALANCE:
        insights.append({
            'icon': '🔴', 'type': 'danger',
            'title': 'Large Unrealized Loss',
            'body': f'Your open positions are down ₹{abs(portfolio.unrealized_pnl):,.0f} '
                    f'({abs(portfolio.unrealized_pnl) / STARTING_BALANCE * 100:.1f}% of starting capital). '
                    f'Review each position and consider cutting losses.',
        })

    # 5. Sitting on too much cash (> 70 % idle)
    if cash_pct > 70 and len(positions) == 0:
        insights.append({
            'icon': '💤', 'type': 'tip',
            'title': 'Capital Sitting Idle',
            'body': f'{cash_pct:.0f}% of your portfolio is in cash. '
                    f'Consider deploying capital across a few quality stocks.',
        })

    # 6. Win rate below 40 % with enough trades
    if len(trade_pnls) >= 5 and win_rate < 40:
        insights.append({
            'icon': '📉', 'type': 'warning',
            'title': 'Low Win Rate',
            'body': f'Only {win_rate}% of your closed trades were profitable. '
                    f'Review your entry strategy — consider buying dips on strong trend stocks.',
        })

    # 7. Average loss > 2× average profit (bad risk/reward)
    if avg_profit > 0 and avg_loss < 0 and abs(avg_loss) > 2 * avg_profit:
        insights.append({
            'icon': '⚖️', 'type': 'warning',
            'title': 'Poor Risk/Reward Ratio',
            'body': f'Your avg loss (₹{abs(avg_loss):,.2f}) is more than 2× your avg profit (₹{avg_profit:,.2f}). '
                    f'Use stop-losses to cut losses early and let winners run.',
        })

    # 8. All trades in same stock (no sector diversity)
    unique_trade_symbols = len(set(t['symbol'] for t in trade_pnls))
    if len(trade_pnls) >= 4 and unique_trade_symbols == 1:
        insights.append({
            'icon': '🔁', 'type': 'tip',
            'title': 'Trading Only One Stock',
            'body': f'All your trades have been in {trade_pnls[0]["symbol"]}. '
                    f'Explore other sectors to build broader market experience.',
        })

    # 9. Total return > 10 % — positive reinforcement
    if portfolio.portfolio_return_pct > 10:
        insights.append({
            'icon': '🏆', 'type': 'success',
            'title': 'Strong Overall Return',
            'body': f'You\'ve grown your portfolio by {portfolio.portfolio_return_pct}% from ₹10L starting capital. '
                    f'Great work — now focus on protecting gains with stop-losses.',
        })

    # 10. Realized P&L negative — booked losses
    if float(portfolio.realized_pnl) < -0.02 * STARTING_BALANCE:
        insights.append({
            'icon': '📋', 'type': 'warning',
            'title': 'Booked Losses Accumulating',
            'body': f'You have realized a loss of ₹{abs(float(portfolio.realized_pnl)):,.0f}. '
                    f'Analyse which trades went wrong and identify the pattern.',
        })

    if not insights:
        insights.append({
            'icon': '✅', 'type': 'success',
            'title': 'Portfolio Looks Balanced',
            'body': 'No major risk flags detected. Keep diversifying, use stop-losses, '
                    'and track your performance daily.',
        })

    # ── F&O Analytics ────────────────────────────────────────────────────────
    fno_executed = list(
        portfolio.fno_orders.filter(status=FnOOrder.EXECUTED).select_related('instrument')
    )
    fno_closed = [o for o in fno_executed if o.realized_pnl and float(o.realized_pnl) != 0]
    fno_total_realized = sum(float(o.realized_pnl) for o in fno_closed)
    fno_wins   = [o for o in fno_closed if float(o.realized_pnl) > 0]
    fno_losses = [o for o in fno_closed if float(o.realized_pnl) < 0]
    fno_win_rate = round(len(fno_wins) / len(fno_closed) * 100, 1) if fno_closed else 0
    fno_best  = max(fno_closed, key=lambda o: float(o.realized_pnl), default=None)
    fno_worst = min(fno_closed, key=lambda o: float(o.realized_pnl), default=None)

    context = {
        'portfolio':    portfolio,
        'total_trades': len(executed),
        'total_buys':   buys.count(),
        'total_sells':  sells.count(),
        'win_rate':     win_rate,
        'avg_profit':   avg_profit,
        'avg_loss':     avg_loss,
        'best_trade':   best_trade,
        'worst_trade':  worst_trade,
        'trade_pnls':   trade_pnls[-20:],
        'insights':     insights,
        # F&O
        'fno_total':          len(fno_executed),
        'fno_closed_count':   len(fno_closed),
        'fno_total_realized': round(fno_total_realized, 2),
        'fno_win_rate':       fno_win_rate,
        'fno_wins_count':     len(fno_wins),
        'fno_losses_count':   len(fno_losses),
        'fno_best':           fno_best,
        'fno_worst':          fno_worst,
        'fno_recent':         fno_executed[:20],
    }
    return render(request, 'paper_trading/analytics.html', context)


# ─── AJAX APIs ───────────────────────────────────────────────────────────────


def stock_search_api(request):
    query = request.GET.get('q', '').strip()
    if len(query) < 2:
        return JsonResponse({'results': []})
    return JsonResponse({'results': search_stocks(query)})



def stock_price_api(request):
    symbol = request.GET.get('symbol', '').strip().upper()
    if not symbol:
        return JsonResponse({'error': 'No symbol'}, status=400)
    data = get_stock_price(symbol)
    if not data:
        return JsonResponse({'error': f'{symbol} not found'}, status=404)
    return JsonResponse(data)



def portfolio_prices_api(request):
    """Polling endpoint — returns latest cached prices for all user positions."""
    portfolio = get_or_create_portfolio(request)
    out = {}
    for pos in portfolio.positions.all():
        cached = price_engine.get(pos.symbol)
        if cached:
            out[pos.symbol] = cached
        else:
            price_engine.watch(pos.symbol)
    return JsonResponse({'prices': out})



def ticker_api(request):
    """Returns live prices for the header ticker tape."""
    from concurrent.futures import ThreadPoolExecutor

    _TICKER_SYMBOLS = [
        ('RELIANCE', 'Reliance'),  ('TCS', 'TCS'),
        ('HDFCBANK', 'HDFC Bank'), ('INFY', 'Infosys'),
        ('ICICIBANK', 'ICICI'),    ('SBIN', 'SBI'),
        ('AXISBANK', 'Axis Bank'), ('TATAMOTORS', 'Tata Motors'),
        ('BAJFINANCE', 'Bajaj Fin'),('BHARTIARTL', 'Airtel'),
        ('WIPRO', 'Wipro'),        ('HCLTECH', 'HCL Tech'),
        ('KOTAKBANK', 'Kotak'),    ('LT', 'L&T'),
        ('MARUTI', 'Maruti'),
    ]

    def fetch(item):
        sym, name = item
        cached = price_engine.get(sym)
        if cached:
            return {**cached, 'name': name}
        data = get_stock_price(sym)
        if data:
            return {**data, 'name': name}
        return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(fetch, _TICKER_SYMBOLS))

    return JsonResponse({'tickers': [r for r in results if r]})



def nav_history_api(request):
    portfolio = get_or_create_portfolio(request)
    period    = request.GET.get('period', 'all')

    qs = portfolio.snapshots.all()
    if period == '1w':
        qs = qs.filter(recorded_at__gte=timezone.now() - timedelta(days=7))
    elif period == '1m':
        qs = qs.filter(recorded_at__gte=timezone.now() - timedelta(days=30))
    elif period == '3m':
        qs = qs.filter(recorded_at__gte=timezone.now() - timedelta(days=90))

    starting = Decimal(STARTING_BALANCE)
    data = [
        {
            'time':    snap.recorded_at.strftime('%Y-%m-%d'),
            'value':   float(snap.portfolio_value),
            'pnl':     float(snap.total_pnl),
            'pnl_pct': round(float((snap.portfolio_value - starting) / starting * 100), 2),
        }
        for snap in qs
    ]
    return JsonResponse({'history': data})



@login_required
def what_if_simulator(request):
    portfolio = get_or_create_portfolio(request)
    return render(request, 'paper_trading/simulator.html', {'portfolio': portfolio})



def what_if_api(request):
    """
    AJAX: given symbol, buy_date, sell_date, quantity → returns P&L simulation.
    Uses yfinance historical daily close prices.
    """
    import yfinance as yf
    from datetime import datetime, timedelta as td

    symbol    = request.GET.get('symbol', '').strip().upper()
    buy_date  = request.GET.get('buy_date', '')
    sell_date = request.GET.get('sell_date', '')
    try:
        quantity = int(request.GET.get('quantity', 1))
    except ValueError:
        quantity = 1

    if not symbol or not buy_date or not sell_date:
        return JsonResponse({'error': 'symbol, buy_date and sell_date are required.'}, status=400)

    try:
        buy_dt  = datetime.strptime(buy_date,  '%Y-%m-%d').date()
        sell_dt = datetime.strptime(sell_date, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

    if sell_dt <= buy_dt:
        return JsonResponse({'error': 'Sell date must be after buy date.'}, status=400)

    if (sell_dt - buy_dt).days > 365 * 10:
        return JsonResponse({'error': 'Date range too large (max 10 years).'}, status=400)

    try:
        ticker = yf.Ticker(symbol + '.NS')
        # Fetch a window slightly wider to handle weekends/holidays
        fetch_start = buy_dt - td(days=5)
        fetch_end   = sell_dt + td(days=5)
        hist = ticker.history(start=fetch_start.strftime('%Y-%m-%d'),
                              end=fetch_end.strftime('%Y-%m-%d'),
                              interval='1d')
        if hist.empty:
            return JsonResponse({'error': f'No historical data for {symbol}.'}, status=404)

        # Reset index so Date is a column, convert to date only
        hist = hist.reset_index()
        hist['Date'] = hist['Date'].dt.date

        # Find closest available trading day on or after buy_date
        buy_rows  = hist[hist['Date'] >= buy_dt]
        sell_rows = hist[hist['Date'] <= sell_dt]

        if buy_rows.empty:
            return JsonResponse({'error': f'No trading data on or after {buy_date}.'}, status=404)
        if sell_rows.empty:
            return JsonResponse({'error': f'No trading data on or before {sell_date}.'}, status=404)

        buy_row  = buy_rows.iloc[0]
        sell_row = sell_rows.iloc[-1]

        buy_price  = round(float(buy_row['Close']),  2)
        sell_price = round(float(sell_row['Close']), 2)
        actual_buy_date  = buy_row['Date'].strftime('%Y-%m-%d')
        actual_sell_date = sell_row['Date'].strftime('%Y-%m-%d')

        invested     = round(buy_price * quantity, 2)
        final_value  = round(sell_price * quantity, 2)
        profit       = round(final_value - invested, 2)
        profit_pct   = round((profit / invested) * 100, 2) if invested else 0

        # Build a price series for the chart (daily closes between the two dates)
        chart_data = hist[(hist['Date'] >= buy_dt) & (hist['Date'] <= sell_dt)]
        chart = [
            {'date': row['Date'].strftime('%Y-%m-%d'), 'close': round(float(row['Close']), 2)}
            for _, row in chart_data.iterrows()
        ]

        return JsonResponse({
            'symbol':           symbol,
            'buy_date':         actual_buy_date,
            'sell_date':        actual_sell_date,
            'buy_price':        buy_price,
            'sell_price':       sell_price,
            'quantity':         quantity,
            'invested':         invested,
            'final_value':      final_value,
            'profit':           profit,
            'profit_pct':       profit_pct,
            'is_profit':        profit >= 0,
            'chart':            chart,
        })

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)



def stock_ohlc_api(request):
    import yfinance as yf
    symbol = request.GET.get('symbol', '').strip().upper()
    period = request.GET.get('period', '6mo')
    if not symbol:
        return JsonResponse({'error': 'No symbol'}, status=400)

    # period → (yf period, yf interval, is_intraday)
    PERIOD_MAP = {
        '1d':  ('1d',  '1m',  True),
        '5d':  ('5d',  '5m',  True),
        '1mo': ('1mo', '1d',  False),
        '3mo': ('3mo', '1d',  False),
        '6mo': ('6mo', '1d',  False),
        '1y':  ('1y',  '1d',  False),
        '2y':  ('2y',  '1d',  False),
        '5y':  ('5y',  '1wk', False),
    }
    if period not in PERIOD_MAP:
        period = '6mo'

    yf_period, yf_interval, is_intraday = PERIOD_MAP[period]

    # Map index names to their yfinance tickers
    _OHLC_INDEX_MAP = {
        'NIFTY': '^NSEI', 'BANKNIFTY': '^NSEBANK', 'SENSEX': '^BSESN',
        'FINNIFTY': 'NIFTY_FIN_SERVICE.NS', 'MIDCPNIFTY': '^NSEMDCP50',
    }
    yf_sym = _OHLC_INDEX_MAP.get(symbol, symbol + '.NS')

    from django.core.cache import cache
    cache_ttl = 60 if is_intraday else 600   # 1 min for intraday, 10 min for daily
    cache_key = f'ohlc_{yf_sym}_{yf_period}_{yf_interval}'
    cached_payload = cache.get(cache_key)
    if cached_payload:
        return JsonResponse(cached_payload)

    try:
        import pandas as pd
        hist = yf.download(yf_sym, period=yf_period, interval=yf_interval,
                           progress=False, auto_adjust=True)
        if hist.empty:
            return JsonResponse({'error': 'No data'}, status=404)
        if isinstance(hist.columns, pd.MultiIndex):
            hist = hist.droplevel(1, axis=1)

        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        candles = []
        for ts, row in hist.iterrows():
            if is_intraday:
                if ts.tzinfo is None:
                    ts = pytz.utc.localize(ts)
                ts_ist = ts.astimezone(ist)
                t = int(ts_ist.timestamp())
            else:
                t = ts.strftime('%Y-%m-%d')
            candles.append({
                'time':   t,
                'open':   round(float(row['Open']),  2),
                'high':   round(float(row['High']),  2),
                'low':    round(float(row['Low']),   2),
                'close':  round(float(row['Close']), 2),
                'volume': int(row['Volume']),
            })
        payload = {'symbol': symbol, 'candles': candles, 'intraday': is_intraday}
        cache.set(cache_key, payload, cache_ttl)
        return JsonResponse(payload)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ─── F&O helpers ──────────────────────────────────────────────────────────────

# NSE/BSE index symbol → yfinance ticker
_INDEX_TICKERS = {
    'NIFTY':      '^NSEI',
    'BANKNIFTY':  '^NSEBANK',
    'SENSEX':     '^BSESN',
    'FINNIFTY':   'NIFTY_FIN_SERVICE.NS',
    'MIDCPNIFTY': '^NSEMDCP50',
    'BANKEX':     'BSE-BANK.BO',
}


def _get_spot(underlying: str) -> float | None:
    """Fetch spot price for an underlying (index or stock)."""
    sym = underlying.upper()
    yf_sym = _INDEX_TICKERS.get(sym)
    if yf_sym:
        try:
            import yfinance as yf
            fi = yf.Ticker(yf_sym).fast_info
            price = fi.last_price
            return round(price, 2) if price else None
        except Exception:
            return None
    data = get_stock_price(sym)
    return data['price'] if data else None


def _fno_get_or_create_instrument(underlying, itype, expiry, strike, lot_size):
    inst, _ = FnOInstrument.objects.get_or_create(
        underlying=underlying.upper(),
        instrument_type=itype,
        expiry=expiry,
        strike=strike,
        defaults={'lot_size': lot_size},
    )
    return inst


# ─── F&O dashboard ────────────────────────────────────────────────────────────

@login_required
def fno_dashboard(request):
    portfolio  = get_or_create_portfolio(request)
    positions  = list(portfolio.fno_positions.select_related('instrument').all())
    recent_orders = list(portfolio.fno_orders.select_related('instrument').all()[:15])

    total_unrealized = 0
    total_margin     = 0
    for pos in positions:
        spot = _get_spot(pos.instrument.underlying)
        if spot:
            from datetime import date
            pd = get_fno_price(
                pos.instrument.underlying,
                pos.instrument.instrument_type,
                float(pos.instrument.strike) if pos.instrument.strike else None,
                pos.instrument.expiry,
                spot,
            )
            pos.last_price = pd['price']
            pos.save(update_fields=['last_price', 'updated_at'])
        total_unrealized += pos.unrealized_pnl
        total_margin     += float(pos.margin_blocked)

    context = {
        'portfolio':         portfolio,
        'positions':         positions,
        'recent_orders':     recent_orders,
        'total_unrealized':  round(total_unrealized, 2),
        'total_margin':      round(total_margin, 2),
    }
    return render(request, 'paper_trading/fno_dashboard.html', context)


# ─── F&O order placement ──────────────────────────────────────────────────────

@login_required
def fno_order(request):
    portfolio = get_or_create_portfolio(request)

    if request.method == 'POST':
        underlying  = request.POST.get('underlying', '').upper().strip()
        itype       = request.POST.get('instrument_type', 'CE')
        expiry_str  = request.POST.get('expiry', '')
        order_side  = request.POST.get('order_type', 'BUY')  # BUY or SELL

        try:
            lots = int(request.POST.get('lots', 1))
            if lots <= 0:
                raise ValueError
        except ValueError:
            messages.error(request, 'Lots must be a positive integer.')
            return redirect('paper_trading:fno_order')

        try:
            from datetime import date
            expiry = date.fromisoformat(expiry_str)
        except ValueError:
            messages.error(request, 'Invalid expiry date.')
            return redirect('paper_trading:fno_order')

        strike = None
        if itype in ('CE', 'PE'):
            try:
                strike = float(request.POST.get('strike', 0))
                if strike <= 0:
                    raise ValueError
            except ValueError:
                messages.error(request, 'Strike price required for options.')
                return redirect('paper_trading:fno_order')

        spot = _get_spot(underlying)
        if spot is None:
            messages.error(request, f'Could not fetch spot price for {underlying}.')
            return redirect('paper_trading:fno_order')

        lot_size = LOT_SIZES.get(underlying, DEFAULT_LOT_SIZE)
        pricing  = get_fno_price(underlying, itype, strike, expiry, spot)
        price    = pricing['price']
        margin   = get_margin(itype, order_side, spot, price, lots, lot_size)

        if float(portfolio.cash_balance) < margin:
            messages.error(
                request,
                f'Insufficient funds. Required ₹{margin:,.2f} '
                f'({"premium" if itype != "FUT" and order_side == "BUY" else "margin"}), '
                f'available ₹{float(portfolio.cash_balance):,.2f}.'
            )
            return redirect('paper_trading:fno_order')

        instrument = _fno_get_or_create_instrument(underlying, itype, expiry, strike, lot_size)

        # Deduct margin/premium from cash
        portfolio.cash_balance = float(portfolio.cash_balance) - margin
        portfolio.save()

        # Update or create position
        lot_delta = lots if order_side == 'BUY' else -lots
        try:
            pos = FnOPosition.objects.get(portfolio=portfolio, instrument=instrument)
            old_lots = pos.lots
            new_lots = old_lots + lot_delta

            if new_lots == 0:
                # Full close — realize P&L and delete
                realized = (price - float(pos.avg_entry_price)) * old_lots * lot_size
                portfolio.realized_pnl = float(portfolio.realized_pnl) + realized
                # Refund margin
                portfolio.cash_balance = float(portfolio.cash_balance) + float(pos.margin_blocked)
                portfolio.save()
                FnOOrder.objects.create(
                    portfolio=portfolio, instrument=instrument,
                    order_type=order_side, lots=lots,
                    executed_price=price, status=FnOOrder.EXECUTED,
                    realized_pnl=round(realized, 2),
                )
                pos.delete()
                messages.success(request,
                    f'Position closed: {instrument.display_name} — '
                    f'P&L ₹{realized:+,.2f}')
                return redirect('paper_trading:fno_dashboard')

            elif (old_lots > 0) == (new_lots > 0):
                # Same direction — average up/down
                total_lots = old_lots + lot_delta
                pos.avg_entry_price = (
                    (float(pos.avg_entry_price) * abs(old_lots) + price * lots)
                    / abs(total_lots)
                )
                pos.lots = total_lots
                pos.margin_blocked = float(pos.margin_blocked) + margin
                pos.save()
            else:
                # Direction flip: partial close then open opposite
                closing_lots = abs(old_lots)
                realized = (price - float(pos.avg_entry_price)) * old_lots * lot_size
                portfolio.realized_pnl = float(portfolio.realized_pnl) + realized
                portfolio.cash_balance = float(portfolio.cash_balance) + float(pos.margin_blocked)
                portfolio.save()
                remaining = lots - closing_lots
                new_margin = get_margin(itype, order_side, spot, price, remaining, lot_size)
                if float(portfolio.cash_balance) < new_margin:
                    portfolio.cash_balance = float(portfolio.cash_balance) + margin
                    portfolio.save()
                    messages.error(request, 'Insufficient funds to open the opposite position.')
                    return redirect('paper_trading:fno_order')
                portfolio.cash_balance = float(portfolio.cash_balance) - new_margin
                portfolio.save()
                pos.lots = lot_delta + old_lots
                pos.avg_entry_price = price
                pos.margin_blocked = new_margin
                pos.save()

        except FnOPosition.DoesNotExist:
            FnOPosition.objects.create(
                portfolio=portfolio, instrument=instrument,
                lots=lot_delta,
                avg_entry_price=price,
                last_price=price,
                margin_blocked=margin,
            )

        FnOOrder.objects.create(
            portfolio=portfolio, instrument=instrument,
            order_type=order_side, lots=lots,
            executed_price=price, status=FnOOrder.EXECUTED,
        )

        label = 'Long' if order_side == 'BUY' else 'Short'
        messages.success(
            request,
            f'{label} {lots} lot(s) of {instrument.display_name} @ ₹{price:,.2f} — '
            f'₹{margin:,.2f} {"premium" if itype != "FUT" and order_side == "BUY" else "margin"} blocked.'
        )
        return redirect('paper_trading:fno_dashboard')

    # ── GET ──
    underlying  = request.GET.get('underlying', 'NIFTY').upper()
    itype       = request.GET.get('type', 'CE')
    expiries    = get_upcoming_expiries()
    expiry_str  = request.GET.get('expiry', expiries[0].isoformat() if expiries else '')

    spot        = _get_spot(underlying)
    strikes     = get_atm_strikes(spot, n=10) if spot else []
    lot_size    = LOT_SIZES.get(underlying, DEFAULT_LOT_SIZE)

    # Pre-price the ATM strike for display
    atm_price = None
    if spot and strikes and itype in ('CE', 'PE'):
        try:
            from datetime import date
            exp = date.fromisoformat(expiry_str)
            atm_strike = strikes[len(strikes) // 2]
            pd = get_fno_price(underlying, itype, atm_strike, exp, spot)
            atm_price = pd['price']
        except Exception:
            pass

    context = {
        'portfolio':   portfolio,
        'underlying':  underlying,
        'itype':       itype,
        'expiries':    expiries,
        'expiry_str':  expiry_str,
        'spot':        spot,
        'strikes':     strikes,
        'lot_size':    lot_size,
        'atm_price':   atm_price,
    }
    return render(request, 'paper_trading/fno_order.html', context)


# ─── F&O close position ───────────────────────────────────────────────────────

@login_required
def fno_close_position(request, position_id):
    portfolio = get_or_create_portfolio(request)
    pos = get_object_or_404(FnOPosition, id=position_id, portfolio=portfolio)

    spot = _get_spot(pos.instrument.underlying)
    if spot is None:
        messages.error(request, 'Could not fetch current price to close position.')
        return redirect('paper_trading:fno_dashboard')

    from datetime import date
    pd = get_fno_price(
        pos.instrument.underlying,
        pos.instrument.instrument_type,
        float(pos.instrument.strike) if pos.instrument.strike else None,
        pos.instrument.expiry,
        spot,
    )
    close_price = pd['price']
    realized    = (close_price - float(pos.avg_entry_price)) * pos.lots * pos.instrument.lot_size

    portfolio.realized_pnl  = float(portfolio.realized_pnl) + realized
    portfolio.cash_balance  = float(portfolio.cash_balance) + float(pos.margin_blocked)
    portfolio.save()

    close_order_type = 'SELL' if pos.lots > 0 else 'BUY'
    FnOOrder.objects.create(
        portfolio=portfolio, instrument=pos.instrument,
        order_type=close_order_type, lots=abs(pos.lots),
        executed_price=close_price, status=FnOOrder.EXECUTED,
        realized_pnl=round(realized, 2),
    )
    pos.delete()

    messages.success(
        request,
        f'Closed {pos.instrument.display_name} — P&L ₹{realized:+,.2f}'
    )
    return redirect('paper_trading:fno_dashboard')


# ─── F&O order history ────────────────────────────────────────────────────────

@login_required
def fno_order_history(request):
    portfolio = get_or_create_portfolio(request)
    orders    = portfolio.fno_orders.select_related('instrument').all()
    return render(request, 'paper_trading/fno_order_history.html', {
        'portfolio': portfolio,
        'orders':    orders,
    })


# ─── F&O price API ────────────────────────────────────────────────────────────

def fno_price_api(request):
    """
    GET params: underlying, type (CE/PE/FUT), strike (optional), expiry (YYYY-MM-DD)
    Returns: price, Greeks, spot, days_to_expiry, margin_buy, margin_sell
    """
    underlying = request.GET.get('underlying', '').upper()
    itype      = request.GET.get('type', 'CE')
    expiry_str = request.GET.get('expiry', '')
    strike_str = request.GET.get('strike', '')

    if not underlying or not expiry_str:
        return JsonResponse({'error': 'underlying and expiry required'}, status=400)

    try:
        from datetime import date
        expiry = date.fromisoformat(expiry_str)
    except ValueError:
        return JsonResponse({'error': 'Invalid expiry date'}, status=400)

    strike = float(strike_str) if strike_str else None
    if itype in ('CE', 'PE') and not strike:
        return JsonResponse({'error': 'strike required for options'}, status=400)

    spot = _get_spot(underlying)
    if spot is None:
        return JsonResponse({'error': f'Cannot fetch price for {underlying}'}, status=200)

    lot_size = LOT_SIZES.get(underlying, DEFAULT_LOT_SIZE)
    pd = get_fno_price(underlying, itype, strike, expiry, spot)
    price = pd['price']

    return JsonResponse({
        **pd,
        'lot_size':    lot_size,
        'margin_buy':  get_margin(itype, 'BUY',  spot, price, 1, lot_size),
        'margin_sell': get_margin(itype, 'SELL', spot, price, 1, lot_size),
    })


# ─── F&O expiries & strikes API ───────────────────────────────────────────────

def fno_expiries_api(request):
    expiries = [d.isoformat() for d in get_upcoming_expiries()]
    return JsonResponse({'expiries': expiries})


def fno_strikes_api(request):
    underlying = request.GET.get('underlying', '').upper()
    if not underlying:
        return JsonResponse({'error': 'underlying required'})
    spot = _get_spot(underlying)
    if spot is None:
        return JsonResponse({'error': f'No data for {underlying}'})
    return JsonResponse({
        'spot':     spot,
        'strikes':  get_atm_strikes(spot, n=12),
        'lot_size': LOT_SIZES.get(underlying, DEFAULT_LOT_SIZE),
    })
