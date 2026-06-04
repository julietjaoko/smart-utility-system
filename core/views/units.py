import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import manager_required
from ..forms import UnitForm
from ..models import (
    Invoice,
    Meter,
    MeterReading,
    PropertyManager,
    Tenant,
    Unit,
)

logger = logging.getLogger(__name__)
User = get_user_model()

@manager_required
def manage_units(request):
    manager = PropertyManager.objects.get(user=request.user)
    units = Unit.objects.filter(manager=manager)
    return render(request, 'core/manage_units.html', {'units': units})

@manager_required
def add_unit(request):
    manager = PropertyManager.objects.get(user=request.user)
    
    if request.method == 'POST':
        form = UnitForm(request.POST)
        
        if form.is_valid():
            # The manager is assigned server-side so ownership cannot be changed from the form.
            unit = form.save(commit=False)
            unit.manager = manager
            unit.save()
            
            messages.success(request, f'✓ Unit {unit.unit_number} added successfully')
            return redirect('manage_units')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = UnitForm()
    
    return render(request, 'core/add_unit.html', {'form': form})

@manager_required
def edit_unit(request, unit_id):
    """Handles updating unit details."""
    
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

@manager_required
def unit_detail(request, unit_id):
    """
    Detailed profile view for a specific unit.
    Acts as a central hub for unit-specific actions and summaries.
    """
    
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
