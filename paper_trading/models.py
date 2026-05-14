from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

STARTING_BALANCE = 1_000_000  # ₹10,00,000


class PaperPortfolio(models.Model):
    user        = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='paper_portfolio')
    session_key = models.CharField(max_length=40, unique=True, null=True, blank=True)
    cash_balance  = models.DecimalField(max_digits=15, decimal_places=2, default=STARTING_BALANCE)
    realized_pnl  = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    created_at    = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        label = self.user.username if self.user_id else (self.session_key or str(self.pk))
        return f"{label} — ₹{self.cash_balance}"

    @property
    def total_invested(self):
        return sum(p.invested_value for p in self.positions.all())

    @property
    def total_current_value(self):
        return sum(p.current_value for p in self.positions.all())

    @property
    def unrealized_pnl(self):
        return self.total_current_value - self.total_invested

    # Keep backward-compat alias
    @property
    def total_pnl(self):
        return self.unrealized_pnl

    @property
    def total_portfolio_value(self):
        return float(self.cash_balance) + self.total_current_value

    @property
    def portfolio_return_pct(self):
        """Overall return % vs starting balance."""
        gain = self.total_portfolio_value - STARTING_BALANCE
        return round(gain / STARTING_BALANCE * 100, 2)

    @property
    def daily_pnl(self):
        """Today's P&L = today's snapshot value minus yesterday's snapshot value."""
        today = timezone.localdate()
        snaps = self.snapshots.filter(recorded_at__date__lte=today).order_by('-recorded_at')
        snaps_list = list(snaps[:2])
        if len(snaps_list) < 2:
            return 0
        return float(snaps_list[0].portfolio_value) - float(snaps_list[1].portfolio_value)


class PaperPortfolioSnapshot(models.Model):
    """Daily NAV snapshot — records portfolio value and P&L at a point in time."""
    portfolio      = models.ForeignKey(PaperPortfolio, on_delete=models.CASCADE, related_name='snapshots')
    recorded_at    = models.DateTimeField(auto_now_add=True)
    portfolio_value = models.DecimalField(max_digits=15, decimal_places=2)
    cash_balance   = models.DecimalField(max_digits=15, decimal_places=2)
    total_invested = models.DecimalField(max_digits=15, decimal_places=2)
    total_pnl      = models.DecimalField(max_digits=15, decimal_places=2)

    class Meta:
        ordering = ['recorded_at']

    def __str__(self):
        label = self.portfolio.user.username if self.portfolio.user_id else str(self.portfolio.pk)
        return f"{label} snapshot @ {self.recorded_at:%Y-%m-%d %H:%M}"


class PaperPosition(models.Model):
    portfolio    = models.ForeignKey(PaperPortfolio, on_delete=models.CASCADE, related_name='positions')
    symbol       = models.CharField(max_length=20)
    company_name = models.CharField(max_length=100, blank=True)
    quantity     = models.IntegerField(default=0)
    avg_buy_price = models.DecimalField(max_digits=12, decimal_places=2)
    last_price   = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('portfolio', 'symbol')

    def __str__(self):
        return f"{self.symbol} x {self.quantity}"

    @property
    def invested_value(self):
        return float(self.avg_buy_price) * self.quantity

    @property
    def current_value(self):
        return float(self.last_price) * self.quantity

    @property
    def pnl(self):
        return self.current_value - self.invested_value

    @property
    def pnl_percent(self):
        if self.invested_value == 0:
            return 0
        return (self.pnl / self.invested_value) * 100


class PaperOrder(models.Model):
    BUY  = 'BUY'
    SELL = 'SELL'
    ORDER_TYPES = [(BUY, 'Buy'), (SELL, 'Sell')]

    MARKET    = 'MARKET'
    LIMIT     = 'LIMIT'
    STOP_LOSS = 'SL'
    PRICE_TYPES = [(MARKET, 'Market'), (LIMIT, 'Limit'), (STOP_LOSS, 'Stop Loss')]

    PENDING   = 'PENDING'
    EXECUTED  = 'EXECUTED'
    REJECTED  = 'REJECTED'
    CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (PENDING,   'Pending'),
        (EXECUTED,  'Executed'),
        (REJECTED,  'Rejected'),
        (CANCELLED, 'Cancelled'),
    ]

    portfolio      = models.ForeignKey(PaperPortfolio, on_delete=models.CASCADE, related_name='orders')
    symbol         = models.CharField(max_length=20)
    company_name   = models.CharField(max_length=100, blank=True)
    order_type     = models.CharField(max_length=4, choices=ORDER_TYPES)
    price_type     = models.CharField(max_length=6, choices=PRICE_TYPES, default=MARKET)
    quantity       = models.IntegerField()
    limit_price    = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    stop_price     = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    executed_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status         = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    rejection_reason = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    executed_at    = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.order_type} {self.quantity} {self.symbol} @ {self.executed_price or self.limit_price or self.stop_price}"

    @property
    def total_value(self):
        price = self.executed_price or self.limit_price or self.stop_price or 0
        return float(price) * self.quantity


