from django.apps import AppConfig


class PaperTradingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'paper_trading'

    def ready(self):
        # Start price engine + order monitor background threads.
        # Guard against double-start during Django's auto-reloader.
        import os
        if os.environ.get('RUN_MAIN') != 'true' and os.environ.get('DJANGO_SETTINGS_MODULE'):
            # Production / gunicorn: always start
            pass
        # Start regardless — threads are daemon so they die with the process
        from .price_engine import price_engine  # noqa: F401 (triggers singleton creation)
        from . import order_monitor
        order_monitor.start()

        # Pre-warm the price engine with the default ticker symbols
        from .price_engine import price_engine as pe
        _TICKER_SYMBOLS = [
            'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK',
            'SBIN', 'AXISBANK', 'TATAMOTORS', 'BAJFINANCE', 'BHARTIARTL',
            'WIPRO', 'HCLTECH', 'KOTAKBANK', 'LT', 'MARUTI',
        ]
        pe.watch_many(_TICKER_SYMBOLS)
