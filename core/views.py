from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate, get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from .decorators import manager_required, tenant_required, system_admin_required
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.urls import reverse
from .models import Unit, Tenant, PropertyManager, Meter, MeterReading, Invoice, Payment, AccountBalance, RateConfig, FixedCharge, ElectricityToken, TenantPreferences, MaintenanceRequest, MaintenanceMessage
from django.http import JsonResponse, FileResponse, HttpResponse
from django.core.paginator import Paginator
from django.db.models import Sum, Avg, Count, Max, Q, F
from django.utils import timezone
from datetime import timedelta, datetime
import json
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from .sms_utils import InvoiceSMS, PaymentSMS, TokenSMS
from .email_utils import InvoiceNotification, PaymentNotification
from .forms import TenantUpdateForm, UnitForm, MeterReadingForm, PaymentForm, TenantCreationForm, MaintenanceRequestForm, MaintenanceMessageForm, PropertyManagerCreationForm, PropertyManagerUpdateForm
from django.utils.dateparse import parse_date
from .pdf_generator import InvoicePDF, PaymentReceiptPDF
import os
from .excel_exporter import InvoiceExporter, PaymentExporter, ConsumptionExporter
from django.db.models.functions import TruncMonth, TruncYear
from calendar import month_name, monthrange
from .mpesa import process_mpesa_callback
from django.contrib.auth.forms import PasswordChangeForm

import logging
logger = logging.getLogger(__name__)

def refresh_invoice_statuses(invoices):
    for invoice in invoices.filter(status__in=['UNPAID', 'PARTIALLY_PAID', 'OVERDUE']):
        invoice.update_status()


def recalculate_tenant_ledger(tenant):
    """
    Rebuild a tenant's running balance from invoices and payments.

    Later invoices store the balance that was carried forward when they were
    generated. If an older invoice is paid after a newer one exists, that
    carried-forward amount must shrink so tenants are not asked to pay it twice.
    """
    if not tenant:
        return Decimal('0.00')

    with transaction.atomic():
        invoices = list(
            Invoice.objects.select_for_update()
            .filter(tenant=tenant)
            .order_by('invoice_date', 'id')
        )
        payment_totals = {
            row['invoice_id']: row['total'] or Decimal('0.00')
            for row in Payment.objects.filter(invoice__in=invoices)
            .values('invoice_id')
            .annotate(total=Sum('amount_paid'))
        }
        total_paid_to_tenant = sum(payment_totals.values(), Decimal('0.00'))

        balance = Decimal('0.00')
        cumulative_charges = Decimal('0.00')
        today = timezone.now().date()

        for invoice in invoices:
            total_paid = payment_totals.get(invoice.id, Decimal('0.00'))
            recalculated_total = invoice.subtotal + balance
            cumulative_charges += invoice.subtotal
            invoice_remaining_after_all_payments = cumulative_charges - total_paid_to_tenant

            if invoice_remaining_after_all_payments <= 0:
                recalculated_status = 'PAID'
            elif invoice_remaining_after_all_payments < invoice.subtotal:
                recalculated_status = 'PARTIALLY_PAID'
            elif invoice.due_date < today:
                recalculated_status = 'OVERDUE'
            else:
                recalculated_status = 'UNPAID'

            changed_fields = []
            if invoice.previous_balance != balance:
                invoice.previous_balance = balance
                changed_fields.append('previous_balance')
            if invoice.total_due != recalculated_total:
                invoice.total_due = recalculated_total
                changed_fields.append('total_due')
            if invoice.status != recalculated_status:
                invoice.status = recalculated_status
                changed_fields.append('status')

            if changed_fields:
                changed_fields.append('updated_at')
                invoice.save(update_fields=changed_fields)

            balance = recalculated_total - total_paid

        account_balance, _ = AccountBalance.objects.select_for_update().get_or_create(
            tenant=tenant,
            defaults={'current_balance': Decimal('0.00')}
        )
        if account_balance.current_balance != balance:
            account_balance.current_balance = balance
            account_balance.save(update_fields=['current_balance', 'last_updated'])

    return balance


def tenant_can_log_tokens(tenant):
    if not tenant.unit:
        return False
    return not Meter.objects.filter(
        unit=tenant.unit,
        meter_type='ELECTRICITY',
        is_active=True
    ).exists()


def recalculate_meter_readings(meter):
    for reading in MeterReading.objects.filter(meter=meter).order_by('reading_date', 'id'):
        reading.save()


def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_superuser or request.user.role == "SYSTEM_ADMIN":
            return redirect("system_admin_dashboard")

        if request.user.role == "PROPERTY_MANAGER":
            if PropertyManager.objects.filter(user=request.user).exists():
                return redirect("manager_dashboard")

            logout(request)
            messages.error(request, "Property Manager profile not found. Please contact admin.")
            return redirect("login")

        if request.user.role == "TENANT":
            if Tenant.objects.filter(user=request.user).exists():
                return redirect("tenant_dashboard")

            logout(request)
            messages.error(request, "Tenant profile not found. Please contact admin.")
            return redirect("login")

        return redirect("/admin/")

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)

            if user.is_superuser or user.role == "SYSTEM_ADMIN":
                return redirect("system_admin_dashboard")

            if user.role == "PROPERTY_MANAGER" and not PropertyManager.objects.filter(user=user).exists():
                messages.error(request, "Property Manager profile not found. Please contact admin.")
                return render(request, "core/login.html")

            if user.role == "TENANT" and not Tenant.objects.filter(user=user).exists():
                messages.error(request, "Tenant profile not found. Please contact admin.")
                return render(request, "core/login.html")

            if user.role == "PROPERTY_MANAGER":
                return redirect("manager_dashboard")
            elif user.role == "TENANT":
                return redirect("tenant_dashboard")
            else:
                return redirect("/admin/")

        messages.error(request, "Invalid username or password")

    return render(request, "core/login.html")



@login_required
def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def manager_dashboard(request):
    """
    Property Manager dashboard with statistics and quick actions.
    Shows key metrics like total units, readings, and active tenants.
    """
    # Ensure user is a property manager
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied: You do not have Property Manager privileges.')
        return redirect('login')
    
    try:
        # Get property manager object
        manager = PropertyManager.objects.get(user=request.user)
        
        # Calculate statistics
        total_units = Unit.objects.filter(manager=manager).count()
        active_tenants = Tenant.objects.filter(
            unit__manager=manager,
            is_active=True
        ).count()
        
        # Get meter readings from current month
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
    # Security: Ensure user is a tenant
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


@manager_required
def manage_units(request):
    manager = PropertyManager.objects.get(user=request.user)
    units = Unit.objects.filter(manager=manager)
    return render(request, 'core/manage_units.html', {'units': units})


@manager_required
def add_unit(request):
    manager = PropertyManager.objects.get(user=request.user)
    
    if request.method == 'POST':
        # Pass the submitted data to the form
        form = UnitForm(request.POST)
        
        # Django automatically validates all fields!
        if form.is_valid():
            # commit=False creates the object but pauses before saving to the DB
            unit = form.save(commit=False)
            
            # Attach the manager automatically (so the user can't spoof it)
            unit.manager = manager
            unit.save()
            
            messages.success(request, f'✓ Unit {unit.unit_number} added successfully')
            return redirect('manage_units')
        else:
            # If validation fails, Django automatically generates error messages
            messages.error(request, 'Please correct the errors below.')
    else:
        # If it's a GET request, just show an empty form
        form = UnitForm()
    
    return render(request, 'core/add_unit.html', {'form': form})


@login_required
def manage_tenants(request):
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    manager = PropertyManager.objects.get(user=request.user)
    
    # NEW QUERY: Get tenants currently in a unit OR tenants with past invoices in your units
    tenants = Tenant.objects.filter(
        Q(unit__manager=manager) | Q(invoice__unit__manager=manager)
    ).distinct()
    
    return render(request, 'core/manage_tenants.html', {'tenants': tenants})

@manager_required
def enter_meter_reading(request):
    """
    Form for property managers to enter new meter readings.
    """
    manager = PropertyManager.objects.get(user=request.user)
    
    if request.method == 'POST':
        # Pass data, files, and the manager to the form
        form = MeterReadingForm(request.POST, request.FILES, manager=manager)
        
        if form.is_valid():
            # Create reading but pause before saving to DB
            reading = form.save(commit=False)
            reading.recorded_by = request.user
            
            # The model's save() method automatically calculates consumption & anomalies!
            reading.save()
            
            # Show success/warning toast based on anomaly detection
            if reading.is_anomaly:
                messages.warning(
                    request,
                    f'⚠️ Reading saved but flagged as anomaly! '
                    f'Consumption: {reading.consumption} {reading.meter.get_meter_type_display()} units.'
                )
            else:
                messages.success(
                    request,
                    f'✓ Reading saved successfully! '
                    f'Consumption: {reading.consumption} {reading.meter.get_meter_type_display()} units.'
                )
            return redirect('meter_reading_list')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        # GET request - load empty form with today's date
        form = MeterReadingForm(manager=manager)
        form.initial['reading_date'] = timezone.now().date()
    
    return render(request, 'core/enter_meter_reading.html', {'form': form})


