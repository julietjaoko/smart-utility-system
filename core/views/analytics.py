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
def consumption_analytics(request):
    """
    Display consumption analytics with charts and statistics.
    Shows trends, comparisons, and insights for property managers.
    """
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        selected_unit = request.GET.get('unit')
        if selected_unit:
            units = Unit.objects.filter(id=selected_unit, manager=manager)
        else:
            units = Unit.objects.filter(manager=manager)
        
        meter_type = request.GET.get('meter_type', 'WATER')
        
        # Six months keeps the chart focused on recent usage patterns.
        six_months_ago = timezone.now() - timedelta(days=180)
        readings = MeterReading.objects.filter(
            meter__unit__in=units,
            meter__meter_type=meter_type,
            reading_date__gte=six_months_ago
        ).exclude(
            verification_status='REJECTED'
        ).order_by('reading_date')
        
        # Readings are grouped by month because the chart compares billing-period trends.
        monthly_data = {}
        for reading in readings:
            month_key = reading.reading_date.strftime('%Y-%m')
            if month_key not in monthly_data:
                monthly_data[month_key] = 0
            monthly_data[month_key] += float(reading.consumption)
        
        sorted_months = sorted(monthly_data.keys())
        chart_labels = [timezone.datetime.strptime(m, '%Y-%m').strftime('%b %Y') for m in sorted_months]
        chart_data = [monthly_data[m] for m in sorted_months]
        
        total_consumption = sum(chart_data)
        avg_monthly = total_consumption / len(chart_data) if chart_data else 0
        
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
        
        anomaly_count = readings.filter(is_anomaly=True).count()
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

@manager_required
def advanced_analytics(request):
    """
    Advanced analytics dashboard with comprehensive metrics.
    """
    # Security check
    
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

@manager_required
def all_unit_performance(request):

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

@manager_required
def unit_performance(request, unit_id):
    """
    Detailed performance analytics for a specific unit.
    """
    # Security check
    
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
