import logging

from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from ..models import (
    AccountBalance,
    Invoice,
    MeterReading,
    Payment,
    PropertyManager,
    Tenant,
    TenantPreferences,
    Unit,
)
from .helpers import (
    recalculate_tenant_ledger,
    tenant_can_log_tokens,
)

logger = logging.getLogger(__name__)
User = get_user_model()

@login_required
def manager_dashboard(request):
    """
    Property Manager dashboard with statistics and quick actions.
    Shows key metrics like total units, readings, and active tenants.
    """
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied: You do not have Property Manager privileges.')
        return redirect('login')
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # Dashboard cards show current estate activity, not lifetime totals.
        total_units = Unit.objects.filter(manager=manager).count()
        active_tenants = Tenant.objects.filter(
            unit__manager=manager,
            is_active=True
        ).count()
        
        from django.utils import timezone
        current_month = timezone.now().month
        current_year = timezone.now().year
        total_readings = MeterReading.objects.filter(
            meter__unit__manager=manager,
            reading_date__month=current_month,
            reading_date__year=current_year
        ).count()
        
        context = {
            'total_units': total_units,
            'active_tenants': active_tenants,
            'total_readings': total_readings,
        }
        
        return render(request, 'core/manager_dashboard.html', context)
    
    except PropertyManager.DoesNotExist:
        logout(request)
        messages.error(request, "Property Manager profile not found. Please contact admin.")
        return redirect("login")

@login_required
def tenant_dashboard(request):
    """
    Tenant dashboard to view personal utility usage and invoices.
    """
    if request.user.role != 'TENANT':
        messages.error(request, 'Access denied: You do not have Tenant privileges.')
        return redirect('login')
    
    try:
        tenant = Tenant.objects.get(user=request.user)
        invoices = Invoice.objects.filter(tenant=tenant)
        current_balance = recalculate_tenant_ledger(tenant)

        latest_invoice = invoices.order_by('-invoice_date').first()
        unpaid_invoices = invoices.filter(status__in=['UNPAID', 'PARTIALLY_PAID', 'OVERDUE'])
        overdue_count = unpaid_invoices.filter(status='OVERDUE').count()
        account_balance = AccountBalance.objects.filter(tenant=tenant).first()
        recent_payments = Payment.objects.filter(invoice__tenant=tenant).order_by('-payment_date')[:3]
        recent_readings = MeterReading.objects.filter(
            meter__unit=tenant.unit
        ).exclude(
            verification_status='REJECTED'
        ).select_related('meter').order_by('-reading_date')[:4] if tenant.unit else []
        preferences, _ = TenantPreferences.objects.get_or_create(tenant=tenant)
        token_logging_available = tenant_can_log_tokens(tenant)

        context = {
            'tenant': tenant,
            'latest_invoice': latest_invoice,
            'open_invoice_count': unpaid_invoices.count(),
            'overdue_count': overdue_count,
            'account_balance': account_balance,
            'current_balance': current_balance,
            'abs_balance': abs(current_balance),
            'recent_payments': recent_payments,
            'recent_readings': recent_readings,
            'preferences': preferences,
            'token_logging_available': token_logging_available,
        }
        return render(request, 'core/tenant_dashboard.html', context)
        
    except Tenant.DoesNotExist:
        logout(request)
        messages.error(request, "Tenant profile not found.")
        return redirect("login")