@manager_required
def edit_meter_reading(request, reading_id):
    manager = PropertyManager.objects.get(user=request.user)
    reading = get_object_or_404(MeterReading, id=reading_id, meter__unit__manager=manager)
    original_meter = reading.meter

    if request.method == 'POST':
        form = MeterReadingForm(request.POST, request.FILES, instance=reading, manager=manager)
        if form.is_valid():
            updated_reading = form.save(commit=False)
            updated_reading.recorded_by = request.user
            updated_reading.save()
            if updated_reading.is_anomaly and updated_reading.verification_status != 'PENDING':
                updated_reading.verification_status = 'PENDING'
                updated_reading.save()
            elif not updated_reading.is_anomaly and updated_reading.verification_status != 'VERIFIED':
                updated_reading.verification_status = 'VERIFIED'
                updated_reading.save()
            recalculate_meter_readings(original_meter)
            if updated_reading.meter_id != original_meter.id:
                recalculate_meter_readings(updated_reading.meter)

            if updated_reading.is_anomaly:
                messages.warning(request, 'Reading updated and flagged for review.')
            else:
                messages.success(request, 'Reading updated successfully.')
            return redirect('meter_reading_detail', reading_id=updated_reading.id)
        messages.error(request, 'Please correct the errors below.')
    else:
        form = MeterReadingForm(instance=reading, manager=manager)
        form.initial['reading_date'] = reading.reading_date.date()

    return render(request, 'core/enter_meter_reading.html', {
        'form': form,
        'reading': reading,
        'is_edit': True,
    })


@login_required
def meter_reading_list(request):
    """
    Display list of all meter readings with filtering and pagination.
    Property managers can see all readings for their units.
    Shows anomalies highlighted for easy identification.
    """
    # Security: Only property managers can view reading list
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # Get all readings for this manager's units
        readings = MeterReading.objects.filter(
            meter__unit__manager=manager
        ).select_related(
            'meter__unit',
            'meter',
            'recorded_by'
        ).order_by('-reading_date')
        
        # Filter by unit if specified
        unit_filter = request.GET.get('unit')
        if unit_filter:
            readings = readings.filter(meter__unit__id=unit_filter)
        
        # Filter by meter type if specified
        meter_type_filter = request.GET.get('meter_type')
        if meter_type_filter:
            readings = readings.filter(meter__meter_type=meter_type_filter)
        
        # Filter by anomalies only if specified
        show_anomalies = request.GET.get('anomalies')
        if show_anomalies == 'true':
            readings = readings.filter(is_anomaly=True)
        
        # Search by unit number
        search_query = request.GET.get('search')
        if search_query:
            readings = readings.filter(
                Q(meter__unit__unit_number__icontains=search_query) |
                Q(notes__icontains=search_query)
            )

        start_date = request.GET.get('start_date', '').strip()
        end_date = request.GET.get('end_date', '').strip()
        if start_date:
            readings = readings.filter(reading_date__date__gte=start_date)
        if end_date:
            readings = readings.filter(reading_date__date__lte=end_date)
        
        # Pagination: 15 readings per page
        paginator = Paginator(readings, 15)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        
        # Get units for filter dropdown
        units = Unit.objects.filter(manager=manager)
        
        # Count anomalies
        anomaly_count = MeterReading.objects.filter(
            meter__unit__manager=manager,
            is_anomaly=True
        ).count()
        
        # Fetch pending anomalies for the Triage Tab
        pending_anomalies = MeterReading.objects.filter(
            meter__unit__manager=manager,
            verification_status='PENDING'
        ).select_related('meter__unit').order_by('-reading_date')

        context = {
            'page_obj': page_obj,
            'units': units,
            'anomaly_count': anomaly_count,
            'pending_anomalies': pending_anomalies,  
            'pending_count': pending_anomalies.count(),
            'current_filters': {
                'unit': unit_filter,
                'meter_type': meter_type_filter,
                'anomalies': show_anomalies,
                'search': search_query,
                'start_date': start_date,
                'end_date': end_date,
            }
        }
        
        return render(request, 'core/meter_reading_list.html', context)
    
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')


@login_required
def meter_reading_detail(request, reading_id):
    """
    Display detailed view of a single meter reading.
    Shows full information including photo, consumption, and history.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # Get the reading
        reading = get_object_or_404(
            MeterReading,
            id=reading_id,
            meter__unit__manager=manager
        )
        
        # Get previous 5 readings for this meter
        previous_readings = MeterReading.objects.filter(
            meter=reading.meter,
            reading_date__lt=reading.reading_date
        ).order_by('-reading_date')[:5]
        
        # Calculate average consumption
        if previous_readings.count() > 0:
            avg_consumption = sum(r.consumption for r in previous_readings) / previous_readings.count()
        else:
            avg_consumption = 0
        
        context = {
            'reading': reading,
            'previous_readings': previous_readings,
            'avg_consumption': avg_consumption,
        }
        
        return render(request, 'core/meter_reading_detail.html', context)
    
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')
    

@login_required
def consumption_analytics(request):
    """
    Display consumption analytics with charts and statistics.
    Shows trends, comparisons, and insights for property managers.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # Get selected unit or show all units
        selected_unit = request.GET.get('unit')
        if selected_unit:
            units = Unit.objects.filter(id=selected_unit, manager=manager)
        else:
            units = Unit.objects.filter(manager=manager)
        
        # Get meter type filter
        meter_type = request.GET.get('meter_type', 'WATER')
        
        # Get last 6 months of readings
        six_months_ago = timezone.now() - timedelta(days=180)
        readings = MeterReading.objects.filter(
            meter__unit__in=units,
            meter__meter_type=meter_type,
            reading_date__gte=six_months_ago
        ).exclude(
            verification_status='REJECTED'  # Add this line!
        ).order_by('reading_date')
        
        # Prepare data for line chart (monthly consumption)
        monthly_data = {}
        for reading in readings:
            month_key = reading.reading_date.strftime('%Y-%m')
            if month_key not in monthly_data:
                monthly_data[month_key] = 0
            monthly_data[month_key] += float(reading.consumption)
        
        # Sort by date
        sorted_months = sorted(monthly_data.keys())
        chart_labels = [timezone.datetime.strptime(m, '%Y-%m').strftime('%b %Y') for m in sorted_months]
        chart_data = [monthly_data[m] for m in sorted_months]
        
        # Calculate statistics
        total_consumption = sum(chart_data)
        avg_monthly = total_consumption / len(chart_data) if chart_data else 0
        
        # Highest and lowest consumption months
        if chart_data:
            max_consumption = max(chart_data)
            min_consumption = min(chart_data)
            max_month = chart_labels[chart_data.index(max_consumption)]
            min_month = chart_labels[chart_data.index(min_consumption)]
        else:
            max_consumption = 0
            min_consumption = 0
            max_month = 'N/A'
            min_month = 'N/A'
        
        # Count anomalies
        anomaly_count = readings.filter(is_anomaly=True).count()
        
        # Get all units for dropdown
        all_units = Unit.objects.filter(manager=manager)
        
        context = {
            'chart_labels': json.dumps(chart_labels),
            'chart_data': json.dumps(chart_data),
            'total_consumption': round(total_consumption, 2),
            'avg_monthly': round(avg_monthly, 2),
            'max_consumption': round(max_consumption, 2),
            'min_consumption': round(min_consumption, 2),
            'max_month': max_month,
            'min_month': min_month,
            'anomaly_count': anomaly_count,
            'all_units': all_units,
            'selected_unit': selected_unit,
            'meter_type': meter_type,
        }
        
        return render(request, 'core/consumption_analytics.html', context)
    
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')
    

