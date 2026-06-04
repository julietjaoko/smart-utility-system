# Generated manually for AuditLog model

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_propertymanager_electricity_anomaly_threshold_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('category', models.CharField(choices=[('AUTH', 'Authentication'), ('BILLING', 'Billing'), ('PAYMENT', 'Payment'), ('READING', 'Meter Reading'), ('TENANT', 'Tenant Management'), ('UNIT', 'Unit Management'), ('SYSTEM', 'System Administration'), ('MAINTENANCE', 'Maintenance')], db_index=True, max_length=20)),
                ('action', models.CharField(db_index=True, max_length=64)),
                ('message', models.TextField()),
                ('severity', models.CharField(choices=[('INFO', 'Info'), ('WARNING', 'Warning'), ('CRITICAL', 'Critical')], default='INFO', max_length=10)),
                ('object_type', models.CharField(blank=True, max_length=50)),
                ('object_id', models.PositiveIntegerField(blank=True, null=True)),
                ('object_repr', models.CharField(blank=True, max_length=255)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='audit_logs', to=settings.AUTH_USER_MODEL)),
                ('property_manager', models.ForeignKey(blank=True, help_text='Scopes manager-visible logs to one estate portfolio', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='audit_logs', to='core.propertymanager')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['property_manager', '-created_at'], name='core_auditl_propert_0a8f2d_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['category', '-created_at'], name='core_auditl_categor_6e0b0a_idx'),
        ),
    ]
