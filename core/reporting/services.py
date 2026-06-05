"""Report data builders for the Reports Center."""

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..models import Invoice, Meter, MeterReading, Payment, Unit
from ..views.helpers import refresh_invoice_statuses


def parse_report_filters(request, manager):
    """Shared GET filters for reports and exports."""
    today = timezone.now().date()
    start_default = today.replace(day=1) - timedelta(days=90)
    start_date = request.GET.get('start_date') or start_default.isoformat()
    end_date = request.GET.get('end_date') or today.isoformat()
    unit_id = request.GET.get('unit') or ''
    meter_type = (request.GET.get('meter_type') or '').upper()
    report_type = request.GET.get('report', 'financial')

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        start = start_default
        end = today

    if start > end:
        start, end = end, start

    units = Unit.objects.filter(manager=manager).order_by('unit_number')
    unit = None
    if unit_id:
        unit = units.filter(id=unit_id).first()

    return {
        'report_type': report_type,
        'start_date': start,
        'end_date': end,
        'start_date_str': start.isoformat(),
        'end_date_str': end.isoformat(),
        'unit': unit,
        'unit_id': unit_id,
        'meter_type': meter_type,
        'units': units,
    }


def build_financial_report(manager, start_date, end_date, unit=None):
    invoices = Invoice.objects.filter(
        unit__manager=manager,
        invoice_date__gte=start_date,
        invoice_date__lte=end_date,
    )
    if unit:
        invoices = invoices.filter(unit=unit)
    refresh_invoice_statuses(invoices)

    payments = Payment.objects.filter(
        invoice__unit__manager=manager,
        payment_date__gte=start_date,
        payment_date__lte=end_date,
    )
    if unit:
        payments = payments.filter(invoice__unit=unit)

    total_billed = invoices.aggregate(t=Sum('total_due'))['t'] or Decimal('0.00')
    total_collected = payments.aggregate(t=Sum('amount_paid'))['t'] or Decimal('0.00')
    invoice_count = invoices.count()
    payment_count = payments.count()

    collection_rate = (
        float(total_collected) / float(total_billed) * 100
        if total_billed > 0 else 0
    )

    status_rows = list(
        invoices.values('status').annotate(count=Count('id')).order_by('status')
    )
    status_labels = dict(Invoice.STATUS_CHOICES)
    for row in status_rows:
        row['label'] = status_labels.get(row['status'], row['status'])

    method_rows = list(
        payments.values('payment_method').annotate(
            count=Count('id'),
            total=Sum('amount_paid'),
        ).order_by('-total')
    )
    method_labels = dict(Payment.PAYMENT_METHOD_CHOICES)
    for row in method_rows:
        row['label'] = method_labels.get(row['payment_method'], row['payment_method'])

    monthly = {}
    for inv in invoices.values('invoice_date', 'total_due'):
        key = inv['invoice_date'].strftime('%Y-%m')
        monthly.setdefault(key, {'billed': Decimal('0'), 'label': inv['invoice_date'].strftime('%b %Y')})
        monthly[key]['billed'] += inv['total_due'] or Decimal('0')

    for pay in payments.values('payment_date', 'amount_paid'):
        key = pay['payment_date'].strftime('%Y-%m')
        monthly.setdefault(key, {'billed': Decimal('0'), 'collected': Decimal('0'), 'label': pay['payment_date'].strftime('%b %Y')})
        monthly.setdefault(key, {}).setdefault('collected', Decimal('0'))
        monthly[key]['collected'] = monthly[key].get('collected', Decimal('0')) + (pay['amount_paid'] or Decimal('0'))

    monthly_rows = []
    for key in sorted(monthly.keys()):
        row = monthly[key]
        billed = row.get('billed', Decimal('0'))
        collected = row.get('collected', Decimal('0'))
        monthly_rows.append({
            'label': row['label'],
            'billed': billed,
            'collected': collected,
            'outstanding': billed - collected,
        })

    return {
        'title': 'Financial Summary',
        'total_billed': total_billed,
        'total_collected': total_collected,
        'outstanding': total_billed - total_collected,
        'collection_rate': round(collection_rate, 1),
        'invoice_count': invoice_count,
        'payment_count': payment_count,
        'status_rows': status_rows,
        'method_rows': method_rows,
        'monthly_rows': monthly_rows,
    }