@login_required
def manage_rates(request):
    """
    Manage utility rates and fixed charges.
    Property managers can set rates for water and electricity.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    try:
        manager = PropertyManager.objects.get(user=request.user)

        if request.method == 'POST':
            water_threshold = request.POST.get('water_anomaly_threshold')
            electricity_threshold = request.POST.get('electricity_anomaly_threshold')
            try:
                water_threshold = Decimal(water_threshold)
                electricity_threshold = Decimal(electricity_threshold)
                if water_threshold <= 0 or electricity_threshold <= 0:
                    raise ValueError

                manager.water_anomaly_threshold = water_threshold
                manager.electricity_anomaly_threshold = electricity_threshold
                manager.save(update_fields=['water_anomaly_threshold', 'electricity_anomaly_threshold'])
                messages.success(request, 'Anomaly thresholds updated successfully.')
                return redirect('manage_rates')
            except (TypeError, ValueError):
                messages.error(request, 'Thresholds must be positive numbers.')
        
        # Get active rates
        water_rate = RateConfig.objects.filter(
            manager=manager,
            utility_type='WATER',
            is_active=True
        ).first()
        
        electricity_rate = RateConfig.objects.filter(
            manager=manager,
            utility_type='ELECTRICITY',
            is_active=True
        ).first()
        
        # Get all fixed charges
        fixed_charges = FixedCharge.objects.filter(
            manager=manager,
            is_active=True
        )
        
        context = {
            'water_rate': water_rate,
            'electricity_rate': electricity_rate,
            'fixed_charges': fixed_charges,
            'manager': manager,
        }
        
        return render(request, 'core/manage_rates.html', context)
    
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')


@login_required
def add_rate(request):
    """
    Add or update a utility rate.
    Deactivates previous rate when new one is added.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    if request.method == 'POST':
        try:
            manager = PropertyManager.objects.get(user=request.user)
            
            utility_type = request.POST.get('utility_type')
            rate_per_unit = request.POST.get('rate_per_unit')
            effective_from = request.POST.get('effective_from')
            
            # Deactivate previous rates of this type
            RateConfig.objects.filter(
                manager=manager,
                utility_type=utility_type
            ).update(is_active=False)
            
            # Create new rate
            rate = RateConfig.objects.create(
                manager=manager,
                utility_type=utility_type,
                rate_per_unit=rate_per_unit,
                effective_from=effective_from,
                is_active=True
            )
            
            messages.success(
                request,
                f'✓ {rate.get_utility_type_display()} rate set to KES {rate.rate_per_unit} per unit'
            )
            return redirect('manage_rates')
        
        except Exception as e:
            messages.error(request, f'Error saving rate: {str(e)}')
    
    return render(request, 'core/add_rate.html')


@login_required
def add_fixed_charge(request):
    """
    Add a new fixed monthly charge.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    if request.method == 'POST':
        try:
            manager = PropertyManager.objects.get(user=request.user)
            
            charge_name = request.POST.get('charge_name')
            amount = request.POST.get('amount')
            effective_from = request.POST.get('effective_from')
            
            # Create fixed charge
            charge = FixedCharge.objects.create(
                manager=manager,
                charge_name=charge_name,
                amount=amount,
                effective_from=effective_from,
                is_active=True
            )
            
            messages.success(
                request,
                f'✓ Fixed charge "{charge.charge_name}" added: KES {charge.amount}/month'
            )
            return redirect('manage_rates')
        
        except Exception as e:
            messages.error(request, f'Error saving charge: {str(e)}')
    
    return render(request, 'core/add_fixed_charge.html')


@login_required
def delete_fixed_charge(request, charge_id):
    """
    Deactivate a fixed charge.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        charge = FixedCharge.objects.get(id=charge_id, manager=manager)
        
        charge.is_active = False
        charge.save()
        
        messages.success(request, f'✓ Charge "{charge.charge_name}" deactivated')
    
    except FixedCharge.DoesNotExist:
        messages.error(request, 'Charge not found')
    
    return redirect('manage_rates')


@login_required
def billing_wizard_start(request):
    """Step 1: Select Billing Month & Enforce Anomaly Review"""
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
        
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


@login_required
def billing_wizard_rates(request):
    """Step 2: Transparent Rate Confirmation"""
    if request.user.role != 'PROPERTY_MANAGER':
        return redirect('tenant_dashboard')
        
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


@login_required
def billing_wizard_preview(request):
    """Step 3: Preview Totals & Final Database Commit"""
    if request.user.role != 'PROPERTY_MANAGER':
        return redirect('tenant_dashboard')
        
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
        # Final Commit: Execute database saves
        invoices_created = 0
        errors = []
        
        # Sequence Number Generator
        last_invoice = Invoice.objects.filter(
            invoice_number__startswith=f"INV-{billing_date.strftime('%Y-%m')}"
        ).order_by('-invoice_number').first()
        new_seq = int(last_invoice.invoice_number.split('-')[-1]) + 1 if last_invoice else 1
        
        for data in preview_data:
            tenant = data['tenant']
            invoice = None # Keep track of the invoice for notifications

            try:
                # 1. ATOMIC BLOCK: Strictly for Database Operations
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
                    
                    # Update Account Balance
                    acc, _ = AccountBalance.objects.get_or_create(tenant=tenant)
                    acc.current_balance = invoice.total_due
                    acc.save()
                    recalculate_tenant_ledger(tenant)
                    invoice.refresh_from_db()
                    
                    new_seq += 1
                    invoices_created += 1

                # 2. NOTIFICATIONS: Placed outside the atomic block!
                # We do this here so slow network calls don't lock up the database.
                
                # Send email notification
                if tenant.user.email:
                    try:
                        preferences = TenantPreferences.objects.filter(tenant=tenant).first()
                        if not preferences or preferences.enable_email_notifications:
                            email_notifier = InvoiceNotification()
                            email_notifier.send_invoice_notification(invoice)
                    except Exception as email_error:
                        # Log it, but don't fail the whole invoice creation
                        logger.error(f"Email error for {tenant}: {str(email_error)}")

                # Send SMS notification
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
                # If the DB fails, it safely rolls back here without affecting other tenants in the loop
                errors.append(f"Error generating for {data['unit'].unit_number}: {str(e)}")
                
        del request.session['billing_month'] # Clear session
        
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


