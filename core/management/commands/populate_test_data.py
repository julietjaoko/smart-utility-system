"""
Management command to populate the database with test data.
Usage: python manage.py populate_test_data
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from core.models import PropertyManager, Unit, Tenant, Meter, MeterReading
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
import random

User = get_user_model()

class Command(BaseCommand):
    help = 'Populates the database with test data for development'

    def handle(self, *args, **kwargs):
        self.stdout.write('Starting data population...')
        
        # Create Property Manager User
        if not User.objects.filter(username='manager1').exists():
            manager_user = User.objects.create_user(
                username='manager1',
                email='manager@example.com',
                password='password123',
                role='PROPERTY_MANAGER',
                first_name='John',
                last_name='Manager'
            )
            self.stdout.write(self.style.SUCCESS('✓ Created manager user'))
        else:
            manager_user = User.objects.get(username='manager1')
            self.stdout.write('Manager user already exists')
        
        # Create Property Manager Profile
        if not PropertyManager.objects.filter(user=manager_user).exists():
            manager = PropertyManager.objects.create(
                user=manager_user,
                estate_name='Greenview Apartments'
            )
            self.stdout.write(self.style.SUCCESS('✓ Created property manager profile'))
        else:
            manager = PropertyManager.objects.get(user=manager_user)
            self.stdout.write('Property manager profile already exists')
        
        # Create Units
        unit_numbers = ['A101', 'A102', 'A103', 'B201', 'B202']
        units = []
        for unit_number in unit_numbers:
            unit, created = Unit.objects.get_or_create(
                unit_number=unit_number,
                estate_name='Greenview Apartments',
                manager=manager,
                defaults={
                    'has_water_meter': True,
                    'has_electricity_meter': random.choice([True, False])
                }
            )
            units.append(unit)
            if created:
                self.stdout.write(self.style.SUCCESS(f'✓ Created unit {unit_number}'))
        
        # Create Tenant Users and Profiles
        tenant_data = [
            ('tenant1', 'Jane', 'Doe', 'A101'),
            ('tenant2', 'Bob', 'Smith', 'A102'),
            ('tenant3', 'Alice', 'Johnson', 'B201'),
        ]
        
        for username, first_name, last_name, unit_number in tenant_data:
            if not User.objects.filter(username=username).exists():
                tenant_user = User.objects.create_user(
                    username=username,
                    email=f'{username}@example.com',
                    password='password123',
                    role='TENANT',
                    first_name=first_name,
                    last_name=last_name
                )
                
                unit = Unit.objects.get(unit_number=unit_number, manager=manager)
                Tenant.objects.create(
                    user=tenant_user,
                    unit=unit,
                    move_in_date=timezone.now().date() - timedelta(days=random.randint(30, 365))
                )
                self.stdout.write(self.style.SUCCESS(f'✓ Created tenant {username} for unit {unit_number}'))
        
        # Create Meters for each unit
        for unit in units:
            # Water meter (all units)
            if unit.has_water_meter:
                water_meter, created = Meter.objects.get_or_create(
                    unit=unit,
                    meter_type='WATER',
                    defaults={
                        'meter_number': f'WM{random.randint(10000, 99999)}',
                        'is_active': True
                    }
                )
                if created:
                    self.stdout.write(f'  ✓ Created water meter for {unit.unit_number}')
            
            # Electricity meter (some units)
            if unit.has_electricity_meter:
                elec_meter, created = Meter.objects.get_or_create(
                    unit=unit,
                    meter_type='ELECTRICITY',
                    defaults={
                        'meter_number': f'EM{random.randint(10000, 99999)}',
                        'is_active': True
                    }
                )
                if created:
                    self.stdout.write(f'  ✓ Created electricity meter for {unit.unit_number}')
        
        # Create Historical Meter Readings (last 6 months)
        self.stdout.write('Creating historical readings...')
        meters = Meter.objects.all()
        
        for meter in meters:
            # Generate 6 months of readings
            base_reading = Decimal(random.randint(1000, 5000))
            
            for month in range(6, 0, -1):
                reading_date = timezone.now() - timedelta(days=30 * month)
                
                # Add some random consumption (20-150 units per month)
                consumption = Decimal(random.uniform(20, 150))
                current_reading = base_reading + consumption
                
                # Occasionally create anomalies (very high or zero consumption)
                if random.random() < 0.1:  # 10% chance of anomaly
                    if random.choice([True, False]):
                        consumption = Decimal(0)  # Zero consumption anomaly
                    else:
                        consumption = Decimal(random.uniform(300, 500))  # Very high consumption
                    current_reading = base_reading + consumption
                
                MeterReading.objects.create(
                    meter=meter,
                    reading_value=current_reading,
                    reading_date=reading_date,
                    recorded_by=manager_user,
                    notes=f'Monthly reading for {meter.meter_type}'
                )
                
                base_reading = current_reading
        
        self.stdout.write(self.style.SUCCESS('✓ Created historical readings for all meters'))
        
        # Summary
        self.stdout.write(self.style.SUCCESS('\n=== DATA POPULATION COMPLETE ==='))
        self.stdout.write(f'Users: {User.objects.count()}')
        self.stdout.write(f'Units: {Unit.objects.count()}')
        self.stdout.write(f'Meters: {Meter.objects.count()}')
        self.stdout.write(f'Readings: {MeterReading.objects.count()}')
        self.stdout.write('\nLogin credentials:')
        self.stdout.write('Manager: manager1 / password123')
        self.stdout.write('Tenant: tenant1 / password123')