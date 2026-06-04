"""Per-meter consumption baselines for smart anomaly detection."""

from decimal import Decimal
from math import sqrt

from ..models import MeterReading, UnitMeterBaseline

MAX_BASELINE_SAMPLES = 6
MIN_BASELINE_SAMPLES = 3
STD_DEV_MULTIPLIER = Decimal('2.0')
FALLBACK_LOW_RATIO = Decimal('0.70')
FALLBACK_HIGH_RATIO = Decimal('1.30')

ANOMALY_LABELS = {
    'meter_rollback': 'Meter reading lower than previous',
    'zero_consumption': 'Zero or negative consumption',
    'hard_limit_exceeded': 'Above estate hard consumption limit',
    'below_baseline': 'Below this unit’s normal usage band',
    'above_baseline': 'Above this unit’s normal usage band',
    'usage_spike': 'Sudden increase vs recent readings',
    'usage_drop': 'Sudden decrease vs recent readings',
}


def _baseline_queryset(meter, *, before=None, exclude_pk=None):
    qs = MeterReading.objects.filter(meter=meter).exclude(verification_status='REJECTED')
    if before is not None:
        qs = qs.filter(reading_date__lt=before)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    tenant = meter.unit.tenants.filter(is_active=True).first()
    if tenant and tenant.move_in_date:
        qs = qs.filter(reading_date__date__gte=tenant.move_in_date)

    return qs.order_by('-reading_date')[:MAX_BASELINE_SAMPLES]


def compute_stats_from_readings(readings):
    """Return mean, std dev, bounds, and sample size from a list of readings."""
    values = [Decimal(r.consumption) for r in readings if r.consumption is not None]
    if not values:
        return None

    n = len(values)
    mean = sum(values) / Decimal(n)
    if n == 1:
        std = Decimal('0')
    else:
        variance = sum((v - mean) ** 2 for v in values) / Decimal(n)
        std = Decimal(str(sqrt(float(variance))))

    if std > 0:
        lower = max(Decimal('0'), mean - STD_DEV_MULTIPLIER * std)
        upper = mean + STD_DEV_MULTIPLIER * std
    else:
        lower = mean * FALLBACK_LOW_RATIO
        upper = mean * FALLBACK_HIGH_RATIO

    return {
        'sample_size': n,
        'mean_consumption': mean,
        'std_deviation': std,
        'lower_bound': lower,
        'upper_bound': upper,
    }


def build_baseline_for_meter(meter, *, before=None, exclude_pk=None):
    """Compute baseline statistics from historical readings (not persisted)."""
    readings = list(_baseline_queryset(meter, before=before, exclude_pk=exclude_pk))
    if len(readings) < MIN_BASELINE_SAMPLES:
        return None
    return compute_stats_from_readings(readings)


def refresh_meter_baseline(meter):
    """Persist baseline profile after a reading is saved."""
    readings = list(
        MeterReading.objects.filter(meter=meter)
        .exclude(verification_status='REJECTED')
        .order_by('-reading_date')[:MAX_BASELINE_SAMPLES]
    )
    if len(readings) < MIN_BASELINE_SAMPLES:
        UnitMeterBaseline.objects.filter(meter=meter).delete()
        return None

    stats = compute_stats_from_readings(readings)
    if not stats:
        return None

    baseline, _ = UnitMeterBaseline.objects.update_or_create(
        meter=meter,
        defaults={
            'sample_size': stats['sample_size'],
            'mean_consumption': stats['mean_consumption'],
            'std_deviation': stats['std_deviation'],
            'lower_bound': stats['lower_bound'],
            'upper_bound': stats['upper_bound'],
        },
    )
    return baseline


def detect_reading_anomalies(reading):
    """
    Evaluate a reading using hard limits plus per-meter statistical baseline.
    Returns (is_anomaly: bool, anomaly_type: str).
    """
    consumption = reading.consumption or Decimal('0')
    manager = reading.meter.unit.manager
    hard_limits = {
        'WATER': manager.water_anomaly_threshold,
        'ELECTRICITY': manager.electricity_anomaly_threshold,
    }
    max_expected = hard_limits.get(reading.meter.meter_type, Decimal('500.00'))

    previous = MeterReading.objects.filter(
        meter=reading.meter,
        reading_date__lt=reading.reading_date,
    ).exclude(verification_status='REJECTED').exclude(
        pk=reading.pk if reading.pk else None
    ).order_by('-reading_date').first()

    if previous and reading.reading_value < previous.reading_value:
        return True, 'meter_rollback'

    if consumption <= 0:
        return True, 'zero_consumption'

    if consumption > max_expected:
        return True, 'hard_limit_exceeded'

    baseline = build_baseline_for_meter(
        reading.meter,
        before=reading.reading_date,
        exclude_pk=reading.pk,
    )
    if baseline:
        if consumption < baseline['lower_bound']:
            return True, 'below_baseline'
        if consumption > baseline['upper_bound']:
            return True, 'above_baseline'
        return False, ''

    # Not enough history for a statistical profile — fall back to last 3 readings.
    recent = list(_baseline_queryset(
        reading.meter,
        before=reading.reading_date,
        exclude_pk=reading.pk,
    )[:3])
    if len(recent) >= 2:
        avg = sum(Decimal(r.consumption) for r in recent) / Decimal(len(recent))
        if avg > 0:
            lower = avg * FALLBACK_LOW_RATIO
            upper = avg * FALLBACK_HIGH_RATIO
            if consumption < lower:
                return True, 'usage_drop'
            if consumption > upper:
                return True, 'usage_spike'

    return False, ''