@login_required
def invoice_list(request):
    """
    Display list of all invoices with filtering.
    Property managers can see all invoices for their units.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
        
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
def record_payment(request, invoice_id):
    """
    Record a payment against an invoice using Django Forms.
    """
    manager = PropertyManager.objects.get(user=request.user)
    invoice = get_object_or_404(Invoice, id=invoice_id, unit__manager=manager)
    recalculate_tenant_ledger(invoice.tenant)
    invoice.refresh_from_db()
    
    # Calculate total already paid
    total_paid = Payment.objects.filter(invoice=invoice).aggregate(
        total=Sum('amount_paid')
    )['total'] or Decimal('0.00')
    
    remaining_balance = invoice.total_due - total_paid

    if request.method == 'POST':
        form = PaymentForm(request.POST)
        
        if form.is_valid():
            try:
                # 1. ATOMIC BLOCK: Save Payment and Update Account Balance safely
                with transaction.atomic():
                    # Create payment record
                    payment = form.save(commit=False)
                    payment.invoice = invoice
                    payment.recorded_by = request.user
                    
                    # If method is not MPESA, clear out the mpesa fields just in case
                    if payment.payment_method != 'MPESA':
                        payment.mpesa_reference = None
                        payment.mpesa_phone = None
                        
                    payment.save() # This triggers invoice.update_status() automatically
                    
                    # Update tenant account balance
                    tenant = invoice.tenant
                    account_balance, created = AccountBalance.objects.select_for_update().get_or_create(
                        tenant=tenant,
                        defaults={'current_balance': Decimal('0.00')}
                    )
                    account_balance.current_balance -= payment.amount_paid
                    account_balance.save()
                    recalculate_tenant_ledger(tenant)

                # 2. NOTIFICATIONS (Outside the atomic block)
                try:
                    email_notifier = PaymentNotification()
                    email_notifier.send_payment_confirmation(payment)
                except Exception as email_error:
                    logger.error(f"Email error: {str(email_error)}")

                try:
                    sms_notifier = PaymentSMS()
                    sms_result = sms_notifier.send_payment_confirmation(payment)
                except Exception as sms_error:
                    logger.error(f"SMS error: {str(sms_error)}")

                messages.success(
                    request,
                    f'✓ Payment of KES {payment.amount_paid} recorded successfully! '
                    f'Invoice status: {invoice.get_status_display()}'
                )
                return redirect('invoice_detail', invoice_id=invoice.id)
                
            except Exception as e:
                messages.error(request, f'Database error recording payment: {str(e)}')
        else:
            messages.error(request, 'Please correct the errors in the form below.')
            
    else:
        # GET request - load form with pre-filled defaults
        form = PaymentForm(initial={
            'payment_date': timezone.now().date(),
            'amount_paid': remaining_balance if remaining_balance > 0 else Decimal('0.00'),
            'payment_method': 'MPESA'
        })
        
    context = {
        'form': form,
        'invoice': invoice,
        'total_paid': total_paid,
        'remaining_balance': remaining_balance,
    }
    
    return render(request, 'core/record_payment.html', context)


@manager_required
def edit_payment(request, payment_id):
    manager = PropertyManager.objects.get(user=request.user)
    payment = get_object_or_404(Payment, id=payment_id, invoice__unit__manager=manager)
    invoice = payment.invoice
    recalculate_tenant_ledger(invoice.tenant)
    invoice.refresh_from_db()
    old_amount = payment.amount_paid
    total_paid = Payment.objects.filter(invoice=invoice).aggregate(
        total=Sum('amount_paid')
    )['total'] or Decimal('0.00')
    remaining_balance = invoice.total_due - total_paid

    if request.method == 'POST':
        form = PaymentForm(request.POST, instance=payment)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated_payment = form.save(commit=False)
                    if updated_payment.payment_method != 'MPESA':
                        updated_payment.mpesa_reference = None
                        updated_payment.mpesa_phone = None
                    updated_payment.save()

                    delta = updated_payment.amount_paid - old_amount
                    account_balance, _ = AccountBalance.objects.select_for_update().get_or_create(
                        tenant=invoice.tenant,
                        defaults={'current_balance': Decimal('0.00')}
                    )
                    account_balance.current_balance -= delta
                    account_balance.save()
                    recalculate_tenant_ledger(invoice.tenant)

                messages.success(request, 'Payment updated successfully.')
                return redirect('invoice_detail', invoice_id=invoice.id)
            except Exception as e:
                messages.error(request, f'Database error updating payment: {str(e)}')
        else:
            messages.error(request, 'Please correct the errors in the form below.')
    else:
        form = PaymentForm(instance=payment)

    context = {
        'form': form,
        'invoice': invoice,
        'payment': payment,
        'total_paid': total_paid,
        'remaining_balance': remaining_balance,
        'is_edit': True,
    }
    return render(request, 'core/record_payment.html', context)


@login_required
def payment_list(request):
    """
    Display list of all payments with filtering.
    Property managers can see payment history.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
        
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # 1. BASE QUERYSET (For accurate top-level dashboard stats)
        base_payments = Payment.objects.filter(invoice__unit__manager=manager)
        
        total_payments = base_payments.count()
        total_amount = base_payments.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
        
        # 2. TABLE QUERYSET (Apply all your filters to this one)
        payments = base_payments.select_related(
            'invoice__unit',
            'invoice__tenant__user',
            'recorded_by'
        ).order_by('-payment_date')
        
        # Filter by payment method (Force uppercase to match DB: 'MPESA', 'CASH', 'BANK')
        method_filter = request.GET.get('method', '').strip().upper()
        if method_filter:
            payments = payments.filter(payment_method=method_filter)
            
        # Filter by date range (add .strip() to be safe)
        start_date = request.GET.get('start_date', '').strip()
        end_date = request.GET.get('end_date', '').strip()
        if start_date:
            payments = payments.filter(payment_date__gte=start_date)
        if end_date:
            payments = payments.filter(payment_date__lte=end_date)
            
        # Search by invoice number or M-Pesa reference
        search_query = request.GET.get('search', '').strip()
        if search_query:
            payments = payments.filter(
                Q(invoice__invoice_number__icontains=search_query) |
                Q(mpesa_reference__icontains=search_query)
            )
            
        # Pagination
        from django.core.paginator import Paginator
        paginator = Paginator(payments, 20)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        
        context = {
            'page_obj': page_obj,
            'total_payments': total_payments,
            'total_amount': total_amount,
            'current_filters': {
                'method': request.GET.get('method', ''), # Keep original casing for HTML dropdown
                'start_date': start_date,
                'end_date': end_date,
                'search': search_query,
            }
        }
        
        return render(request, 'core/payment_list.html', context)
        
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')
    

@login_required
def initiate_mpesa_payment(request, invoice_id):
    """
    Initiates an M-Pesa STK push.
    Securely handles requests from both Property Managers and Tenants.
    """
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'error': 'Invalid request method. Must be POST.'
        })

    phone_number = request.POST.get('phone_number')
    if not phone_number:
        return JsonResponse({
            'success': False,
            'error': 'Phone number is required.'
        })

    invoice = None
    if request.user.role == 'PROPERTY_MANAGER':
        manager = PropertyManager.objects.get(user=request.user)
        invoice = get_object_or_404(Invoice, id=invoice_id, unit__manager=manager)
    elif request.user.role == 'TENANT':
        tenant = Tenant.objects.get(user=request.user)
        invoice = get_object_or_404(Invoice, id=invoice_id, tenant=tenant)
    else:
        return JsonResponse({
            'success': False,
            'error': 'Unauthorized role.'
        })

    recalculate_tenant_ledger(invoice.tenant)
    invoice.refresh_from_db()

    if invoice.status == 'PAID':
        return JsonResponse({
            'success': False,
            'error': 'This invoice is already fully paid.'
        })

    total_paid = Payment.objects.filter(invoice=invoice).aggregate(
        total=Sum('amount_paid')
    )['total'] or Decimal('0.00')

    amount_due = invoice.total_due - total_paid

    if amount_due <= 0:
        return JsonResponse({
            'success': False,
            'error': 'No pending balance for this invoice.'
        })

    try:
        from .mpesa import MpesaDarajaSandbox

        mpesa_client = MpesaDarajaSandbox()

        base_callback_url = settings.MPESA_CALLBACK_URL.rstrip('/')
        dynamic_callback_url = f"{base_callback_url}{reverse('mpesa_webhook', args=[invoice.id])}"

        response = mpesa_client.initiate_stk_push(
            phone_number=phone_number,
            amount=int(amount_due),
            account_reference=invoice.invoice_number,
            transaction_desc=f"Payment for {invoice.invoice_number}",
            callback_url=dynamic_callback_url
        )

        if response.get('success'):
            return JsonResponse({
                'success': True,
                'message': response.get(
                    'response_description',
                    'STK Push sent successfully. Check your phone.'
                ),
                'checkout_request_id': response.get('checkout_request_id'),
                'merchant_request_id': response.get('merchant_request_id'),
            })

        return JsonResponse({
            'success': False,
            'error': response.get('error', 'M-Pesa API error'),
            'response_code': response.get('response_code')
        })

    except Exception as e:
        logger.exception(f"M-Pesa push failed for invoice {invoice.id}: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'M-Pesa request failed: {str(e)}'
        })


@csrf_exempt
def mpesa_callback(request):
    """
    M-Pesa callback endpoint.
    Receives payment confirmation from Safaricom.
    This endpoint is called by M-Pesa servers after payment.
    """
    if request.method == 'POST':
        try:
            # Get callback data
            callback_data = json.loads(request.body)
            
            # Process the callback
            from .mpesa import process_mpesa_callback
            result = process_mpesa_callback(callback_data)
            
            if result.get('success'):
                # Payment successful - auto-record payment
                # Extract account reference (invoice number)
                account_reference = callback_data.get('Body', {}).get('stkCallback', {}).get('AccountReference')
                
                if account_reference:
                    try:
                        # Find the invoice
                        invoice = Invoice.objects.get(invoice_number=account_reference)
                        
                        # Create payment record
                        Payment.objects.create(
                            invoice=invoice,
                            payment_date=timezone.now().date(),
                            amount_paid=result.get('amount'),
                            payment_method='MPESA',
                            mpesa_reference=result.get('mpesa_receipt'),
                            mpesa_phone=result.get('phone_number'),
                            notes='Auto-recorded from M-Pesa callback',
                            recorded_by=None  # System-generated
                        )
                        
                        # Note: Invoice status updates automatically via Payment.save()
                        
                    except Invoice.DoesNotExist:
                        logger.error(
                            f"CRITICAL: Unallocated M-Pesa Payment. "
                            f"Ref: {result.get('mpesa_receipt')}, "
                            f"Amount: KES {result.get('amount')}, "
                            f"Phone: {result.get('phone_number')}, "
                            f"Attempted Invoice: {account_reference}"
                        )  # Invoice not found, log this in production
            
            # Always return success to M-Pesa
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Success'})
        
        except Exception as e:
            # Log error but still return success to M-Pesa
            print(f"M-Pesa callback error: {str(e)}")
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Success'})
    
    return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Invalid request'}, status=400)


@login_required
def tenant_invoices(request):
    """
    Display tenant's invoice history.
    Tenants can view all their invoices.
    """
    # Security check
    if request.user.role != 'TENANT':
        messages.error(request, 'Access denied')
        return redirect('manager_dashboard')
    
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


