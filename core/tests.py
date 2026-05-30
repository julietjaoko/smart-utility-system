from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .models import AccountBalance, Invoice, Payment, PropertyManager, Tenant, Unit, User
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

    def make_invoice(self, number, subtotal, previous_balance):
        today = timezone.now().date()
        return Invoice.objects.create(
            unit=self.unit,
            tenant=self.tenant,
            invoice_number=number,
            invoice_date=today,
            due_date=today + timedelta(days=10),
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
