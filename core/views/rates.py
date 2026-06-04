import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect, render

from ..decorators import manager_required
from ..models import FixedCharge, PropertyManager, RateConfig

logger = logging.getLogger(__name__)
User = get_user_model()

@manager_required
def manage_rates(request):
    """
    Manage utility rates and fixed charges.
    Property managers can set rates for water and electricity.
    """
    # Security check
    
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

@manager_required
def add_rate(request):
    """
    Add or update a utility rate.
    Deactivates previous rate when new one is added.
    """
    # Security check
    
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

@manager_required
def add_fixed_charge(request):
    """
    Add a new fixed monthly charge.
    """
    # Security check
    
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

@manager_required
def delete_fixed_charge(request, charge_id):
    """
    Deactivate a fixed charge.
    """
    # Security check
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        charge = FixedCharge.objects.get(id=charge_id, manager=manager)
        
        charge.is_active = False
        charge.save()
        
        messages.success(request, f'✓ Charge "{charge.charge_name}" deactivated')
    
    except FixedCharge.DoesNotExist:
        messages.error(request, 'Charge not found')
    
    return redirect('manage_rates')