@login_required
def tenant_consumption_history(request):
    """
    Display tenant's consumption history.
    Shows meter readings for their unit.
    """
    # Security check
    if request.user.role != 'TENANT':
        messages.error(request, 'Access denied')
        return redirect('manager_dashboard')
    
    try:
        tenant = Tenant.objects.get(user=request.user)
        unit = tenant.unit
        
        if not unit:
            messages.warning(request, 'No unit assigned to your account')
            return redirect('tenant_dashboard')
        
        # Get meter type filter
        meter_type = request.GET.get('meter_type', 'WATER')
        
        # Get readings for this unit's meters
        readings = MeterReading.objects.filter(
            meter__unit=unit,
            meter__meter_type=meter_type
        ).exclude(
            verification_status='REJECTED'
        ).order_by('-reading_date')[:12]  # Last 12 readings
        
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
            'chart_labels': json.dumps(chart_labels),
            'chart_data': json.dumps(chart_data),
        }
        
        return render(request, 'core/tenant_consumption_history.html', context)
    
    except Tenant.DoesNotExist:
        messages.error(request, 'Tenant profile not found')
        return redirect('login')
    

@login_required
def get_unit_meters(request, unit_id):
    """
    AJAX endpoint to get meters for a specific unit.
    Returns JSON with meter details including previous reading info.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        unit = get_object_or_404(Unit, id=unit_id, manager=manager)
        
        # Get all active meters for this unit
        meters = Meter.objects.filter(unit=unit, is_active=True)
        
        meters_data = []
        for meter in meters:
            # Get previous reading for this meter
            previous_reading = MeterReading.objects.filter(
                meter=meter
            ).exclude(
                verification_status='REJECTED'
            ).order_by('-reading_date').first()
            
            meter_info = {
                'id': meter.id,
                'meter_type': meter.get_meter_type_display(),
                'meter_number': meter.meter_number,
                'previous_reading': str(previous_reading.reading_value) if previous_reading else 'None',
                'previous_date': previous_reading.reading_date.strftime('%b %d, %Y') if previous_reading else 'N/A',
                'previous_consumption': str(previous_reading.consumption) if previous_reading else 'N/A'
            }
            meters_data.append(meter_info)
        
        return JsonResponse({'meters': meters_data})
    
    except PropertyManager.DoesNotExist:
        return JsonResponse({'error': 'Property Manager profile not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    

@login_required
def download_invoice_pdf(request, invoice_id):
    """
    Generate and download invoice as PDF.
    """
    # Get invoice based on user role
    if request.user.role == 'PROPERTY_MANAGER':
        manager = PropertyManager.objects.get(user=request.user)
        invoice = get_object_or_404(Invoice, id=invoice_id, unit__manager=manager)
    else:  # Tenant
        tenant = Tenant.objects.get(user=request.user)
        invoice = get_object_or_404(Invoice, id=invoice_id, tenant=tenant)
    
    # Generate PDF
    pdf_generator = InvoicePDF(invoice)
    
    # Create temp directory if it doesn't exist
    temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp')
    os.makedirs(temp_dir, exist_ok=True)
    
    # Generate filename
    filename = f'invoice_{invoice.invoice_number}.pdf'
    filepath = os.path.join(temp_dir, filename)
    
    # Generate PDF
    pdf_generator.generate(filepath)
    
    # Return PDF as download
    response = FileResponse(open(filepath, 'rb'), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response


@login_required
def download_payment_receipt(request, payment_id):
    """
    Generate and download payment receipt as PDF.
    """
    # Get payment based on user role
    if request.user.role == 'PROPERTY_MANAGER':
        manager = PropertyManager.objects.get(user=request.user)
        payment = get_object_or_404(Payment, id=payment_id, invoice__unit__manager=manager)
    else:  # Tenant
        tenant = Tenant.objects.get(user=request.user)
        payment = get_object_or_404(Payment, id=payment_id, invoice__tenant=tenant)
    
    # Generate PDF
    pdf_generator = PaymentReceiptPDF(payment)
    
    # Create temp directory
    temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp')
    os.makedirs(temp_dir, exist_ok=True)
    
    # Generate filename
    filename = f'receipt_{payment.invoice.invoice_number}_{payment.id}.pdf'
    filepath = os.path.join(temp_dir, filename)
    
    # Generate PDF
    pdf_generator.generate(filepath)
    
    # Return PDF
    response = FileResponse(open(filepath, 'rb'), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response


@login_required
def export_invoices_excel(request):
    """
    Export invoices to Excel.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # Get invoices with same filters as invoice list
        invoices = Invoice.objects.filter(
            unit__manager=manager
        ).select_related('unit', 'tenant__user').order_by('-invoice_date')
        
        # Apply filters if any
        status_filter = request.GET.get('status')
        if status_filter:
            invoices = invoices.filter(status=status_filter)
        
        unit_filter = request.GET.get('unit')
        if unit_filter:
            invoices = invoices.filter(unit__id=unit_filter)
        
        # Generate Excel
        exporter = InvoiceExporter()
        
        # Create temp directory
        temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename = f'invoices_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        filepath = os.path.join(temp_dir, filename)
        
        exporter.generate(invoices, filepath)
        
        # Return Excel file
        with open(filepath, 'rb') as f:
            response = HttpResponse(
                f.read(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')


@login_required
def export_payments_excel(request):
    """
    Export payments to Excel.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # Get payments with filters
        payments = Payment.objects.filter(
            invoice__unit__manager=manager
        ).select_related('invoice__unit', 'invoice__tenant__user', 'recorded_by').order_by('-payment_date')
        
        # Apply filters
        method_filter = request.GET.get('method')
        if method_filter:
            payments = payments.filter(payment_method=method_filter)
        
        start_date = request.GET.get('start_date')
        if start_date:
            payments = payments.filter(payment_date__gte=start_date)
        
        end_date = request.GET.get('end_date')
        if end_date:
            payments = payments.filter(payment_date__lte=end_date)
        
        # Generate Excel
        exporter = PaymentExporter()
        
        temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename = f'payments_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        filepath = os.path.join(temp_dir, filename)
        
        exporter.generate(payments, filepath)
        
        # Return Excel file
        with open(filepath, 'rb') as f:
            response = HttpResponse(
                f.read(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')


@login_required
def export_consumption_excel(request):
    """
    Export consumption data to Excel.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # Get readings with filters
        readings = MeterReading.objects.filter(
            meter__unit__manager=manager
        ).select_related('meter__unit', 'recorded_by').order_by('-reading_date')
        
        # Apply filters
        unit_filter = request.GET.get('unit')
        if unit_filter:
            readings = readings.filter(meter__unit__id=unit_filter)
        
        meter_type_filter = request.GET.get('meter_type')
        if meter_type_filter:
            readings = readings.filter(meter__meter_type=meter_type_filter)
        
        anomalies_only = request.GET.get('anomalies')
        if anomalies_only == 'true':
            readings = readings.filter(is_anomaly=True)

        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        if start_date:
            readings = readings.filter(reading_date__date__gte=start_date)
        if end_date:
            readings = readings.filter(reading_date__date__lte=end_date)
        
        # Generate Excel
        exporter = ConsumptionExporter()
        
        temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename = f'consumption_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        filepath = os.path.join(temp_dir, filename)
        
        exporter.generate(readings, filepath)
        
        # Return Excel file
        with open(filepath, 'rb') as f:
            response = HttpResponse(
                f.read(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')
    

@login_required
def advanced_analytics(request):
    """
    Advanced analytics dashboard with comprehensive metrics.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # Date filters
        year = request.GET.get('year', datetime.now().year)
        year = int(year)
        
        # Get available years
        available_years = MeterReading.objects.filter(
            meter__unit__manager=manager
        ).dates('reading_date', 'year', order='DESC')
        
        # Revenue Analytics
        invoices_this_year = Invoice.objects.filter(
            unit__manager=manager,
            invoice_date__year=year
        )
        refresh_invoice_statuses(invoices_this_year)
        
        total_billed = invoices_this_year.aggregate(
            total=Sum('total_due')
        )['total'] or Decimal('0.00')
        
        payments_this_year = Payment.objects.filter(
            invoice__unit__manager=manager,
            payment_date__year=year
        )
        
        total_collected = payments_this_year.aggregate(
            total=Sum('amount_paid')
        )['total'] or Decimal('0.00')
        
        collection_rate = (float(total_collected) / float(total_billed) * 100) if total_billed > 0 else 0
        
        # Outstanding amount
        outstanding = total_billed - total_collected
        
        # Monthly revenue trend
        monthly_revenue = invoices_this_year.annotate(
            month=TruncMonth('invoice_date')
        ).values('month').annotate(
            billed=Sum('total_due'),
            paid=Sum('payments__amount_paid')
        ).order_by('month')
        
        revenue_labels = []
        billed_data = []
        collected_data = []
        
        for item in monthly_revenue:
            revenue_labels.append(item['month'].strftime('%B'))
            billed_data.append(float(item['billed'] or 0))
            collected_data.append(float(item['paid'] or 0))
        
        # Consumption Analytics
        consumption_this_year = MeterReading.objects.filter(
            meter__unit__manager=manager,
            reading_date__year=year
        )
        
        # Water vs Electricity breakdown
        water_consumption = consumption_this_year.filter(
            meter__meter_type='WATER'
        ).aggregate(total=Sum('consumption'))['total'] or 0
        
        electricity_consumption = consumption_this_year.filter(
            meter__meter_type='ELECTRICITY'
        ).aggregate(total=Sum('consumption'))['total'] or 0
        
        # Monthly consumption trend
        monthly_consumption = consumption_this_year.annotate(
            month=TruncMonth('reading_date')
        ).values('month', 'meter__meter_type').annotate(
            total=Sum('consumption')
        ).order_by('month')
        
        consumption_labels = []
        water_monthly = []
        electricity_monthly = []
        
        # Organize by month
        months_dict = {}
        for item in monthly_consumption:
            month_name = item['month'].strftime('%B')
            if month_name not in months_dict:
                months_dict[month_name] = {'water': 0, 'electricity': 0}
            
            if item['meter__meter_type'] == 'WATER':
                months_dict[month_name]['water'] = float(item['total'])
            else:
                months_dict[month_name]['electricity'] = float(item['total'])
        
        for month, values in months_dict.items():
            consumption_labels.append(month)
            water_monthly.append(values['water'])
            electricity_monthly.append(values['electricity'])
        
        # Invoice Status Distribution
        status_distribution = invoices_this_year.values('status').annotate(
            count=Count('id')
        )
        
        status_labels = []
        status_counts = []
        
        for item in status_distribution:
            status_labels.append(Invoice._meta.get_field('status').choices[
                [choice[0] for choice in Invoice._meta.get_field('status').choices].index(item['status'])
            ][1])
            status_counts.append(item['count'])
        
        # Top consuming units
        top_units = consumption_this_year.values(
            'meter__unit__unit_number',
            'meter__unit__id'
        ).annotate(
            total_consumption=Sum('consumption')
        ).order_by('-total_consumption')[:5]
        
        # Anomaly statistics
        total_readings = consumption_this_year.count()
        anomaly_readings = consumption_this_year.filter(is_anomaly=True).count()
        anomaly_rate = (anomaly_readings / total_readings * 100) if total_readings > 0 else 0
        
        # Anomaly breakdown (Changed from 'anomaly_type' to 'verification_status')
        anomaly_breakdown = consumption_this_year.filter(
            is_anomaly=True
        ).values('verification_status').annotate(count=Count('id'))
        
        # Year-over-year comparison
        previous_year = year - 1
        previous_year_invoices = Invoice.objects.filter(
            unit__manager=manager,
            invoice_date__year=previous_year
        )
        
        previous_year_billed = previous_year_invoices.aggregate(
            total=Sum('total_due')
        )['total'] or Decimal('0.00')
        
        yoy_growth = 0
        if previous_year_billed > 0:
            yoy_growth = ((float(total_billed) - float(previous_year_billed)) / float(previous_year_billed) * 100)
        
        # Average invoice value
        avg_invoice = invoices_this_year.aggregate(
            avg=Avg('total_due')
        )['avg'] or Decimal('0.00')
        
        # Payment method distribution
        payment_methods = payments_this_year.values('payment_method').annotate(
            count=Count('id'),
            amount=Sum('amount_paid')
        )
        
        context = {
            'year': year,
            'available_years': available_years,
            
            # Revenue metrics
            'total_billed': total_billed,
            'total_collected': total_collected,
            'outstanding': outstanding,
            'collection_rate': round(collection_rate, 1),
            'avg_invoice': avg_invoice,
            'yoy_growth': round(yoy_growth, 1),
            
            # Revenue charts
            'revenue_labels': json.dumps(revenue_labels),
            'billed_data': json.dumps(billed_data),
            'collected_data': json.dumps(collected_data),
            
            # Consumption metrics
            'water_consumption': water_consumption,
            'electricity_consumption': electricity_consumption,
            'total_consumption': water_consumption + electricity_consumption,
            
            # Consumption charts
            'consumption_labels': json.dumps(consumption_labels),
            'water_monthly': json.dumps(water_monthly),
            'electricity_monthly': json.dumps(electricity_monthly),
            
            # Invoice status
            'status_labels': json.dumps(status_labels),
            'status_counts': json.dumps(status_counts),
            
            # Top units
            'top_units': top_units,
            
            # Anomalies
            'total_readings': total_readings,
            'anomaly_readings': anomaly_readings,
            'anomaly_rate': round(anomaly_rate, 1),
            'anomaly_breakdown': anomaly_breakdown,
            
            # Payment methods
            'payment_methods': payment_methods,
        }
        
        return render(request, 'core/advanced_analytics.html', context)
    
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')


@login_required
def all_unit_performance(request):
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')

    manager = PropertyManager.objects.get(user=request.user)
    year = int(request.GET.get('year', datetime.now().year))
    utility_type = request.GET.get('utility_type', '').strip().upper()
    search_query = request.GET.get('search', '').strip()

    readings = MeterReading.objects.filter(
        meter__unit__manager=manager,
        reading_date__year=year
    ).exclude(verification_status='REJECTED')

    if utility_type in ['WATER', 'ELECTRICITY']:
        readings = readings.filter(meter__meter_type=utility_type)

    unit_totals = readings.values(
        'meter__unit__id',
        'meter__unit__unit_number',
        'meter__unit__estate_name'
    ).annotate(
        total_consumption=Sum('consumption'),
        reading_count=Count('id'),
        anomaly_count=Count('id', filter=Q(is_anomaly=True))
    ).order_by('-total_consumption')

    if search_query:
        unit_totals = unit_totals.filter(
            Q(meter__unit__unit_number__icontains=search_query) |
            Q(meter__unit__estate_name__icontains=search_query)
        )

    paginator = Paginator(unit_totals, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    available_years = MeterReading.objects.filter(
        meter__unit__manager=manager
    ).dates('reading_date', 'year', order='DESC')

    return render(request, 'core/all_unit_performance.html', {
        'page_obj': page_obj,
        'year': year,
        'available_years': available_years,
        'current_filters': {
            'utility_type': utility_type,
            'search': search_query,
        }
    })


@login_required
def unit_performance(request, unit_id):
    """
    Detailed performance analytics for a specific unit.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        unit = get_object_or_404(Unit, id=unit_id, manager=manager)
        
        # Get date range
        months = int(request.GET.get('months', 12))
        start_date = datetime.now().date() - timedelta(days=months*30)
        
        # Consumption history
        readings = MeterReading.objects.filter(
            meter__unit=unit,
            reading_date__gte=start_date
        ).select_related('meter').order_by('reading_date')
        
        # Organize by meter type
        water_readings = readings.filter(meter__meter_type='WATER')
        electricity_readings = readings.filter(meter__meter_type='ELECTRICITY')
        
        # Chart data
        water_labels = []
        water_values = []
        for reading in water_readings:
            water_labels.append(reading.reading_date.strftime('%b %Y'))
            water_values.append(float(reading.consumption))
        
        electricity_labels = []
        electricity_values = []
        for reading in electricity_readings:
            electricity_labels.append(reading.reading_date.strftime('%b %Y'))
            electricity_values.append(float(reading.consumption))
        
        # Invoice history
        invoices = Invoice.objects.filter(
            unit=unit,
            invoice_date__gte=start_date
        ).order_by('-invoice_date')
        
        # Payment history
        payments = Payment.objects.filter(
            invoice__unit=unit,
            payment_date__gte=start_date
        ).order_by('-payment_date')
        
        # Statistics
        total_billed = invoices.aggregate(total=Sum('total_due'))['total'] or Decimal('0.00')
        total_paid = payments.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
        
        # Average consumption
        avg_water = water_readings.aggregate(avg=Avg('consumption'))['avg'] or 0
        avg_electricity = electricity_readings.aggregate(avg=Avg('consumption'))['avg'] or 0
        
        # Current tenant
        tenant = Tenant.objects.filter(unit=unit).first()
        
        context = {
            'unit': unit,
            'tenant': tenant,
            'months': months,
            
            # Consumption
            'water_labels': json.dumps(water_labels),
            'water_values': json.dumps(water_values),
            'electricity_labels': json.dumps(electricity_labels),
            'electricity_values': json.dumps(electricity_values),
            'avg_water': avg_water,
            'avg_electricity': avg_electricity,
            
            # Financial
            'total_billed': total_billed,
            'total_paid': total_paid,
            'invoices': invoices[:5],  # Last 5
            'payments': payments[:5],  # Last 5
        }
        
        return render(request, 'core/unit_performance.html', context)
    
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')
    

@login_required
def resolve_anomaly(request, reading_id, action):
    """
    Handles the triage of anomalous meter readings.
    Action can be 'verify' or 'reject'.
    """
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')

    if request.method != 'POST':
        messages.error(request, 'Invalid request method.')
        return redirect('meter_reading_list')
        
    try:
        manager = PropertyManager.objects.get(user=request.user)
        reading = get_object_or_404(
            MeterReading, 
            id=reading_id, 
            meter__unit__manager=manager,
            verification_status='PENDING'
        )
        
        if action == 'verify':
            reading.verification_status = 'VERIFIED'
            reading.save()
            messages.success(
                request, 
                f'✓ Reading for Unit {reading.meter.unit.unit_number} verified and cleared for billing.'
            )
        elif action == 'reject':
            reading.verification_status = 'REJECTED'
            reading.save()
            messages.warning(
                request, 
                f'Reading for Unit {reading.meter.unit.unit_number} rejected. Please enter a corrected reading.'
            )
            return redirect('enter_meter_reading')
            
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found.')
        
    return redirect('meter_reading_list')

@login_required
def unit_detail(request, unit_id):
    """
    Detailed profile view for a specific unit.
    Acts as a central hub for unit-specific actions and summaries.
    """
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    manager = PropertyManager.objects.get(user=request.user)
    unit = get_object_or_404(Unit, id=unit_id, manager=manager)
    
    # Get current tenant if assigned
    tenant = Tenant.objects.filter(unit=unit).first()
    
    # Get recent meter readings (last 5)
    recent_readings = MeterReading.objects.filter(
        meter__unit=unit
    ).select_related('meter').order_by('-reading_date')[:5]
    
    # Get recent invoices (last 5)
    recent_invoices = Invoice.objects.filter(
        unit=unit
    ).order_by('-invoice_date')[:5]
    
    # Calculate total outstanding balance for this unit
    total_outstanding = Invoice.objects.filter(
        unit=unit,
        status__in=['UNPAID', 'PARTIALLY_PAID', 'OVERDUE']
    ).aggregate(total=Sum('total_due'))['total'] or Decimal('0.00')

    context = {
        'unit': unit,
        'tenant': tenant,
        'recent_readings': recent_readings,
        'recent_invoices': recent_invoices,
        'total_outstanding': total_outstanding,
    }
    
    return render(request, 'core/unit_detail.html', context)


@login_required
def edit_unit(request, unit_id):
    """Handles updating unit details."""
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
    manager = PropertyManager.objects.get(user=request.user)
    unit = get_object_or_404(Unit, id=unit_id, manager=manager)
    
    if request.method == 'POST':
        unit.unit_number = request.POST.get('unit_number')
        unit.estate_name = request.POST.get('estate_name')
        unit.has_water_meter = request.POST.get('has_water_meter') == 'on'
        unit.has_electricity_meter = request.POST.get('has_electricity_meter') == 'on'
        
        try:
            unit.save()
            messages.success(request, f'Unit {unit.unit_number} updated successfully.')
            return redirect('unit_detail', unit_id=unit.id)
        except Exception as e:
            messages.error(request, f'Error updating unit: {str(e)}')
            
    return render(request, 'core/edit_unit.html', {'unit': unit})


@login_required
def deactivate_tenant(request, tenant_id):
    """
    Deactivates a tenant, revokes login access, and vacates the unit.
    Includes a strict financial check to ensure balances are settled first.
    """
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
        
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
    
@login_required
def bulk_delete_invoices(request):
    """
    Bulk delete selected invoices.
    Only allows deletion of unpaid invoices and properly adjusts the tenant's account balance.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
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


@login_required
def bulk_send_invoices(request):
    """
    Send email notifications for selected invoices.
    """
    # Security check
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
    
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


@login_required
def tenant_preferences(request):
    """
    Manage tenant preferences and feature toggles.
    """
    # Security check
    if request.user.role != 'TENANT':
        messages.error(request, 'Access denied')
        return redirect('manager_dashboard')
    
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


@login_required
def electricity_tokens(request):
    """
    View and manage electricity tokens.
    """
    # Security check
    if request.user.role != 'TENANT':
        messages.error(request, 'Access denied')
        return redirect('manager_dashboard')
    
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


@login_required
def add_electricity_token(request):
    """
    Log a new electricity token.
    """
    # Security check
    if request.user.role != 'TENANT':
        messages.error(request, 'Access denied')
        return redirect('manager_dashboard')
    
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


@login_required
def delete_electricity_token(request, token_id):
    """
    Delete an electricity token.
    """
    # Security check
    if request.user.role != 'TENANT':
        messages.error(request, 'Access denied')
        return redirect('manager_dashboard')
    
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


@csrf_exempt
def mpesa_webhook(request, invoice_id):
    """
    Receives the payment confirmation from Safaricom.
    Because we append the invoice_id to the URL, we know exactly who paid!
    """
    if request.method == 'POST':
        try:
            # Parse Safaricom's JSON payload
            callback_data = json.loads(request.body)
            print("\n=== SAFARICOM WEBHOOK RECEIVED ===")
            print(json.dumps(callback_data, indent=2))
            print("==================================\n")
            
            result = process_mpesa_callback(callback_data)
            
            if result.get('success'):
                amount_paid = Decimal(str(result['amount']))
                mpesa_receipt = result['mpesa_receipt']
                phone_used = result['phone_number']
                
                # Fetch the invoice
                invoice = Invoice.objects.get(id=invoice_id)
                tenant = invoice.tenant
                
                # Prevent duplicate processing of the same receipt
                if Payment.objects.filter(mpesa_reference=mpesa_receipt).exists():
                    return HttpResponse('Already Processed', status=200)

                with transaction.atomic():
                    # 1. Create the Payment Record
                    payment = Payment.objects.create(
                        invoice=invoice,
                        amount_paid=amount_paid,
                        payment_date=timezone.now().date(),
                        payment_method='MPESA',
                        mpesa_reference=mpesa_receipt,
                        mpesa_phone=phone_used,
                        notes=f"Automated STK Push Payment. Receipt: {mpesa_receipt}"
                    )
                    
                    # 2. Update the Account Balance safely
                    account_balance, _ = AccountBalance.objects.select_for_update().get_or_create(
                        tenant=tenant,
                        defaults={'current_balance': Decimal('0.00')}
                    )
                    account_balance.current_balance -= amount_paid
                    account_balance.save()
                    recalculate_tenant_ledger(tenant)

                # 3. Send automated receipt (Outside atomic block)
                try:
                    email_notifier = PaymentNotification() # Assuming from email_utils
                    email_notifier.send_payment_confirmation(payment)
                except Exception as e:
                    logger.error(f"Failed to send email receipt for M-Pesa payment {mpesa_receipt}: {e}")

            # Always return a 200 OK so Safaricom knows we received the message
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})
            
        except Exception as e:
            logger.error(f"M-Pesa Webhook Error: {str(e)}")
            return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Failed'}, status=500)

    # Reject GET requests
    return HttpResponse('Method Not Allowed', status=405)

User = get_user_model()

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
                    
                    # 1. Create the base User account
                    # We use the email as their username for easy login
                    user = User.objects.create_user(
                        username=email,
                        email=email,
                        password=password,
                        first_name=form.cleaned_data['first_name'],
                        last_name=form.cleaned_data['last_name'],
                        role='TENANT'
                    )
                    
                    # 2. Create the linked Tenant profile
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
        # GET request: Check if we clicked from a specific unit page
        initial_data = {}
        if 'unit_id' in request.GET:
            initial_data['unit'] = request.GET.get('unit_id')
            
        # Pass the initial data to pre-fill the dropdown!
        form = TenantCreationForm(manager=manager, initial=initial_data)
        
    return render(request, 'core/add_tenant.html', {'form': form})


@login_required
def change_password(request):
    """
    Allows both Tenants and Property Managers to change their password securely.
    """
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            # Important: Keep the user logged in after password change
            update_session_auth_hash(request, user)
            
            messages.success(request, '✓ Your password was successfully updated!')
            
            # Redirect back to their respective dashboard
            if request.user.role == 'PROPERTY_MANAGER':
                return redirect('manager_dashboard')
            else:
                return redirect('tenant_dashboard')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = PasswordChangeForm(request.user)
        
    return render(request, 'core/change_password.html', {'form': form})


@login_required
def edit_tenant(request, tenant_id):
    """Handles updating existing tenant details and transferring units."""
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
        
    manager = PropertyManager.objects.get(user=request.user)
    
    # Safely fetch the tenant using the Q object to ensure they belong to this manager's property
    tenant = get_object_or_404(
        Tenant.objects.distinct(),
        Q(id=tenant_id) & (Q(unit__manager=manager) | Q(invoice__unit__manager=manager))
    )
        
    if request.method == 'POST':
        form = TenantUpdateForm(request.POST, manager=manager, tenant=tenant)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    # 1. Update the base User model
                    user = tenant.user
                    user.first_name = form.cleaned_data['first_name']
                    user.last_name = form.cleaned_data['last_name']
                    user.save()
                    
                    # 2. Update the Tenant profile
                    tenant.phone_number = form.cleaned_data['phone_number']
                    tenant.unit = form.cleaned_data['unit']
                    
                    # If they are assigned a unit, ensure they are marked as active
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


@login_required
def generate_final_invoice(request, tenant_id):
    """
    Generates a prorated final invoice for a tenant moving out mid-month.
    """
    if request.user.role != 'PROPERTY_MANAGER':
        messages.error(request, 'Access denied')
        return redirect('tenant_dashboard')
        
    manager = PropertyManager.objects.get(user=request.user)
    tenant = get_object_or_404(Tenant, id=tenant_id, unit__manager=manager, is_active=True)
    unit = tenant.unit

    if request.method == 'POST':
        today = timezone.now().date()
        billing_period = f"Final - {today.strftime('%B %Y')}"
        
        # Prevent double generation
        if Invoice.objects.filter(tenant=tenant, billing_period=billing_period).exists():
            messages.error(request, 'A final invoice has already been generated for this tenant this month.')
            return redirect('unit_detail', unit_id=unit.id)

        # 1. Get active rates and charges
        water_rate_config = RateConfig.objects.filter(manager=manager, utility_type='WATER', is_active=True).first()
        elec_rate_config = RateConfig.objects.filter(manager=manager, utility_type='ELECTRICITY', is_active=True).first()
        fixed_charges = FixedCharge.objects.filter(manager=manager, is_active=True)

        # 2. Prorate fixed charges based on the day of the month
        _, days_in_month = monthrange(today.year, today.month)
        proration_factor = Decimal(today.day) / Decimal(days_in_month)
        
        total_fixed_charges = Decimal('0.00')
        fixed_charges_breakdown = {}
        for charge in fixed_charges:
            prorated_amount = round(charge.amount * proration_factor, 2)
            total_fixed_charges += prorated_amount
            fixed_charges_breakdown[f"{charge.charge_name} (Prorated)"] = str(prorated_amount)

        # 3. Get Final Consumption
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

        # 4. Get Previous Balance
        prev_balance = recalculate_tenant_ledger(tenant)

        try:
            with transaction.atomic():
                # Generate unique Final Invoice Number
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
                    due_date=today + timedelta(days=3), # Short due date for move-outs
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

                # Update Account Balance
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

    # Note: For a quick implementation, you don't even need a separate HTML page.
    # We can just redirect back to the unit detail if it's not a POST request.
    return redirect('unit_detail', unit_id=unit.id)

@manager_required
def delete_payment(request, payment_id):
    """
    Safely deletes a mistakenly entered payment and reverses the 
    tenant's account balance.
    """
    manager = PropertyManager.objects.get(user=request.user)
    # Ensure this payment belongs to a unit managed by this manager
    payment = get_object_or_404(Payment, id=payment_id, invoice__unit__manager=manager)
    invoice = payment.invoice
    tenant = invoice.tenant
    
    if request.method == 'POST':
        try:
            with transaction.atomic():
                # 1. Reverse the account balance (add the money back to their debt)
                account_balance, _ = AccountBalance.objects.select_for_update().get_or_create(
                    tenant=tenant,
                    defaults={'current_balance': Decimal('0.00')}
                )
                account_balance.current_balance += payment.amount_paid
                account_balance.save()
                
                # 2. Delete the payment
                amount_deleted = payment.amount_paid
                payment.delete()
                
                # 3. Re-calculate the invoice status (e.g., changing it from PAID back to UNPAID)
                recalculate_tenant_ledger(tenant)
                
            messages.success(request, f'✓ Payment of KES {amount_deleted} safely reversed. Account balance updated.')
        except Exception as e:
            messages.error(request, f'Error reversing payment: {str(e)}')
            
        return redirect('invoice_detail', invoice_id=invoice.id)
        
    return redirect('payment_list')

@login_required
def tenant_maintenance_requests(request):
    if request.user.role != "TENANT":
        messages.error(request, "Access denied")
        return redirect("manager_dashboard")

    tenant = get_object_or_404(Tenant.objects.select_related("unit__manager"), user=request.user)
    requests_qs = MaintenanceRequest.objects.filter(tenant=tenant).select_related("unit", "manager")

    return render(request, "core/tenant_maintenance_requests.html", {"requests": requests_qs, "tenant": tenant})


@login_required
def tenant_new_maintenance_request(request):
    if request.user.role != "TENANT":
        messages.error(request, "Access denied")
        return redirect("manager_dashboard")

    tenant = get_object_or_404(Tenant.objects.select_related("unit__manager"), user=request.user)

    if not tenant.unit:
        messages.error(request, "You need an assigned unit before logging a maintenance request.")
        return redirect("tenant_dashboard")

    if request.method == "POST":
        form = MaintenanceRequestForm(request.POST, request.FILES)
        if form.is_valid():
            maintenance_request = form.save(commit=False)
            maintenance_request.tenant = tenant
            maintenance_request.unit = tenant.unit
            maintenance_request.manager = tenant.unit.manager
            maintenance_request.save()

            MaintenanceMessage.objects.create(
                request=maintenance_request,
                sender=request.user,
                message=maintenance_request.description,
            )

            messages.success(request, "Maintenance request submitted successfully.")
            return redirect("tenant_maintenance_detail", request_id=maintenance_request.id)
        messages.error(request, "Please correct the errors below.")
    else:
        form = MaintenanceRequestForm()

    return render(request, "core/tenant_new_maintenance_request.html", {"form": form})


@login_required
def tenant_maintenance_detail(request, request_id):
    if request.user.role != "TENANT":
        messages.error(request, "Access denied")
        return redirect("manager_dashboard")

    tenant = get_object_or_404(Tenant, user=request.user)
    maintenance_request = get_object_or_404(
        MaintenanceRequest.objects.select_related("tenant__user", "unit", "manager"),
        id=request_id,
        tenant=tenant,
    )

    if request.method == "POST":
        form = MaintenanceMessageForm(request.POST)
        if form.is_valid():
            reply = form.save(commit=False)
            reply.request = maintenance_request
            reply.sender = request.user
            reply.save()
            maintenance_request.save()
            messages.success(request, "Reply sent.")
            return redirect("tenant_maintenance_detail", request_id=request_id)
    else:
        form = MaintenanceMessageForm()

    return render(request, "core/maintenance_detail.html", {
        "maintenance_request": maintenance_request,
        "reply_form": form,
        "is_manager": False,
    })


@login_required
def manager_maintenance_requests(request):
    if request.user.role != "PROPERTY_MANAGER":
        messages.error(request, "Access denied")
        return redirect("tenant_dashboard")

    manager = get_object_or_404(PropertyManager, user=request.user)
    requests_qs = MaintenanceRequest.objects.filter(manager=manager).select_related("tenant__user", "unit")

    status_filter = request.GET.get("status", "").strip().upper()
    if status_filter:
        requests_qs = requests_qs.filter(status=status_filter)

    return render(request, "core/manager_maintenance_requests.html", {
        "requests": requests_qs,
        "current_status": status_filter,
        "statuses": MaintenanceRequest.STATUS_CHOICES,
    })


@login_required
def manager_maintenance_detail(request, request_id):
    if request.user.role != "PROPERTY_MANAGER":
        messages.error(request, "Access denied")
        return redirect("tenant_dashboard")

    manager = get_object_or_404(PropertyManager, user=request.user)
    maintenance_request = get_object_or_404(
        MaintenanceRequest.objects.select_related("tenant__user", "unit", "manager"),
        id=request_id,
        manager=manager,
    )

    if request.method == "POST":
        if "status" in request.POST:
            new_status = request.POST.get("status")
            valid_statuses = dict(MaintenanceRequest.STATUS_CHOICES)
            if new_status in valid_statuses:
                maintenance_request.status = new_status
                maintenance_request.save()
                messages.success(request, "Status updated.")
                return redirect("manager_maintenance_detail", request_id=request_id)

        form = MaintenanceMessageForm(request.POST)
        if form.is_valid():
            reply = form.save(commit=False)
            reply.request = maintenance_request
            reply.sender = request.user
            reply.save()
            maintenance_request.save()
            messages.success(request, "Reply sent.")
            return redirect("manager_maintenance_detail", request_id=request_id)
    else:
        form = MaintenanceMessageForm()

    return render(request, "core/maintenance_detail.html", {
        "maintenance_request": maintenance_request,
        "reply_form": form,
        "is_manager": True,
        "statuses": MaintenanceRequest.STATUS_CHOICES,
    })


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
