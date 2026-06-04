import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..audit_log import log_audit
from ..decorators import system_admin_required
from ..forms import PropertyManagerCreationForm, PropertyManagerUpdateForm, TenantUpdateForm
from ..models import (
    FixedCharge,
    Invoice,
    Payment,
    PropertyManager,
    RateConfig,
    Tenant,
    Unit,
)

logger = logging.getLogger(__name__)
User = get_user_model()

@system_admin_required
def system_admin_dashboard(request):
    """Comprehensive System Admin Dashboard with financial analytics."""
    current_year = timezone.now().year
    
    # Core Platform Stats
    total_managers = PropertyManager.objects.count()
    total_tenants = Tenant.objects.count()
    active_tenants = Tenant.objects.filter(is_active=True).count()
    total_units = Unit.objects.count()
    total_invoices = Invoice.objects.count()

    # System-Wide Financials
    total_revenue_ytd = Payment.objects.filter(
        payment_date__year=current_year
    ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
    
    total_outstanding = Invoice.objects.filter(
        status__in=['UNPAID', 'PARTIALLY_PAID', 'OVERDUE']
    ).aggregate(total=Sum('total_due'))['total'] or Decimal('0.00')

    context = {
        "total_managers": total_managers,
        "total_tenants": total_tenants,
        "active_tenants": active_tenants,
        "total_units": total_units,
        "total_invoices": total_invoices,
        "total_revenue_ytd": total_revenue_ytd,
        "total_outstanding": total_outstanding,
        "current_year": current_year,
    }
    return render(request, "core/system_admin_dashboard.html", context)

@system_admin_required
def system_admin_manager_detail(request, manager_id):
    """Deep-dive view into a specific Property Manager's entire ecosystem."""
    manager = get_object_or_404(PropertyManager.objects.select_related('user'), id=manager_id)
    
    # Base Querysets
    units = Unit.objects.filter(manager=manager)
    tenants = Tenant.objects.filter(unit__manager=manager).select_related('user', 'unit')
    invoices = Invoice.objects.filter(unit__manager=manager)
    payments = Payment.objects.filter(invoice__unit__manager=manager)
    
    # Financial Analytics for this specific manager
    total_billed = invoices.aggregate(total=Sum('total_due'))['total'] or Decimal('0.00')
    total_collected = payments.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
    
    # Current Active Rates
    water_rate = RateConfig.objects.filter(manager=manager, utility_type='WATER', is_active=True).first()
    elec_rate = RateConfig.objects.filter(manager=manager, utility_type='ELECTRICITY', is_active=True).first()
    fixed_charges = FixedCharge.objects.filter(manager=manager, is_active=True)

    context = {
        'manager': manager,
        'total_units': units.count(),
        'active_tenants': tenants.filter(is_active=True).count(),
        'total_billed': total_billed,
        'total_collected': total_collected,
        'water_rate': water_rate,
        'elec_rate': elec_rate,
        'fixed_charges': fixed_charges,
        'recent_invoices': invoices.order_by('-invoice_date')[:5],
        'recent_payments': payments.order_by('-payment_date')[:5],
        'tenants': tenants, # Pass all tenants for the data table
    }
    return render(request, "core/system_admin_manager_detail.html", context)

@system_admin_required
def system_admin_toggle_tenant(request, tenant_id):
    """Allows System Admin to override and deactivate/activate a tenant."""
    tenant = get_object_or_404(Tenant, id=tenant_id)
    user = tenant.user
    
    # Toggle logic
    tenant.is_active = not tenant.is_active
    user.is_active = tenant.is_active
    
    tenant.save()
    user.save()
    
    status = "activated" if tenant.is_active else "deactivated"
    messages.success(request, f"Tenant {user.get_full_name()} has been {status}.")
    
    # Redirect back to the manager detail page they came from
    return redirect(request.META.get('HTTP_REFERER', 'system_admin_dashboard'))

@system_admin_required
def system_admin_managers(request):
    managers = PropertyManager.objects.select_related("user").order_by("estate_name")
    return render(request, "core/system_admin_managers.html", {"managers": managers})

@system_admin_required
def system_admin_create_manager(request):
    if request.method == "POST":
        form = PropertyManagerCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            log_audit(
                request=request,
                category='SYSTEM',
                action='MANAGER_CREATED',
                message=f'Created property manager {user.username}',
                object_type='User',
                object_id=user.id,
                object_repr=user.username,
            )
            messages.success(request, f"Property manager {user.get_full_name() or user.username} created successfully.")
            return redirect("system_admin_managers")
        messages.error(request, "Please correct the errors below.")
    else:
        form = PropertyManagerCreationForm()

    return render(request, "core/system_admin_create_manager.html", {"form": form})

@system_admin_required
def system_admin_toggle_user(request, user_id):
    user = get_object_or_404(get_user_model(), id=user_id)

    if user.role == "SYSTEM_ADMIN" and user == request.user:
        messages.error(request, "You cannot deactivate your own system admin account.")
        return redirect("system_admin_managers")

    user.is_active = not user.is_active
    user.save(update_fields=["is_active"])

    status = "activated" if user.is_active else "deactivated"
    log_audit(
        request=request,
        category='SYSTEM',
        action='USER_TOGGLED',
        message=f'{user.username} was {status} by system admin',
        object_type='User',
        object_id=user.id,
        object_repr=user.username,
        severity='WARNING' if not user.is_active else 'INFO',
    )
    messages.success(request, f"{user.username} has been {status}.")
    return redirect("system_admin_managers")

@system_admin_required
def system_admin_edit_manager(request, manager_id):
    """Allows System Admin to edit a property manager's details."""
    manager = get_object_or_404(PropertyManager, id=manager_id)
    
    if request.method == 'POST':
        form = PropertyManagerUpdateForm(request.POST, instance=manager)
        if form.is_valid():
            form.save()
            messages.success(request, f"✓ Property Manager '{manager.estate_name}' updated successfully.")
            return redirect('system_admin_manager_detail', manager_id=manager.id)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = PropertyManagerUpdateForm(instance=manager)
        
    return render(request, 'core/system_admin_edit_manager.html', {
        'form': form, 
        'manager': manager
    })

@system_admin_required
def system_admin_edit_tenant(request, tenant_id):
    """Allows System Admin absolute power to edit tenant details."""
    tenant = get_object_or_404(Tenant, id=tenant_id)
    
    # We pass the tenant's current manager to the form so the unit dropdown populates correctly
    manager = tenant.unit.manager if tenant.unit else None
    
    if request.method == 'POST':
        form = TenantUpdateForm(request.POST, manager=manager, tenant=tenant)
        if form.is_valid():
            with transaction.atomic():
                user = tenant.user
                user.first_name = form.cleaned_data['first_name']
                user.last_name = form.cleaned_data['last_name']
                user.save()
                
                tenant.phone_number = form.cleaned_data['phone_number']
                tenant.unit = form.cleaned_data['unit']
                
                if tenant.unit:
                    tenant.is_active = True
                    user.is_active = True
                    user.save()
                    
                tenant.save()
                
            messages.success(request, f'✓ Tenant {user.get_full_name()} updated by System Admin.')
            # Redirect back to the manager's detail page
            return redirect('system_admin_manager_detail', manager_id=manager.id if manager else 1)
    else:
        initial_data = {
            'first_name': tenant.user.first_name,
            'last_name': tenant.user.last_name,
            'phone_number': tenant.phone_number,
            'unit': tenant.unit,
        }
        form = TenantUpdateForm(manager=manager, tenant=tenant, initial=initial_data)
        
    return render(request, 'core/edit_tenant.html', {'form': form, 'tenant': tenant})
