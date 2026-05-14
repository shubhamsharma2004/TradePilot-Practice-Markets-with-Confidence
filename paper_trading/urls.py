from django.urls import path
from . import views

app_name = 'paper_trading'

urlpatterns = [
    # ── Equity ──────────────────────────────────────────────────────────────
    path('',                   views.dashboard,           name='dashboard'),
    path('order/',             views.place_order,         name='place_order'),
    path('orders/',            views.order_history,       name='order_history'),
    path('orders/<int:order_id>/cancel/', views.cancel_order, name='cancel_order'),
    path('analytics/',         views.analytics,           name='analytics'),
    path('simulator/',         views.what_if_simulator,   name='simulator'),
    # Equity AJAX
    path('api/search/',        views.stock_search_api,    name='stock_search'),
    path('api/price/',         views.stock_price_api,     name='stock_price'),
    path('api/ohlc/',          views.stock_ohlc_api,      name='stock_ohlc'),
    path('api/portfolio-prices/', views.portfolio_prices_api, name='portfolio_prices'),
    path('api/ticker/',        views.ticker_api,          name='ticker'),
    path('api/nav-history/',   views.nav_history_api,     name='nav_history'),
    path('api/what-if/',       views.what_if_api,         name='what_if'),

    # ── F&O ─────────────────────────────────────────────────────────────────
    path('fno/',                        views.fno_dashboard,      name='fno_dashboard'),
    path('fno/order/',                  views.fno_order,          name='fno_order'),
    path('fno/orders/',                 views.fno_order_history,  name='fno_order_history'),
    path('fno/close/<int:position_id>/', views.fno_close_position, name='fno_close'),
    # F&O AJAX
    path('api/fno/price/',    views.fno_price_api,    name='fno_price'),
    path('api/fno/expiries/', views.fno_expiries_api, name='fno_expiries'),
    path('api/fno/strikes/',  views.fno_strikes_api,  name='fno_strikes'),
]
