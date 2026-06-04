from decimal import Decimal
from datetime import timedelta
from io import BytesIO

from django.urls import reverse
from django.test import TestCase
from django.utils import timezone
from openpyxl import load_workbook

from .models import (
    AccountBalance,
    Invoice,
    Meter,
    MeterReading,
    Payment,
    PropertyManager,
    Tenant,
    TenantPreferences,
    Unit,
    User,
)
from .views import recalculate_tenant_ledger


class BillingLedgerTests(TestCase):
    def setUp(self):
        manager_user = User.objects.create_user(
            username='manager@example.com',
            password='password',
            role='PROPERTY_MANAGER',
        )
        self.manager = PropertyManager.objects.create(
            user=manager_user,
            estate_name='Test Estate',
        )
        self.unit = Unit.objects.create(
            unit_number='A1',
            estate_name='Test Estate',
            manager=self.manager,
        )
        tenant_user = User.objects.create_user(
            username='tenant@example.com',
            password='password',
            role='TENANT',
        )
        self.tenant = Tenant.objects.create(user=tenant_user, unit=self.unit)

    def make_invoice(self, number, subtotal, previous_balance, days_offset=0):
        today = timezone.now().date()
        return Invoice.objects.create(
            unit=self.unit,
            tenant=self.tenant,
            invoice_number=number,
            invoice_date=today + timedelta(days=days_offset),
            due_date=today + timedelta(days=days_offset + 10),
            billing_period=number,
            subtotal=Decimal(subtotal),
            previous_balance=Decimal(previous_balance),
            total_due=Decimal(subtotal) + Decimal(previous_balance),
            generated_by=self.manager.user,
        )

    def test_late_payment_reduces_newer_invoice_carried_balance(self):
        old_invoice = self.make_invoice('INV-OLD', '1000.00', '0.00')
        new_invoice = self.make_invoice('INV-NEW', '500.00', '1000.00')
        AccountBalance.objects.create(tenant=self.tenant, current_balance=Decimal('1500.00'))

        Payment.objects.create(
            invoice=old_invoice,
            payment_date=timezone.now().date(),
            amount_paid=Decimal('1000.00'),
            payment_method='CASH',
        )

        current_balance = recalculate_tenant_ledger(self.tenant)
        old_invoice.refresh_from_db()
        new_invoice.refresh_from_db()

        self.assertEqual(old_invoice.status, 'PAID')
        self.assertEqual(new_invoice.previous_balance, Decimal('0.00'))
        self.assertEqual(new_invoice.total_due, Decimal('500.00'))
        self.assertEqual(current_balance, Decimal('500.00'))
        self.assertEqual(new_invoice.balance_due, Decimal('500.00'))

    def test_latest_invoice_payment_clears_carried_previous_invoices(self):
        carried_balance = Decimal('0.00')
        old_invoices = []

        for index in range(6):
            invoice = self.make_invoice(
                f'INV-OLD-{index + 1}',
                '3000.00',
                str(carried_balance),
                days_offset=index,
            )
            old_invoices.append(invoice)
            carried_balance += Decimal('3000.00')

        latest_invoice = self.make_invoice(
            'INV-LATEST',
            '7000.00',
            str(carried_balance),
            days_offset=6,
        )
        AccountBalance.objects.create(tenant=self.tenant, current_balance=Decimal('25000.00'))

        Payment.objects.create(
            invoice=latest_invoice,
            payment_date=timezone.now().date(),
            amount_paid=Decimal('25000.00'),
            payment_method='CASH',
        )

        current_balance = recalculate_tenant_ledger(self.tenant)

        for invoice in old_invoices:
            invoice.refresh_from_db()
            self.assertEqual(invoice.status, 'PAID')

        latest_invoice.refresh_from_db()
        self.assertEqual(latest_invoice.status, 'PAID')
        self.assertEqual(latest_invoice.previous_balance, Decimal('18000.00'))
        self.assertEqual(latest_invoice.total_due, Decimal('25000.00'))
        self.assertEqual(current_balance, Decimal('0.00'))


