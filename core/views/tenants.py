import logging
from calendar import monthrange
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..decorators import manager_required
from ..forms import (
    TenantCreationForm,
    TenantUpdateForm,
)
from ..models import (
    AccountBalance,
    FixedCharge,
    Invoice,
    Meter,
    MeterReading,
    PropertyManager,
    RateConfig,
    Tenant,
)
from .helpers import recalculate_tenant_ledger

logger = logging.getLogger(__name__)
User = get_user_model()

@manager_required
def manage_tenants(request):
    
    manager = PropertyManager.objects.get(user=request.user)
    
    # Past invoices keep former tenants visible for billing history and payment follow-up.
    tenants = Tenant.objects.filter(
        Q(unit__manager=manager) | Q(invoice__unit__manager=manager)
    ).distinct()
    
    return render(request, 'core/manage_tenants.html', {'tenants': tenants})

@manager_required
def add_tenant(request):
    """
    Onboard a new tenant. Creates both the User account and the Tenant profile.
    """
    manager = PropertyManager.objects.get(user=request.user)
    
    if request.method == 'POST':
        form = TenantCreationForm(request.POST, manager=manager)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    email = form.cleaned_data['email']
                    password = form.cleaned_data['password']
                    
                    # The email doubles as username so tenants have one login identifier to remember.
                    user = User.objects.create_user(
                        username=email,
                        email=email,
                        password=password,
                        first_name=form.cleaned_data['first_name'],
                        last_name=form.cleaned_data['last_name'],
                        role='TENANT'
                    )
                    
                    # The profile stores rental details separately from authentication details.
                    tenant = Tenant.objects.create(
                        user=user,
                        unit=form.cleaned_data['unit'],
                        phone_number=form.cleaned_data['phone_number'],
                        move_in_date=timezone.now().date()
                    )
                    
                messages.success(
                    request, 
                    f'✓ Tenant {user.get_full_name()} added successfully! '
                    f'They can now log in using their email and the temporary password.'
                )
                return redirect('manage_tenants')
                
            except Exception as e:
                messages.error(request, f'Database error creating tenant: {str(e)}')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        initial_data = {}
        if 'unit_id' in request.GET:
            initial_data['unit'] = request.GET.get('unit_id')
            
        # Opening the form from a unit page preselects that unit for faster onboarding.
        form = TenantCreationForm(manager=manager, initial=initial_data)
        
    return render(request, 'core/add_tenant.html', {'form': form})

@manager_required
def edit_tenant(request, tenant_id):
    """Handles updating existing tenant details and transferring units."""
        
    manager = PropertyManager.objects.get(user=request.user)
    
    # Former tenants remain editable if they still have invoices under this manager's units.
    tenant = get_object_or_404(
        Tenant.objects.distinct(),
        Q(id=tenant_id) & (Q(unit__manager=manager) | Q(invoice__unit__manager=manager))
    )
        
    if request.method == 'POST':
        form = TenantUpdateForm(request.POST, manager=manager, tenant=tenant)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = tenant.user
                    user.first_name = form.cleaned_data['first_name']
                    user.last_name = form.cleaned_data['last_name']
                    user.save()
                    
                    tenant.phone_number = form.cleaned_data['phone_number']
                    tenant.unit = form.cleaned_data['unit']
                    
                    # Reassigning a unit reactivates the tenant account for normal portal access.
                    if tenant.unit:
                        tenant.is_active = True
                        user.is_active = True
                        user.save()
                        
                    tenant.save()
                    
                messages.success(request, f'✓ Tenant {user.get_full_name()} updated successfully.')
                return redirect('manage_tenants')
                
            except Exception as e:
                messages.error(request, f'Database error: {str(e)}')
    else:
        # Pre-fill the form with their current details
        initial_data = {
            'first_name': tenant.user.first_name,
            'last_name': tenant.user.last_name,
            'phone_number': tenant.phone_number,
            'unit': tenant.unit,
        }
        form = TenantUpdateForm(manager=manager, tenant=tenant, initial=initial_data)
        
    return render(request, 'core/edit_tenant.html', {'form': form, 'tenant': tenant})

@manager_required
def deactivate_tenant(request, tenant_id):
    """
    Deactivates a tenant, revokes login access, and vacates the unit.
    Includes a strict financial check to ensure balances are settled first.
    """
        
    if request.method == 'POST':
        manager = PropertyManager.objects.get(user=request.user)
        # Ensure the tenant belongs to a unit managed by this manager
        tenant = get_object_or_404(Tenant, id=tenant_id, unit__manager=manager)
        unit = tenant.unit
        
        # ---------------------------------------------------------
        # STRICT FINANCIAL SAFETY CHECK
        # ---------------------------------------------------------
        try:
            account = AccountBalance.objects.get(tenant=tenant)
            
            if account.current_balance > 0:
                # They owe money
                messages.error(
                    request, 
                    f'Action Blocked: {tenant.user.get_full_name()} still owes KES {account.current_balance}. '
                    f'Please record their final payment before deactivating.'
                )
                return redirect('unit_detail', unit_id=unit.id)
                
            elif account.current_balance < 0:
                # They are owed a refund (credit)
                messages.error(
                    request, 
                    f'Action Blocked: {tenant.user.get_full_name()} has a credit balance of KES {abs(account.current_balance)}. '
                    f'Please refund the deposit/credit to balance the account to zero.'
                )
                return redirect('unit_detail', unit_id=unit.id)
                
        except AccountBalance.DoesNotExist:
            # If no balance record exists at all, it's safe to proceed
            pass
            
        # ---------------------------------------------------------
        # SAFE OFFBOARDING LOGIC
        # ---------------------------------------------------------
        # 1. Mark Tenant profile as inactive
        tenant.is_active = False
        
        # 2. Prevent user from logging in
        user = tenant.user
        user.is_active = False 
        user.save()
        
        # 3. Vacate the unit
        tenant.unit = None
        tenant.save()
        
        messages.success(request, f'✓ Tenant {user.get_full_name()} deactivated successfully. Unit {unit.unit_number} is now vacant.')
        return redirect('unit_detail', unit_id=unit.id)
        
    return redirect('manage_units')

