from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('paper_trading', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaperPortfolioSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
                ('portfolio_value', models.DecimalField(decimal_places=2, max_digits=15)),
                ('cash_balance', models.DecimalField(decimal_places=2, max_digits=15)),
                ('total_invested', models.DecimalField(decimal_places=2, max_digits=15)),
                ('total_pnl', models.DecimalField(decimal_places=2, max_digits=15)),
                ('portfolio', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='snapshots',
                    to='paper_trading.paperportfolio',
                )),
            ],
            options={
                'ordering': ['recorded_at'],
            },
        ),
    ]
