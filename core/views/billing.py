import logging
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..decorators import manager_required
from ..email_utils import InvoiceNotification
from ..models import (
    AccountBalance,
    FixedCharge,
    Invoice,
    Meter,
    MeterReading,
    Payment,
    PropertyManager,
    RateConfig,
    Tenant,
    TenantPreferences,
    Unit,
)
from ..sms_utils import InvoiceSMS
from .helpers import recalculate_tenant_ledger


logger = logging.getLogger(__name__)
User = get_user_model()

@manager_required
def billing_wizard_start(request):
    """Step 1: Select Billing Month & Enforce Anomaly Review"""
        
    manager = PropertyManager.objects.get(user=request.user)
    
    if request.method == 'POST':
        billing_month = request.POST.get('billing_month')
        request.session['billing_month'] = billing_month
        
        # Strict Gatekeeper: Check for unverified anomalies
        pending_anomalies = MeterReading.objects.filter(
            meter__unit__manager=manager,
            is_anomaly=True,
            verification_status='PENDING'
        ).count()
        
        if pending_anomalies > 0:
            messages.error(
                request, 
                f'Action Blocked: You have {pending_anomalies} pending anomalies. '
                f'You must verify or reject them before generating invoices.'
            )
            return redirect('meter_reading_list') # Redirects to the triage list
            
        return redirect('billing_wizard_rates')
        
    current_month = timezone.now().strftime('%Y-%m')
    return render(request, 'core/wizard_step1_start.html', {'current_month': current_month})

@manager_required
def billing_wizard_rates(request):
    """Step 2: Transparent Rate Confirmation"""
        
    manager = PropertyManager.objects.get(user=request.user)
    billing_month = request.session.get('billing_month')
    
    if not billing_month:
        return redirect('billing_wizard_start')
        
    water_rate = RateConfig.objects.filter(manager=manager, utility_type='WATER', is_active=True).first()
    elec_rate = RateConfig.objects.filter(manager=manager, utility_type='ELECTRICITY', is_active=True).first()
    fixed_charges = FixedCharge.objects.filter(manager=manager, is_active=True)
    
    if request.method == 'POST':
        return redirect('billing_wizard_preview')
        
    context = {
        'billing_month': billing_month,
        'water_rate': water_rate,
        'elec_rate': elec_rate,
        'fixed_charges': fixed_charges
    }
    return render(request, 'core/wizard_step2_rates.html', context)

