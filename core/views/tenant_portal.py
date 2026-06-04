import json
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..decorators import tenant_required
from ..models import (
    ElectricityToken,
    Invoice,
    MeterReading,
    Tenant,
    TenantPreferences,
)
from ..sms_utils import TokenSMS
from ..tenant_alerts import mark_high_consumption_readings, tenant_consumption_alerts
from .helpers import (
    recalculate_tenant_ledger,
    tenant_can_log_tokens,
)

logger = logging.getLogger(__name__)
User = get_user_model()

@tenant_required
def tenant_invoices(request):
    """
    Display tenant's invoice history.
    Tenants can view all their invoices.
    """
    # Security check
    
    try:
        tenant = Tenant.objects.get(user=request.user)
        
        # Get all invoices for this tenant
        current_balance = recalculate_tenant_ledger(tenant)
        invoices = Invoice.objects.filter(tenant=tenant).order_by('-invoice_date')
        
        # Filter by status if specified
        status_filter = request.GET.get('status', '').strip().upper()
        if status_filter:
            invoices = invoices.filter(status=status_filter)
        
        # Get account balance
        abs_balance = abs(current_balance)

        # Pagination
        from django.core.paginator import Paginator
        paginator = Paginator(invoices, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        
        context = {
            'page_obj': page_obj,
            'current_balance': current_balance,
            'abs_balance': abs_balance,
            'status_filter': status_filter,
        }
        
        return render(request, 'core/tenant_invoices.html', context)
    
    except Tenant.DoesNotExist:
        messages.error(request, 'Tenant profile not found')
        return redirect('login')

@tenant_required
def tenant_consumption_history(request):
    """
    Display tenant's consumption history.
    Shows meter readings for their unit.
    """
    # Security check
    
    try:
        tenant = Tenant.objects.get(user=request.user)
        unit = tenant.unit
        
        if not unit:
            messages.warning(request, 'No unit assigned to your account')
            return redirect('tenant_dashboard')
        
        # Get meter type filter
        meter_type = request.GET.get('meter_type', 'WATER')
        
        # Get readings for this unit's meters
        readings = list(MeterReading.objects.filter(
            meter__unit=unit,
            meter__meter_type=meter_type
        ).exclude(
            verification_status='REJECTED'
        ).order_by('-reading_date')[:12])  # Last 12 readings

        high_consumption_alerts = tenant_consumption_alerts(
            tenant,
            meter_type=meter_type,
        )
        mark_high_consumption_readings(readings, high_consumption_alerts)
        
        # Prepare chart data
        chart_labels = []
        chart_data = []
        
        for reading in reversed(readings):
            chart_labels.append(reading.reading_date.strftime('%b %d'))
            chart_data.append(float(reading.consumption))
        
        context = {
            'readings': readings,
            'meter_type': meter_type,
            'unit': unit,
            'high_consumption_alerts': high_consumption_alerts,
            'chart_labels': json.dumps(chart_labels),
            'chart_data': json.dumps(chart_data),
        }
        
        return render(request, 'core/tenant_consumption_history.html', context)
    
    except Tenant.DoesNotExist:
        messages.error(request, 'Tenant profile not found')
        return redirect('login')

@tenant_required
def tenant_preferences(request):
    """
    Manage tenant preferences and feature toggles.
    """
    # Security check
    
    try:
        tenant = Tenant.objects.get(user=request.user)
        
        # Get or create preferences
        preferences, created = TenantPreferences.objects.get_or_create(
            tenant=tenant,
            defaults={
                'enable_token_logging': False,
                'enable_sms_notifications': True,
                'enable_email_notifications': True,
                'show_consumption_alerts': True
            }
        )
        token_logging_available = tenant_can_log_tokens(tenant)
        if not token_logging_available and preferences.enable_token_logging:
            preferences.enable_token_logging = False
            preferences.save(update_fields=['enable_token_logging', 'updated_at'])
        
        if request.method == 'POST':
            # Update preferences
            preferences.enable_token_logging = (
                token_logging_available and request.POST.get('enable_token_logging') == 'on'
            )
            preferences.enable_sms_notifications = request.POST.get('enable_sms_notifications') == 'on'
            preferences.enable_email_notifications = request.POST.get('enable_email_notifications') == 'on'
            preferences.show_consumption_alerts = request.POST.get('show_consumption_alerts') == 'on'
            
            # Update phone number
            phone_number = request.POST.get('phone_number', '').strip()
            if phone_number:
                tenant.phone_number = phone_number
                tenant.save()
            
            preferences.save()
            
            messages.success(request, '✓ Preferences updated successfully')
            return redirect('tenant_preferences')
        
        context = {
            'tenant': tenant,
            'preferences': preferences,
            'token_logging_available': token_logging_available,
        }
        
        return render(request, 'core/tenant_preferences.html', context)
    
    except Tenant.DoesNotExist:
        messages.error(request, 'Tenant profile not found')
        return redirect('login')

@tenant_required
def electricity_tokens(request):
    """
    View and manage electricity tokens.
    """
    # Security check
    
    try:
        tenant = Tenant.objects.get(user=request.user)
        
        # Check if feature is enabled
        preferences = TenantPreferences.objects.filter(tenant=tenant).first()
        token_logging_available = tenant_can_log_tokens(tenant)
        if not token_logging_available or not preferences or not preferences.enable_token_logging:
            return render(request, 'core/electricity_tokens_disabled.html', {
                'tenant': tenant,
                'token_logging_available': token_logging_available,
                'preferences': preferences,
            })
        
        # Get all tokens
        tokens = ElectricityToken.objects.filter(tenant=tenant).order_by('-purchase_date')
        
        # Calculate statistics
        total_tokens = tokens.count()
        total_units = tokens.aggregate(total=Sum('units'))['total'] or Decimal('0.00')
        total_spent = tokens.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Pagination
        from django.core.paginator import Paginator
        paginator = Paginator(tokens, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        
        context = {
            'page_obj': page_obj,
            'total_tokens': total_tokens,
            'total_units': total_units,
            'total_spent': total_spent,
        }
        
        return render(request, 'core/electricity_tokens.html', context)
    
    except Tenant.DoesNotExist:
        messages.error(request, 'Tenant profile not found')
        return redirect('login')

@tenant_required
def add_electricity_token(request):
    """
    Log a new electricity token.
    """
    # Security check
    
    try:
        tenant = Tenant.objects.get(user=request.user)
        
        # Check if feature is enabled
        preferences = TenantPreferences.objects.filter(tenant=tenant).first()
        if not tenant_can_log_tokens(tenant) or not preferences or not preferences.enable_token_logging:
            messages.warning(request, 'Please enable token logging in your preferences first')
            return redirect('electricity_tokens')
        
        if request.method == 'POST':
            token_number = request.POST.get('token_number').strip()
            units = request.POST.get('units')
            amount = request.POST.get('amount')
            expiry_date = request.POST.get('expiry_date')
            vendor = request.POST.get('vendor', '').strip()
            notes = request.POST.get('notes', '').strip()
            
            # Create token
            token = ElectricityToken.objects.create(
                tenant=tenant,
                token_number=token_number,
                units=units,
                amount=amount,
                expiry_date=expiry_date if expiry_date else None,
                vendor=vendor,
                notes=notes
            )
            
            # Send SMS notification if enabled
            if preferences.enable_sms_notifications and tenant.phone_number:
                try:
                    sms_notifier = TokenSMS()
                    sms_notifier.send_token_notification(token)
                except Exception as e:
                    print(f"SMS error: {str(e)}")
            
            messages.success(request, f'✓ Token {token_number} logged successfully!')
            return redirect('electricity_tokens')
        
        context = {
            'today': timezone.now().date(),
        }
        
        return render(request, 'core/add_electricity_token.html', context)
    
    except Tenant.DoesNotExist:
        messages.error(request, 'Tenant profile not found')
        return redirect('login')

@tenant_required
def delete_electricity_token(request, token_id):
    """
    Delete an electricity token.
    """
    # Security check
    
    try:
        tenant = Tenant.objects.get(user=request.user)
        token = get_object_or_404(ElectricityToken, id=token_id, tenant=tenant)
        
        if request.method == 'POST':
            token_number = token.token_number
            token.delete()
            messages.success(request, f'✓ Token {token_number} deleted')
        return redirect('electricity_tokens')
    
    except Tenant.DoesNotExist:
        messages.error(request, 'Tenant profile not found')
        return redirect('login')