class ConsumptionExportTests(TestCase):
    def setUp(self):
        self.manager_user = User.objects.create_user(
            username='manager-export@example.com',
            password='password',
            role='PROPERTY_MANAGER',
        )
        self.manager = PropertyManager.objects.create(
            user=self.manager_user,
            estate_name='Export Estate',
        )
        self.unit = Unit.objects.create(
            unit_number='B1',
            estate_name='Export Estate',
            manager=self.manager,
        )
        self.meter = Meter.objects.get(unit=self.unit, meter_type='WATER')

    def test_meter_readings_export_generates_workbook_with_previous_reading(self):
        first_date = timezone.now() - timedelta(days=30)
        second_date = timezone.now()
        MeterReading.objects.create(
            meter=self.meter,
            reading_value=Decimal('100.00'),
            reading_date=first_date,
            recorded_by=self.manager_user,
        )
        MeterReading.objects.create(
            meter=self.meter,
            reading_value=Decimal('145.00'),
            reading_date=second_date,
            recorded_by=self.manager_user,
        )

        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('export_consumption_excel'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        workbook = load_workbook(BytesIO(response.content))
        worksheet = workbook['Consumption']
        self.assertEqual(worksheet['B5'].value, 'B1')
        self.assertEqual(worksheet['F5'].value, 145)
        self.assertEqual(worksheet['G5'].value, 100)
        self.assertEqual(worksheet['H5'].value, 45)


class TenantConsumptionAlertTests(TestCase):
    def setUp(self):
        self.manager_user = User.objects.create_user(
            username='manager-alerts@example.com',
            password='password',
            role='PROPERTY_MANAGER',
        )
        self.manager = PropertyManager.objects.create(
            user=self.manager_user,
            estate_name='Alert Estate',
        )
        self.unit = Unit.objects.create(
            unit_number='C1',
            estate_name='Alert Estate',
            manager=self.manager,
        )
        self.tenant_user = User.objects.create_user(
            username='tenant-alerts@example.com',
            password='password',
            role='TENANT',
        )
        self.tenant = Tenant.objects.create(user=self.tenant_user, unit=self.unit)
        self.meter = Meter.objects.get(unit=self.unit, meter_type='WATER')

    def create_high_usage_reading(self):
        now = timezone.now()
        values = [
            ('10.00', now - timedelta(days=40)),
            ('20.00', now - timedelta(days=30)),
            ('30.00', now - timedelta(days=20)),
            ('50.00', now - timedelta(days=10)),
        ]
        for value, reading_date in values:
            MeterReading.objects.create(
                meter=self.meter,
                reading_value=Decimal(value),
                reading_date=reading_date,
                recorded_by=self.manager_user,
            )

    def test_tenant_dashboard_shows_high_consumption_alert_when_enabled(self):
        TenantPreferences.objects.create(
            tenant=self.tenant,
            show_consumption_alerts=True,
        )
        self.create_high_usage_reading()

        self.client.force_login(self.tenant_user)
        response = self.client.get(reverse('tenant_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'High Consumption Alert')
        self.assertContains(response, 'High')

    def test_tenant_dashboard_hides_high_consumption_alert_when_disabled(self):
        TenantPreferences.objects.create(
            tenant=self.tenant,
            show_consumption_alerts=False,
        )
        self.create_high_usage_reading()

        self.client.force_login(self.tenant_user)
        response = self.client.get(reverse('tenant_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'High Consumption Alert')


class LoginMessageTests(TestCase):
    def test_invalid_credentials_show_only_generic_login_error(self):
        response = self.client.post(
            reverse('login'),
            {'username': 'unknown', 'password': 'wrong'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Invalid username or password.')
        self.assertNotContains(response, 'profile not found')

    def test_missing_profile_does_not_show_internal_login_error(self):
        User.objects.create_user(
            username='manager-without-profile',
            password='password123',
            role='PROPERTY_MANAGER',
        )

        response = self.client.post(
            reverse('login'),
            {'username': 'manager-without-profile', 'password': 'password123'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Invalid username or password.')
        self.assertNotContains(response, 'Property Manager profile not found')
