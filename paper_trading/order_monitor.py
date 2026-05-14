"""
Background thread that polls pending LIMIT and STOP-LOSS orders every few seconds
and executes them when their price condition is met.

Started once via PaperTradingConfig.ready().
"""
import threading
import time
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 7   # seconds


def _execute_pending_orders():
    """Check all PENDING orders and execute those whose condition is met."""
    import django
    from django.utils import timezone
    from .models import PaperOrder, PaperPortfolio
    from .price_engine import price_engine

    pending = PaperOrder.objects.filter(status=PaperOrder.PENDING).select_related('portfolio')

    for order in pending:
        sym = order.symbol
        cached = price_engine.get(sym)
        if not cached:
            price_engine.watch(sym)
            continue

        current_price = Decimal(str(cached['price']))

        execute = False
        exec_price = current_price

        if order.price_type == PaperOrder.LIMIT:
            if order.order_type == PaperOrder.BUY and current_price <= order.limit_price:
                execute = True
                exec_price = order.limit_price
            elif order.order_type == PaperOrder.SELL and current_price >= order.limit_price:
                execute = True
                exec_price = order.limit_price

        elif order.price_type == PaperOrder.STOP_LOSS:
            if order.order_type == PaperOrder.SELL and current_price <= order.stop_price:
                # Convert to market execution at current price
                execute = True
                exec_price = current_price

        if not execute:
            continue

        portfolio = order.portfolio
        total = float(exec_price) * order.quantity

        try:
            if order.order_type == PaperOrder.BUY:
                if float(portfolio.cash_balance) < total:
                    order.status = PaperOrder.REJECTED
                    order.rejection_reason = 'Insufficient funds at time of limit execution.'
                    order.save()
                    continue

                from .models import PaperPosition
                portfolio.cash_balance = float(portfolio.cash_balance) - total
                portfolio.save()

                position, created = PaperPosition.objects.get_or_create(
                    portfolio=portfolio,
                    symbol=sym,
                    defaults={
                        'company_name': order.company_name,
                        'quantity': 0,
                        'avg_buy_price': exec_price,
                        'last_price': exec_price,
                    }
                )
                if not created:
                    total_qty = position.quantity + order.quantity
                    position.avg_buy_price = Decimal(str(
                        (float(position.avg_buy_price) * position.quantity + float(exec_price) * order.quantity)
                        / total_qty
                    ))
                    position.quantity = total_qty
                else:
                    position.quantity = order.quantity
                position.last_price = exec_price
                position.save()

            elif order.order_type == PaperOrder.SELL:
                from .models import PaperPosition
                try:
                    position = PaperPosition.objects.get(portfolio=portfolio, symbol=sym)
                except PaperPosition.DoesNotExist:
                    order.status = PaperOrder.REJECTED
                    order.rejection_reason = f'Position in {sym} no longer exists.'
                    order.save()
                    continue

                if position.quantity < order.quantity:
                    order.status = PaperOrder.REJECTED
                    order.rejection_reason = f'Insufficient shares at time of execution.'
                    order.save()
                    continue

                # Realized P&L
                realized = (float(exec_price) - float(position.avg_buy_price)) * order.quantity
                portfolio.realized_pnl = float(portfolio.realized_pnl) + realized
                portfolio.cash_balance = float(portfolio.cash_balance) + total
                portfolio.save()

                position.quantity -= order.quantity
                position.last_price = exec_price
                if position.quantity == 0:
                    position.delete()
                else:
                    position.save()

            order.status = PaperOrder.EXECUTED
            order.executed_price = exec_price
            order.executed_at = timezone.now()
            order.save()
            logger.info('Order %s executed: %s %s x %s @ %s', order.id, order.order_type, order.quantity, sym, exec_price)

        except Exception as exc:
            logger.exception('Error executing order %s: %s', order.id, exc)


def _monitor_loop():
    # Wait for Django to fully initialize before touching ORM
    import django
    time.sleep(5)
    while True:
        try:
            _execute_pending_orders()
        except Exception as exc:
            logger.exception('order_monitor loop error: %s', exc)
        time.sleep(CHECK_INTERVAL)


def start():
    t = threading.Thread(target=_monitor_loop, daemon=True, name='order-monitor')
    t.start()
    logger.info('order_monitor started')
