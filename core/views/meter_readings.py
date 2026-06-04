import json
import logging
import os
from calendar import month_name, monthrange
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, F, Max, Q, Sum
from django.db.models.functions import TruncMonth, TruncYear
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt

from ..decorators import manager_required, system_admin_required, tenant_required
from ..email_utils import InvoiceNotification, PaymentNotification
from ..excel_exporter import ConsumptionExporter, InvoiceExporter, PaymentExporter
from ..forms import (
    MaintenanceMessageForm,
    MaintenanceRequestForm,
    MeterReadingForm,
    PaymentForm,
    PropertyManagerCreationForm,
    PropertyManagerUpdateForm,
    TenantCreationForm,
    TenantUpdateForm,
    UnitForm,
)
from ..models import (
    AccountBalance,
    ElectricityToken,
    FixedCharge,
    Invoice,
    MaintenanceMessage,
    MaintenanceRequest,
    Meter,
    MeterReading,
    Payment,
    PropertyManager,
    RateConfig,
    Tenant,
    TenantPreferences,
    Unit,
)
from ..mpesa import process_mpesa_callback
from ..pdf_generator import InvoicePDF, PaymentReceiptPDF
from ..sms_utils import InvoiceSMS, PaymentSMS, TokenSMS
from .helpers import (
    recalculate_meter_readings,
    recalculate_tenant_ledger,
    refresh_invoice_statuses,
    tenant_can_log_tokens,
)

logger = logging.getLogger(__name__)
User = get_user_model()

@manager_required
def enter_meter_reading(request):
    """
    Form for property managers to enter new meter readings.
    """
    manager = PropertyManager.objects.get(user=request.user)
    
    if request.method == 'POST':
        form = MeterReadingForm(request.POST, request.FILES, manager=manager)
        
        if form.is_valid():
            # The model save handles consumption and anomaly checks once the recorder is attached.
            reading = form.save(commit=False)
            reading.recorded_by = request.user
            reading.save()
            
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

@manager_required
def meter_reading_list(request):
    """
    Display list of all meter readings with filtering and pagination.
    Property managers can see all readings for their units.
    Shows anomalies highlighted for easy identification.
    """
    # Security: Only property managers can view reading list
    
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

@manager_required
def meter_reading_detail(request, reading_id):
    """
    Display detailed view of a single meter reading.
    Shows full information including photo, consumption, and history.
    """
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        reading = get_object_or_404(
            MeterReading,
            id=reading_id,
            meter__unit__manager=manager
        )
        
        # A short history gives context without making the detail page noisy.
        previous_readings = MeterReading.objects.filter(
            meter=reading.meter,
            reading_date__lt=reading.reading_date
        ).order_by('-reading_date')[:5]
        
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

@manager_required
def resolve_anomaly(request, reading_id, action):
    """
    Handles the triage of anomalous meter readings.
    Action can be 'verify' or 'reject'.
    """

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