@manager_required
def billing_wizard_preview(request):
    """Step 3: Preview Totals & Final Database Commit"""
        
    manager = PropertyManager.objects.get(user=request.user)
    billing_month = request.session.get('billing_month')
    
    if not billing_month:
        return redirect('billing_wizard_start')
        
    billing_date = datetime.strptime(billing_month, '%Y-%m')
    due_date = billing_date + timedelta(days=10)
    units = Unit.objects.filter(manager=manager)
    
    # Active Configurations
    water_rate_config = RateConfig.objects.filter(manager=manager, utility_type='WATER', is_active=True).first()
    elec_rate_config = RateConfig.objects.filter(manager=manager, utility_type='ELECTRICITY', is_active=True).first()
    fixed_charges = FixedCharge.objects.filter(manager=manager, is_active=True)
    
    total_fixed_charges = sum(charge.amount for charge in fixed_charges)
    fixed_charges_breakdown = {charge.charge_name: str(charge.amount) for charge in fixed_charges}
    
    preview_data = []
    
    # Calculate totals dynamically for the preview
    for unit in units:
        tenant = Tenant.objects.filter(unit=unit).first()
        if not tenant:
            continue
            
        existing_invoice = Invoice.objects.filter(
            unit=unit,
            billing_period=billing_date.strftime('%B %Y')
        ).exists()
        
        if existing_invoice:
            continue
            
        water_units = Decimal('0.00')
        electricity_units = Decimal('0.00')
        
        if unit.has_water_meter:
            water_meter = Meter.objects.filter(unit=unit, meter_type='WATER', is_active=True).first()
            if water_meter:
                # Only use VERIFIED readings
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
                    
        prev_balance = recalculate_tenant_ledger(tenant)
        
        water_charge = water_units * (water_rate_config.rate_per_unit if water_rate_config else Decimal('0.00'))
        elec_charge = electricity_units * (elec_rate_config.rate_per_unit if elec_rate_config else Decimal('0.00'))
        subtotal = water_charge + elec_charge + total_fixed_charges
        total_due = subtotal + prev_balance
        
        preview_data.append({
            'unit': unit,
            'tenant': tenant,
            'water_charge': water_charge,
            'elec_charge': elec_charge,
            'fixed_charges': total_fixed_charges,
            'prev_balance': prev_balance,
            'total_due': total_due,
            'water_units': water_units,
            'electricity_units': electricity_units
        })
        
    if request.method == 'POST':
        invoices_created = 0
        errors = []
        
        # The sequence continues from the last invoice in the same billing month.
        last_invoice = Invoice.objects.filter(
            invoice_number__startswith=f"INV-{billing_date.strftime('%Y-%m')}"
        ).order_by('-invoice_number').first()
        new_seq = int(last_invoice.invoice_number.split('-')[-1]) + 1 if last_invoice else 1
        
        for data in preview_data:
            tenant = data['tenant']
            invoice = None

            try:
                # Each invoice is saved in its own transaction so one failed unit does not block the rest.
                with transaction.atomic():
                    invoice_number = f"INV-{billing_date.strftime('%Y-%m')}-{new_seq:03d}"
                    
                    invoice = Invoice.objects.create(
                        unit=data['unit'],
                        tenant=tenant,
                        invoice_number=invoice_number,
                        invoice_date=billing_date.date(),
                        due_date=due_date.date(),
                        billing_period=billing_date.strftime('%B %Y'),
                        water_units=data['water_units'],
                        water_rate=water_rate_config.rate_per_unit if water_rate_config else Decimal('0.00'),
                        electricity_units=data['electricity_units'],
                        electricity_rate=elec_rate_config.rate_per_unit if elec_rate_config else Decimal('0.00'),
                        total_fixed_charges=total_fixed_charges,
                        fixed_charges_breakdown=fixed_charges_breakdown,
                        previous_balance=data['prev_balance'],
                        generated_by=request.user
                    )
                    invoice.calculate_totals()
                    invoice.save()
                    
                    acc, _ = AccountBalance.objects.get_or_create(tenant=tenant)
                    acc.current_balance = invoice.total_due
                    acc.save()
                    recalculate_tenant_ledger(tenant)
                    invoice.refresh_from_db()
                    
                    new_seq += 1
                    invoices_created += 1

                # Notifications run after saving so slow external services do not hold database locks.
                if tenant.user.email:
                    try:
                        preferences = TenantPreferences.objects.filter(tenant=tenant).first()
                        if not preferences or preferences.enable_email_notifications:
                            email_notifier = InvoiceNotification()
                            email_notifier.send_invoice_notification(invoice)
                    except Exception as email_error:
                        # Notification failures should not undo a valid invoice.
                        logger.error(f"Email error for {tenant}: {str(email_error)}")

                if tenant.phone_number:
                    try:
                        preferences = TenantPreferences.objects.filter(tenant=tenant).first()
                        if preferences and preferences.enable_sms_notifications:
                            sms_notifier = InvoiceSMS()
                            sms_result = sms_notifier.send_invoice_notification(invoice)
                            if not sms_result['success']:
                                logger.error(f"SMS error for {tenant}: {sms_result.get('error', 'Unknown')}")
                    except Exception as sms_error:
                        logger.error(f"SMS error for {tenant}: {str(sms_error)}")

            except Exception as e:
                errors.append(f"Error generating for {data['unit'].unit_number}: {str(e)}")
                
        del request.session['billing_month']
        
        if errors:
            messages.warning(request, f'Generated {invoices_created} invoices, but encountered errors: {"; ".join(errors)}')
        else:
            messages.success(request, f'✓ Successfully generated {invoices_created} invoices and sent notifications.')
            
        return redirect('invoice_list')
        
    context = {
        'billing_month_display': billing_date.strftime('%B %Y'),
        'preview_data': preview_data,
        'total_invoices': len(preview_data)
    }
    return render(request, 'core/wizard_step3_preview.html', context)

