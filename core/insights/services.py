"""Rule-based smart insights for property manager dashboards."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from ..models import Invoice, Meter, MeterReading, Payment, Tenant, Unit
from ..views.helpers import refresh_invoice_statuses

MIN_BASELINE_READINGS = 3
COLLECTION_RATE_WARN = 70


def _insight(level, icon, message, *, link_name=None, link_kwargs=None, link_label='View'):
    item = {'level': level, 'icon': icon, 'message': message, 'link': None, 'link_label': link_label}
    if link_name:
        item['link'] = reverse(link_name, kwargs=link_kwargs or {})
    return item


def _high_usage_spikes(manager):
    """Units whose latest reading exceeds the stored smart baseline upper bound."""
    spikes = []
    meters = Meter.objects.filter(
        unit__manager=manager,
        is_active=True,
    ).select_related('unit', 'usage_baseline')

    for meter in meters:
        baseline = getattr(meter, 'usage_baseline', None)
        if not baseline or baseline.sample_size < MIN_BASELINE_READINGS:
            continue

        latest = (
            MeterReading.objects.filter(meter=meter)
            .exclude(verification_status='REJECTED')
            .order_by('-reading_date')
            .first()
        )
        if not latest or baseline.mean_consumption <= 0:
            continue

        if latest.consumption > baseline.upper_bound:
            pct = float(
                (latest.consumption - baseline.mean_consumption) / baseline.mean_consumption * 100
            )
            spikes.append({
                'unit_id': meter.unit_id,
                'unit_number': meter.unit.unit_number,
                'meter_label': meter.get_meter_type_display(),
                'pct': pct,
            })

    spikes.sort(key=lambda s: s['pct'], reverse=True)
    return spikes


def _units_missing_readings_this_month(manager, month_start, month_end):
    """Active tenanted units with an active meter but no reading in the current month."""
    missing = 0
    units = Unit.objects.filter(manager=manager).prefetch_related('meter_set')
    for unit in units:
        if not Tenant.objects.filter(unit=unit, is_active=True).exists():
            continue
        for meter in unit.meter_set.filter(is_active=True):
            has_reading = MeterReading.objects.filter(
                meter=meter,
                reading_date__gte=month_start,
                reading_date__lte=month_end,
            ).exclude(verification_status='REJECTED').exists()
            if not has_reading:
                missing += 1
    return missing


def get_manager_insights(manager, limit=5):
    """
    Return prioritized plain-language insights (newest/most urgent first).
    """
    today = timezone.now().date()
    month_start = today.replace(day=1)
    month_end = timezone.now()
    insights = []

    pending_anomalies = MeterReading.objects.filter(
        meter__unit__manager=manager,
        is_anomaly=True,
        verification_status='PENDING',
    ).count()
    if pending_anomalies:
        insights.append(_insight(
            'critical',
            'alert-triangle',
            (
                f'{pending_anomalies} meter reading(s) need review before you can bill safely. '
                'Verify or reject flagged anomalies first.'
            ),
            link_name='meter_reading_list',
            link_label='Review readings',
        ))

    open_invoices = Invoice.objects.filter(
        unit__manager=manager,
        status__in=['UNPAID', 'PARTIALLY_PAID', 'OVERDUE'],
    )
    refresh_invoice_statuses(open_invoices)

    overdue_count = Invoice.objects.filter(unit__manager=manager, status='OVERDUE').count()
    if overdue_count:
        insights.append(_insight(
            'critical',
            'alert-circle',
            f'{overdue_count} invoice(s) are overdue. Prioritize follow-up and payment recording.',
            link_name='invoice_list',
            link_label='View invoices',
        ))

    due_soon = Invoice.objects.filter(
        unit__manager=manager,
        status__in=['UNPAID', 'PARTIALLY_PAID'],
        due_date__gte=today,
        due_date__lte=today + timedelta(days=7),
    ).count()
    if due_soon:
        insights.append(_insight(
            'warning',
            'clock',
            f'{due_soon} invoice(s) are due within the next 7 days.',
            link_name='invoice_list',
            link_label='View invoices',
        ))

    spikes = _high_usage_spikes(manager)
    if spikes:
        top = spikes[0]
        if len(spikes) == 1:
            msg = (
                f'Unit {top["unit_number"]} {top["meter_label"].lower()} usage is '
                f'{top["pct"]:.0f}% above its recent average — worth checking for leaks or waste.'
            )
        else:
            msg = (
                f'{len(spikes)} unit(s) show unusually high usage vs recent averages '
                f'(highest: Unit {top["unit_number"]} at +{top["pct"]:.0f}%).'
            )
        insights.append(_insight(
            'warning',
            'trending-up',
            msg,
            link_name='unit_performance',
            link_kwargs={'unit_id': top['unit_id']},
            link_label='Unit details',
        ))

    invoices_mtd = Invoice.objects.filter(
        unit__manager=manager,
        invoice_date__gte=month_start,
        invoice_date__lte=today,
    )
    billed_mtd = invoices_mtd.aggregate(t=Sum('total_due'))['t'] or Decimal('0')
    if billed_mtd > 0:
        collected_mtd = Payment.objects.filter(
            invoice__unit__manager=manager,
            payment_date__gte=month_start,
            payment_date__lte=today,
        ).aggregate(t=Sum('amount_paid'))['t'] or Decimal('0')
        rate = float(collected_mtd / billed_mtd * 100)
        if rate < COLLECTION_RATE_WARN:
            insights.append(_insight(
                'warning',
                'percent',
                (
                    f'Collection rate this month is {rate:.0f}% '
                    f'(KES {collected_mtd:,.2f} of KES {billed_mtd:,.2f} billed).'
                ),
                link_name='reports_center',
                link_label='Financial report',
            ))

    missing_readings = _units_missing_readings_this_month(manager, month_start, month_end)
    if missing_readings:
        insights.append(_insight(
            'info',
            'gauge',
            f'{missing_readings} active meter(s) still have no reading recorded this month.',
            link_name='enter_meter_reading',
            link_label='Enter reading',
        ))

    vacant = Unit.objects.filter(manager=manager).count() - Tenant.objects.filter(
        unit__manager=manager, is_active=True
    ).count()
    if vacant > 0 and len(insights) < limit:
        insights.append(_insight(
            'info',
            'home',
            f'{vacant} unit(s) are currently vacant — no active tenant assigned.',
            link_name='manage_units',
            link_label='Manage units',
        ))

    if not insights:
        insights.append(_insight(
            'success',
            'check-circle',
            'No urgent issues detected. Usage, billing, and collections look healthy for now.',
            link_name='reports_center',
            link_label='Open reports',
        ))

    return insights[:limit]