# ── F&O models ────────────────────────────────────────────────────────────────

LOT_SIZES = {
    'NIFTY': 75, 'BANKNIFTY': 30, 'FINNIFTY': 65,
    'MIDCPNIFTY': 75, 'SENSEX': 10, 'BANKEX': 15,
    'RELIANCE': 250, 'TCS': 150, 'INFY': 300, 'HDFCBANK': 550,
    'ICICIBANK': 700, 'SBIN': 1500, 'AXISBANK': 625, 'TATAMOTORS': 900,
    'BAJFINANCE': 125, 'BHARTIARTL': 475, 'WIPRO': 1600, 'HCLTECH': 350,
    'KOTAKBANK': 400, 'LT': 150, 'MARUTI': 75, 'ADANIENT': 400,
    'HINDUNILVR': 300, 'NTPC': 3000, 'POWERGRID': 3450, 'ONGC': 1925,
}
DEFAULT_LOT_SIZE = 100
FUTURES_MARGIN_PCT = 0.15   # 15 % of notional


class FnOInstrument(models.Model):
    CALL   = 'CE'
    PUT    = 'PE'
    FUTURE = 'FUT'
    TYPES  = [(CALL, 'Call'), (PUT, 'Put'), (FUTURE, 'Future')]

    underlying      = models.CharField(max_length=20)
    instrument_type = models.CharField(max_length=3, choices=TYPES)
    expiry          = models.DateField()
    strike          = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    lot_size        = models.PositiveIntegerField(default=DEFAULT_LOT_SIZE)

    class Meta:
        unique_together = ('underlying', 'instrument_type', 'expiry', 'strike')

    @property
    def display_name(self):
        exp = self.expiry.strftime('%d %b %y').upper()
        if self.instrument_type == self.FUTURE:
            return f"{self.underlying} {exp} FUT"
        return f"{self.underlying} {exp} {int(self.strike)} {self.instrument_type}"

    def __str__(self):
        return self.display_name


class FnOPosition(models.Model):
    portfolio       = models.ForeignKey(PaperPortfolio, on_delete=models.CASCADE, related_name='fno_positions')
    instrument      = models.ForeignKey(FnOInstrument, on_delete=models.CASCADE)
    lots            = models.IntegerField()          # positive = long, negative = short
    avg_entry_price = models.DecimalField(max_digits=12, decimal_places=4)
    last_price      = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    margin_blocked  = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('portfolio', 'instrument')

    @property
    def unrealized_pnl(self):
        return (float(self.last_price) - float(self.avg_entry_price)) * self.lots * self.instrument.lot_size

    @property
    def notional_value(self):
        return float(self.last_price) * abs(self.lots) * self.instrument.lot_size

    def __str__(self):
        direction = 'LONG' if self.lots > 0 else 'SHORT'
        return f"{direction} {abs(self.lots)} lots {self.instrument}"


class FnOOrder(models.Model):
    BUY  = 'BUY'
    SELL = 'SELL'
    ORDER_TYPES = [(BUY, 'Buy'), (SELL, 'Sell')]

    EXECUTED = 'EXECUTED'
    REJECTED = 'REJECTED'
    STATUSES = [(EXECUTED, 'Executed'), (REJECTED, 'Rejected')]

    portfolio       = models.ForeignKey(PaperPortfolio, on_delete=models.CASCADE, related_name='fno_orders')
    instrument      = models.ForeignKey(FnOInstrument, on_delete=models.CASCADE)
    order_type      = models.CharField(max_length=4, choices=ORDER_TYPES)
    lots            = models.IntegerField()
    executed_price  = models.DecimalField(max_digits=12, decimal_places=4)
    status          = models.CharField(max_length=10, choices=STATUSES, default=EXECUTED)
    rejection_reason = models.TextField(blank=True)
    realized_pnl    = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def premium(self):
        return float(self.executed_price) * abs(self.lots) * self.instrument.lot_size

    def __str__(self):
        return f"{self.order_type} {self.lots}L {self.instrument} @ {self.executed_price}"