@manager_required
def invoice_list(request):
    """
    Display list of all invoices with filtering.
    Property managers can see all invoices for their units.
    """
    # Security check
        
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # 1. BASE QUERYSET (We use this for the top stats so they don't break when filtering)
        base_invoices = Invoice.objects.filter(unit__manager=manager)
        tenant_ids = base_invoices.exclude(tenant__isnull=True).values_list('tenant_id', flat=True).distinct()
        for tenant in Tenant.objects.filter(id__in=tenant_ids):
            recalculate_tenant_ledger(tenant)
        base_invoices = Invoice.objects.filter(unit__manager=manager)
        
        # Calculate summary statistics BEFORE filtering
        total_invoices = base_invoices.count()
        unpaid_invoices = base_invoices.filter(status='UNPAID').count()
        overdue_invoices = base_invoices.filter(status='OVERDUE').count()
        open_invoices = base_invoices.filter(
            status__in=['UNPAID', 'PARTIALLY_PAID', 'OVERDUE']
        ).prefetch_related('payments')
        total_outstanding = sum((invoice.balance_due for invoice in open_invoices), Decimal('0.00'))
        
        # 2. TABLE QUERYSET (We apply the actual filters to this one)
        invoices = base_invoices.select_related('unit', 'tenant__user').order_by('-invoice_date')
        
        # Filter by status (Force uppercase so 'Paid' becomes 'PAID' to match DB)
        status_filter = request.GET.get('status', '').strip().upper()
        if status_filter:
            invoices = invoices.filter(status=status_filter)
            
        # Filter by unit (Ensure we are passing an integer ID, not text)
        unit_filter = request.GET.get('unit', '').strip()
        if unit_filter:
            try:
                invoices = invoices.filter(unit__id=int(unit_filter))
            except ValueError:
                pass # Ignore if the HTML accidentally passed a string instead of an ID
                
        # Search by invoice number or unit number
        search_query = request.GET.get('search', '').strip()
        if search_query:
            invoices = invoices.filter(
                Q(invoice_number__icontains=search_query) |
                Q(unit__unit_number__icontains=search_query)
            )
            
        # Pagination: 20 per page
        from django.core.paginator import Paginator
        paginator = Paginator(invoices, 20)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        
        # Get units for filter dropdown
        units = Unit.objects.filter(manager=manager)
        
        context = {
            'page_obj': page_obj,
            'units': units,
            'total_invoices': total_invoices,
            'unpaid_invoices': unpaid_invoices,
            'overdue_invoices': overdue_invoices,
            'total_outstanding': total_outstanding,
            'current_filters': {
                'status': request.GET.get('status', ''), # Keep original casing for the dropdown
                'unit': unit_filter,
                'search': search_query,
            }
        }
        
        return render(request, 'core/invoice_list.html', context)
        
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')

@login_required
def invoice_detail(request, invoice_id):
    """
    Display detailed view of a single invoice.
    Shows full breakdown and payment history.
    """
    # Get invoice based on user role
    if request.user.role == 'PROPERTY_MANAGER':
        manager = PropertyManager.objects.get(user=request.user)
        invoice = get_object_or_404(
            Invoice,
            id=invoice_id,
            unit__manager=manager
        )
    else:  # Tenant
        tenant = Tenant.objects.get(user=request.user)
        invoice = get_object_or_404(
            Invoice,
            id=invoice_id,
            tenant=tenant
        )
    recalculate_tenant_ledger(invoice.tenant)
    invoice.refresh_from_db()
    
    # Get payment history for this invoice
    payments = Payment.objects.filter(invoice=invoice).order_by('-payment_date')
    
    # Calculate total paid
    total_paid = payments.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
    remaining_balance = invoice.total_due - total_paid

    abs_remaining_balance = abs(remaining_balance)
    
    context = {
        'invoice': invoice,
        'payments': payments,
        'total_paid': total_paid,
        'remaining_balance': remaining_balance,
        'abs_remaining_balance': abs_remaining_balance,
    }
    
    return render(request, 'core/invoice_detail.html', context)

