import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ..models import AccountBalance, Invoice, Meter, MeterReading, Payment, Tenant

logger = logging.getLogger(__name__)

def refresh_invoice_statuses(invoices):
    """Refresh only open invoices because paid invoices no longer change with time."""
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
    """Allow manual token logging only where no active electricity meter exists."""
    if not tenant.unit:
        return False
    return not Meter.objects.filter(
        unit=tenant.unit,
        meter_type='ELECTRICITY',
        is_active=True
    ).exists()

def recalculate_meter_readings(meter):
    """Replay readings in date order so edited historical readings update later consumption."""
    for reading in MeterReading.objects.filter(meter=meter).order_by('reading_date', 'id'):
        reading.save()
