from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('paper_trading', '0002_paperportfoliosnapshot'),
    ]

    operations = [
        # Add realized_pnl to PaperPortfolio
        migrations.AddField(
            model_name='paperportfolio',
            name='realized_pnl',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=15),
        ),
        # Add stop_price to PaperOrder
        migrations.AddField(
            model_name='paperorder',
            name='stop_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        # Update price_type choices to include SL
        migrations.AlterField(
            model_name='paperorder',
            name='price_type',
            field=models.CharField(
                choices=[('MARKET', 'Market'), ('LIMIT', 'Limit'), ('SL', 'Stop Loss')],
                default='MARKET',
                max_length=6,
            ),
        ),
    ]