@manager_required
def bulk_delete_invoices(request):
    """
    Bulk delete selected invoices.
    Only allows deletion of unpaid invoices and properly adjusts the tenant's account balance.
    """
    # Security check
    
    if request.method == 'POST':
        try:
            manager = PropertyManager.objects.get(user=request.user)
            invoice_ids = request.POST.getlist('invoice_ids')
            
            if not invoice_ids:
                messages.warning(request, 'No invoices selected')
                return redirect('invoice_list')
            
            # Wrap in an atomic block so if balance updates fail, invoices aren't deleted
            with transaction.atomic():
                # Get selected invoices and prefetch the tenant to avoid N+1 queries
                invoices = Invoice.objects.filter(
                    id__in=invoice_ids,
                    unit__manager=manager,
                    status='UNPAID'  # Only allow deletion of unpaid invoices
                ).select_related('tenant')
                
                count = invoices.count()
                
                if count == 0:
                    messages.warning(request, 'No valid invoices to delete. Only unpaid invoices can be deleted.')
                    return redirect('invoice_list')
                
                # 1. Deduct the invoice amounts from the tenants' account balances BEFORE deleting
                for invoice in invoices:
                    if invoice.tenant:
                        account_balance = AccountBalance.objects.filter(tenant=invoice.tenant).first()
                        if account_balance:
                            account_balance.current_balance -= invoice.total_due
                            account_balance.save()
                
                # 2. Delete the invoices safely
                invoices.delete()
                
            messages.success(request, f'✓ Successfully deleted {count} invoice(s) and restored tenant balances.')
            return redirect('invoice_list')
        
        except PropertyManager.DoesNotExist:
            messages.error(request, 'Property Manager profile not found')
            return redirect('manager_dashboard')
        except Exception as e:
            messages.error(request, f'Database error during deletion: {str(e)}')
            return redirect('invoice_list')
    
    return redirect('invoice_list')

@manager_required
def bulk_send_invoices(request):
    """
    Send email notifications for selected invoices.
    """
    # Security check
    
    if request.method == 'POST':
        try:
            manager = PropertyManager.objects.get(user=request.user)
            invoice_ids = request.POST.getlist('invoice_ids')
            
            if not invoice_ids:
                messages.warning(request, 'No invoices selected')
                return redirect('invoice_list')
            
            # Get selected invoices
            invoices = Invoice.objects.filter(
                id__in=invoice_ids,
                unit__manager=manager
            ).select_related('tenant__user', 'unit')
            
            email_notifier = InvoiceNotification()
            sent_count = 0
            failed_count = 0
            
            for invoice in invoices:
                try:
                    success = email_notifier.send_invoice_notification(invoice)
                    if success:
                        sent_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    failed_count += 1
            
            if sent_count > 0:
                messages.success(request, f'✓ Sent {sent_count} email notification(s)')
            
            if failed_count > 0:
                messages.warning(request, f'Failed to send {failed_count} email(s)')
            
            return redirect('invoice_list')
        
        except PropertyManager.DoesNotExist:
            messages.error(request, 'Property Manager profile not found')
            return redirect('manager_dashboard')
    
    return redirect('invoice_list')

@manager_required
def send_invoice_reminder(request, invoice_id):
    """
    Sends a manual payment reminder (Email/SMS) to the tenant for a specific invoice.
    """
    manager = PropertyManager.objects.get(user=request.user)
    invoice = get_object_or_404(Invoice, id=invoice_id, unit__manager=manager)
    
    if invoice.status == 'PAID':
        messages.error(request, 'Cannot send a reminder for an invoice that is already paid.')
        return redirect('invoice_detail', invoice_id=invoice.id)
        
    emails_sent = False
    sms_sent = False
    tenant = invoice.tenant
    
    # 1. Send Email Reminder
    if tenant.user.email:
        try:
            email_notifier = InvoiceNotification()
            # Reusing your existing notification logic
            email_notifier.send_invoice_notification(invoice) 
            emails_sent = True
        except Exception as e:
            logger.error(f"Failed to send email reminder for INV-{invoice.id}: {e}")
            
    # 2. Send SMS Reminder
    if tenant.phone_number:
        try:
            sms_notifier = InvoiceSMS()
            sms_notifier.send_invoice_notification(invoice)
            sms_sent = True
        except Exception as e:
            logger.error(f"Failed to send SMS reminder for INV-{invoice.id}: {e}")
            
    if emails_sent or sms_sent:
        messages.success(request, f'✓ Payment reminder successfully sent to {tenant.user.get_full_name() or tenant.user.username}.')
    else:
        messages.warning(request, '⚠️ Could not send reminder. Tenant may not have a valid email or phone number on file.')
        
    return redirect('invoice_detail', invoice_id=invoice.id)