@manager_required
def generate_final_invoice(request, tenant_id):
    """
    Generates a prorated final invoice for a tenant moving out mid-month.
    """
        
    manager = PropertyManager.objects.get(user=request.user)
    tenant = get_object_or_404(Tenant, id=tenant_id, unit__manager=manager, is_active=True)
    unit = tenant.unit

    if request.method == 'POST':
        today = timezone.now().date()
        billing_period = f"Final - {today.strftime('%B %Y')}"
        
        # A tenant should receive only one final invoice for the same move-out period.
        if Invoice.objects.filter(tenant=tenant, billing_period=billing_period).exists():
            messages.error(request, 'A final invoice has already been generated for this tenant this month.')
            return redirect('unit_detail', unit_id=unit.id)

        water_rate_config = RateConfig.objects.filter(manager=manager, utility_type='WATER', is_active=True).first()
        elec_rate_config = RateConfig.objects.filter(manager=manager, utility_type='ELECTRICITY', is_active=True).first()
        fixed_charges = FixedCharge.objects.filter(manager=manager, is_active=True)

        # Fixed monthly charges are prorated so moving out mid-month is billed fairly.
        _, days_in_month = monthrange(today.year, today.month)
        proration_factor = Decimal(today.day) / Decimal(days_in_month)
        
        total_fixed_charges = Decimal('0.00')
        fixed_charges_breakdown = {}
        for charge in fixed_charges:
            prorated_amount = round(charge.amount * proration_factor, 2)
            total_fixed_charges += prorated_amount
            fixed_charges_breakdown[f"{charge.charge_name} (Prorated)"] = str(prorated_amount)

        # The latest verified reading represents the final consumption to include.
        water_units = Decimal('0.00')
        electricity_units = Decimal('0.00')

        if unit.has_water_meter:
            water_meter = Meter.objects.filter(unit=unit, meter_type='WATER', is_active=True).first()
            if water_meter:
                latest_reading = MeterReading.objects.filter(
                    meter=water_meter, verification_status='VERIFIED'
                ).order_by('-reading_date').first()
                if latest_reading:
                    water_units = latest_reading.consumption

        if unit.has_electricity_meter:
            elec_meter = Meter.objects.filter(unit=unit, meter_type='ELECTRICITY', is_active=True).first()
            if elec_meter:
                latest_reading = MeterReading.objects.filter(
                    meter=elec_meter, verification_status='VERIFIED'
                ).order_by('-reading_date').first()
                if latest_reading:
                    electricity_units = latest_reading.consumption

        # The carried balance is recalculated first so the final invoice closes the ledger correctly.
        prev_balance = recalculate_tenant_ledger(tenant)

        try:
            with transaction.atomic():
                # The sequence keeps final invoice numbers readable while avoiding duplicates.
                last_invoice = Invoice.objects.filter(
                    invoice_number__startswith=f"INV-FIN-{today.strftime('%Y-%m')}"
                ).order_by('-invoice_number').first()
                new_seq = int(last_invoice.invoice_number.split('-')[-1]) + 1 if last_invoice else 1
                invoice_number = f"INV-FIN-{today.strftime('%Y-%m')}-{new_seq:03d}"

                invoice = Invoice.objects.create(
                    unit=unit,
                    tenant=tenant,
                    invoice_number=invoice_number,
                    invoice_date=today,
                    due_date=today + timedelta(days=3),  # Move-out invoices need faster settlement.
                    billing_period=billing_period,
                    water_units=water_units,
                    water_rate=water_rate_config.rate_per_unit if water_rate_config else Decimal('0.00'),
                    electricity_units=electricity_units,
                    electricity_rate=elec_rate_config.rate_per_unit if elec_rate_config else Decimal('0.00'),
                    total_fixed_charges=total_fixed_charges,
                    fixed_charges_breakdown=fixed_charges_breakdown,
                    previous_balance=prev_balance,
                    generated_by=request.user
                )
                invoice.calculate_totals()
                invoice.save()

                acc, _ = AccountBalance.objects.get_or_create(tenant=tenant)
                acc.current_balance = invoice.total_due
                acc.save()
                recalculate_tenant_ledger(tenant)
                invoice.refresh_from_db()

            messages.success(request, f'✓ Final Invoice {invoice_number} generated successfully! Please collect payment before deactivating.')
            return redirect('invoice_detail', invoice_id=invoice.id)

        except Exception as e:
            messages.error(request, f'Error generating final invoice: {str(e)}')
            return redirect('unit_detail', unit_id=unit.id)

    return redirect('unit_detail', unit_id=unit.id)