def build_arrears_report(manager, unit=None):
    invoices = Invoice.objects.filter(
        unit__manager=manager,
        status__in=['UNPAID', 'PARTIALLY_PAID', 'OVERDUE'],
    ).select_related('unit', 'tenant__user')
    if unit:
        invoices = invoices.filter(unit=unit)
    refresh_invoice_statuses(invoices)

    today = timezone.now().date()
    rows = []
    total_balance = Decimal('0.00')

    for invoice in invoices.order_by('due_date'):
        paid = Payment.objects.filter(invoice=invoice).aggregate(t=Sum('amount_paid'))['t'] or Decimal('0.00')
        balance = (invoice.total_due or Decimal('0.00')) - paid
        if balance <= 0:
            continue
        days_overdue = max(0, (today - invoice.due_date).days) if invoice.due_date < today else 0
        tenant_name = ''
        if invoice.tenant and invoice.tenant.user:
            tenant_name = invoice.tenant.user.get_full_name() or invoice.tenant.user.username
        rows.append({
            'invoice_number': invoice.invoice_number,
            'unit': invoice.unit.unit_number,
            'tenant': tenant_name,
            'billing_period': invoice.billing_period,
            'due_date': invoice.due_date,
            'status': invoice.get_status_display(),
            'total_due': invoice.total_due,
            'amount_paid': paid,
            'balance_due': balance,
            'days_overdue': days_overdue,
        })
        total_balance += balance

    rows.sort(key=lambda r: (-r['days_overdue'], -float(r['balance_due'])))

    return {
        'title': 'Arrears & Outstanding Balances',
        'rows': rows,
        'total_balance': total_balance,
        'account_count': len(rows),
    }


def build_consumption_report(manager, start_date, end_date, unit=None, meter_type=''):
    base_readings = MeterReading.objects.filter(
        meter__unit__manager=manager,
        reading_date__date__gte=start_date,
        reading_date__date__lte=end_date,
    ).exclude(verification_status='REJECTED').select_related('meter__unit')

    if unit:
        base_readings = base_readings.filter(meter__unit=unit)
    if meter_type in ('WATER', 'ELECTRICITY'):
        base_readings = base_readings.filter(meter__meter_type=meter_type)

    readings = base_readings.filter(verification_status='VERIFIED')

    totals = readings.aggregate(
        total=Coalesce(Sum('consumption'), Value(Decimal('0'))),
    )
    total_consumption = totals['total'] or Decimal('0')
    anomaly_count = base_readings.filter(is_anomaly=True).count()
    reading_count = readings.count()

    by_unit = list(
        readings.values(
            'meter__unit__unit_number',
            'meter__meter_type',
        ).annotate(
            total=Sum('consumption'),
            readings=Count('id'),
            anomalies=Count('id', filter=Q(is_anomaly=True)),
        ).order_by('-total')[:50]
    )

    meter_labels = dict(Meter.METER_TYPE_CHOICES)
    for row in by_unit:
        row['meter_label'] = meter_labels.get(row['meter__meter_type'], row['meter__meter_type'])

    return {
        'title': 'Consumption Summary',
        'total_consumption': total_consumption,
        'reading_count': reading_count,
        'anomaly_count': anomaly_count,
        'by_unit': by_unit,
    }


def build_anomaly_report(manager, start_date, end_date, unit=None):
    readings = MeterReading.objects.filter(
        meter__unit__manager=manager,
        reading_date__date__gte=start_date,
        reading_date__date__lte=end_date,
        is_anomaly=True,
    ).select_related('meter__unit', 'recorded_by').order_by('-reading_date')

    if unit:
        readings = readings.filter(meter__unit=unit)

    rows = []
    for reading in readings[:200]:
        rows.append({
            'date': reading.reading_date.date(),
            'unit': reading.meter.unit.unit_number,
            'meter_type': reading.meter.get_meter_type_display(),
            'consumption': reading.consumption,
            'verification_status': reading.get_verification_status_display(),
            'anomaly_type': reading.anomaly_type or '—',
            'recorded_by': reading.recorded_by.username if reading.recorded_by else '—',
        })

    return {
        'title': 'Anomaly Report',
        'rows': rows,
        'total': readings.count(),
    }
