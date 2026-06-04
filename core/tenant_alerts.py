"""Tenant-facing consumption alert helpers."""

from .models import MeterReading, TenantPreferences

HIGH_CONSUMPTION_ANOMALY_TYPES = {
    'above_baseline',
    'usage_spike',
    'hard_limit_exceeded',
}


def tenant_consumption_alerts(tenant, *, meter_type=None, limit=3):
    """Return high-consumption readings when the tenant has alerts enabled."""
    preferences = TenantPreferences.objects.filter(tenant=tenant).first()
    if preferences and not preferences.show_consumption_alerts:
        return []

    if not tenant.unit:
        return []

    readings = MeterReading.objects.filter(
        meter__unit=tenant.unit,
        is_anomaly=True,
        anomaly_type__in=HIGH_CONSUMPTION_ANOMALY_TYPES,
    ).exclude(
        verification_status='REJECTED',
    ).select_related(
        'meter',
        'meter__usage_baseline',
    ).order_by('-reading_date')

    if meter_type:
        readings = readings.filter(meter__meter_type=meter_type)

    alerts = []
    for reading in readings[:limit]:
        baseline = getattr(reading.meter, 'usage_baseline', None)
        percent_above = None
        if baseline and baseline.mean_consumption > 0:
            percent_above = (
                (reading.consumption - baseline.mean_consumption)
                / baseline.mean_consumption
                * 100
            )

        alerts.append({
            'reading': reading,
            'meter_label': reading.meter.get_meter_type_display(),
            'reason': reading.get_anomaly_label(),
            'percent_above': percent_above,
        })

    return alerts


def mark_high_consumption_readings(readings, alerts):
    """Attach lightweight template flags to readings already being displayed."""
    alert_ids = {alert['reading'].id for alert in alerts}
    for reading in readings:
        reading.is_high_consumption_alert = reading.id in alert_ids
    return readings
