# Per-unit smart baselines

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_rename_core_auditl_propert_0a8f2d_idx_core_auditl_propert_1db7c3_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='meterreading',
            name='anomaly_type',
            field=models.CharField(
                blank=True,
                help_text='Machine-readable reason when is_anomaly is True',
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name='UnitMeterBaseline',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sample_size', models.PositiveSmallIntegerField(default=0)),
                ('mean_consumption', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('std_deviation', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('lower_bound', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('upper_bound', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('meter', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='usage_baseline',
                    to='core.meter',
                )),
            ],
            options={
                'verbose_name': 'Unit meter baseline',
            },
        ),
    ]
