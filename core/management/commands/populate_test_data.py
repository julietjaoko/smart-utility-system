from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from core.models import PropertyManager, Unit, Tenant, Meter, MeterReading
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
import random

User = get_user_model()

class Command(BaseCommand):
    help = 'Populates the database with additional test data for a new manager'

    def handle(self, *args, **kwargs):
        self.stdout.write('Starting data population for New Manager...')
        
        # 1. Create a NEW Property Manager User
        manager_username = 'manager2'
        if not User.objects.filter(username=manager_username).exists():
            manager_user = User.objects.create_user(
                username=manager_username,
                email='alex.manager@example.com',
                password='password123',
                role='PROPERTY_MANAGER',
                first_name='Alex',
                last_name='Smith'
            )
            self.stdout.write(self.style.SUCCESS(f'✓ Created user: {manager_username}'))
        else:
            manager_user = User.objects.get(username=manager_username)
            self.stdout.write(f'User {manager_username} already exists')
        
        # 2. Create Property Manager Profile
        estate_name = 'Blueberry Estates'
        manager_profile, created = PropertyManager.objects.get_or_create(
            user=manager_user,
            defaults={'estate_name': estate_name}
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'✓ Created profile for {estate_name}'))

        # 3. Create Unique Units for this Manager
        # Using a different naming convention (C and D blocks) to distinguish from manager1
        unit_numbers = ['C-10', 'C-11', 'D-05', 'D-06']
        units = []
        for unit_no in unit_numbers:
            unit, created = Unit.objects.get_or_create(
                unit_number=unit_no,
                estate_name=estate_name,
                manager=manager_profile,
                defaults={
                    'has_water_meter': True,
                    'has_electricity_meter': True # All units here have both
                }
            )
            units.append(unit)
            if created:
                self.stdout.write(self.style.SUCCESS(f'  ✓ Created unit {unit_no}'))
        
        # 4. Create New Tenant Users
        tenant_data = [
            ('tenant_alex1', 'Sarah', 'Connor', 'C-10'),
            ('tenant_alex2', 'Marcus', 'Wright', 'D-05'),
        ]
        
        for username, first_name, last_name, unit_no in tenant_data:
            if not User.objects.filter(username=username).exists():
                t_user = User.objects.create_user(
                    username=username,
                    email=f'{username}@example.com',
                    password='password123',
                    role='TENANT',
                    first_name=first_name,
                    last_name=last_name
                )
                
                target_unit = Unit.objects.get(unit_number=unit_no, manager=manager_profile)
                Tenant.objects.create(
                    user=t_user,
                    unit=target_unit,
                    move_in_date=timezone.now().date() - timedelta(days=90),
                    is_active=True
                )
                self.stdout.write(self.style.SUCCESS(f'  ✓ Created tenant {username} for {unit_no}'))

        # 5. Create Meters and 4 Months of History
        self.stdout.write('Generating historical meter readings...')
        for unit in units:
            for m_type in ['WATER', 'ELECTRICITY']:
                meter, _ = Meter.objects.get_or_create(
                    unit=unit,
                    meter_type=m_type,
                    defaults={
                        'meter_number': f'{m_type[:1]}M-BLUE-{random.randint(100, 999)}',
                        'is_active': True
                    }
                )
                
                # Generate historical readings
                current_val = Decimal(random.randint(50, 200))
                for month in range(4, 0, -1):
                    reading_date = timezone.now() - timedelta(days=30 * month)
                    consumption = Decimal(random.uniform(10, 45))
                    current_val += consumption
                    
                    MeterReading.objects.create(
                        meter=meter,
                        reading_value=current_val,
                        reading_date=reading_date,
                        recorded_by=manager_user,
                        verification_status='VERIFIED'
                    )

        self.stdout.write(self.style.SUCCESS('\n=== NEW MANAGER DATA POPULATED ==='))
        self.stdout.write(f'Manager: {manager_username} / password123')
        self.stdout.write(f'Estate: {estate_name}')