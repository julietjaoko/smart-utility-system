from django.contrib import admin
from .models import (
    User, PropertyManager, Unit, Tenant, 
    Meter, MeterReading
)

# Customize User Admin
@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """
    Admin interface for User model.
    Shows key fields and allows filtering by role.
    """
    list_display = ['username', 'email', 'role', 'is_active', 'date_joined']
    list_filter = ['role', 'is_active']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering = ['-date_joined']


@admin.register(PropertyManager)
class PropertyManagerAdmin(admin.ModelAdmin):
    """Admin interface for Property Manager model."""
    list_display = ['user', 'estate_name']
    search_fields = ['user__username', 'estate_name']


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    """Admin interface for Unit model."""
    list_display = ['unit_number', 'estate_name', 'manager', 'has_water_meter', 'has_electricity_meter']
    list_filter = ['has_water_meter', 'has_electricity_meter', 'estate_name']
    search_fields = ['unit_number', 'estate_name']


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    """Admin interface for Tenant model."""
    list_display = ['user', 'unit', 'move_in_date']
    list_filter = ['move_in_date']
    search_fields = ['user__username', 'unit__unit_number']


@admin.register(Meter)
class MeterAdmin(admin.ModelAdmin):
    """Admin interface for Meter model."""
    list_display = ['unit', 'meter_type', 'meter_number', 'is_active']
    list_filter = ['meter_type', 'is_active']
    search_fields = ['meter_number', 'unit__unit_number']


@admin.register(MeterReading)
class MeterReadingAdmin(admin.ModelAdmin):
    """
    Admin interface for Meter Reading model.
    Shows consumption and highlights anomalies.
    """
    list_display = [
        'meter', 'reading_date', 'reading_value', 
        'consumption', 'is_anomaly', 'recorded_by'
    ]
    list_filter = ['is_anomaly', 'meter__meter_type', 'reading_date']
    search_fields = ['meter__unit__unit_number', 'notes']
    readonly_fields = ['consumption', 'is_anomaly', 'created_at']
    ordering = ['-reading_date']
    
    # Highlight anomalies in red
    def get_list_display_links(self, request, list_display):
        return ['meter']
    
from .models import RateConfig, FixedCharge, Invoice, Payment, AccountBalance

@admin.register(RateConfig)
class RateConfigAdmin(admin.ModelAdmin):
    list_display = ['utility_type', 'rate_per_unit', 'effective_from', 'is_active']
    list_filter = ['utility_type', 'is_active']

@admin.register(FixedCharge)
class FixedChargeAdmin(admin.ModelAdmin):
    list_display = ['charge_name', 'amount', 'effective_from', 'is_active']
    list_filter = ['is_active']

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['invoice_number', 'unit', 'billing_period', 'total_due', 'status', 'invoice_date']
    list_filter = ['status', 'invoice_date']
    search_fields = ['invoice_number', 'unit__unit_number']
    readonly_fields = ['invoice_number', 'created_at', 'updated_at']

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['invoice', 'payment_date', 'amount_paid', 'payment_method', 'mpesa_reference']
    list_filter = ['payment_method', 'payment_date']
    search_fields = ['invoice__invoice_number', 'mpesa_reference']

@admin.register(AccountBalance)
class AccountBalanceAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'current_balance', 'last_updated']
    readonly_fields = ['last_updated']

from .models import ElectricityToken, TenantPreferences

@admin.register(ElectricityToken)
class ElectricityTokenAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'token_number', 'units', 'amount', 'purchase_date', 'vendor']
    list_filter = ['purchase_date', 'vendor']
    search_fields = ['token_number', 'tenant__user__username', 'tenant__user__email']
    readonly_fields = ['purchase_date']
    date_hierarchy = 'purchase_date'

@admin.register(TenantPreferences)
class TenantPreferencesAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'enable_token_logging', 'enable_sms_notifications', 'enable_email_notifications', 'updated_at']
    list_filter = ['enable_token_logging', 'enable_sms_notifications', 'enable_email_notifications']
    search_fields = ['tenant__user__username', 'tenant__user__email']
    readonly_fields = ['updated_at']