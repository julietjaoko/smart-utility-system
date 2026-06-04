import logging
from datetime import datetime

from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import manager_required, system_admin_required
from ..export_helpers import excel_http_response
from ..excel_exporter import (
    AnomalyReportExporter,
    ArrearsExporter,
    AuditLogExporter,
    ConsumptionSummaryExporter,
    FinancialSummaryExporter,
)
from ..models import AuditLog, PropertyManager
from ..reporting.services import (
    build_anomaly_report,
    build_arrears_report,
    build_consumption_report,
    build_financial_report,
    parse_report_filters,
)

logger = logging.getLogger(__name__)


def _get_manager(request):
    return get_object_or_404(PropertyManager, user=request.user)


def _build_report_payload(manager, filters):
    report_type = filters['report_type']
    start = filters['start_date']
    end = filters['end_date']
    unit = filters['unit']

    if report_type == 'arrears':
        return build_arrears_report(manager, unit=unit)
    if report_type == 'consumption':
        return build_consumption_report(
            manager, start, end, unit=unit, meter_type=filters['meter_type'],
        )
    if report_type == 'anomalies':
        return build_anomaly_report(manager, start, end, unit=unit)
    return build_financial_report(manager, start, end, unit=unit)


@manager_required
def reports_center(request):
    """Unified reporting hub with preview and export actions."""
    manager = _get_manager(request)
    filters = parse_report_filters(request, manager)
    report_data = _build_report_payload(manager, filters)

    context = {
        'filters': filters,
        'report_data': report_data,
        'report_types': [
            ('financial', 'Financial Summary', 'Billed vs collected, collection rate, payment methods'),
            ('arrears', 'Arrears & Outstanding', 'Unpaid balances by unit with days overdue'),
            ('consumption', 'Consumption Summary', 'Usage totals and per-unit breakdown'),
            ('anomalies', 'Anomaly Report', 'Flagged meter readings requiring review'),
        ],
    }
    return render(request, 'core/reports_center.html', context)


@manager_required
def export_report_excel(request):
    """Download the active report type as Excel."""
    manager = _get_manager(request)
    filters = parse_report_filters(request, manager)
    report_data = _build_report_payload(manager, filters)

    temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp')
    os.makedirs(temp_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_type = filters['report_type']

    if report_type == 'arrears':
        exporter = ArrearsExporter()
        filename = f'arrears_report_{stamp}.xlsx'
    elif report_type == 'anomalies':
        exporter = AnomalyReportExporter()
        filename = f'anomaly_report_{stamp}.xlsx'
    elif report_type == 'consumption':
        exporter = ConsumptionSummaryExporter()
        filename = f'consumption_report_{stamp}.xlsx'
    else:
        exporter = FinancialSummaryExporter()
        filename = f'financial_report_{stamp}.xlsx'

    filepath = os.path.join(temp_dir, filename)
    exporter.generate(report_data, filters, filepath)

    with open(filepath, 'rb') as f:
        response = HttpResponse(
            f.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


def _activity_log_queryset(request, manager=None):
    qs = AuditLog.objects.select_related('actor', 'property_manager__user')
    if manager is not None:
        qs = qs.filter(property_manager=manager)
    category = request.GET.get('category', '').strip()
    if category:
        qs = qs.filter(category=category)
    action = request.GET.get('action', '').strip()
    if action:
        qs = qs.filter(action__icontains=action)
    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()
    if start_date:
        qs = qs.filter(created_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(created_at__date__lte=end_date)
    return qs


@manager_required
def activity_logs(request):
    """Audit trail for the signed-in property manager."""
    manager = _get_manager(request)
    logs = _activity_log_queryset(request, manager=manager)
    paginator = Paginator(logs, 40)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'categories': AuditLog.CATEGORY_CHOICES,
        'current_filters': {
            'category': request.GET.get('category', ''),
            'action': request.GET.get('action', ''),
            'start_date': request.GET.get('start_date', ''),
            'end_date': request.GET.get('end_date', ''),
        },
        'is_system_view': False,
    }
    return render(request, 'core/activity_logs.html', context)


@manager_required
def export_activity_logs_excel(request):
    manager = _get_manager(request)
    logs = list(_activity_log_queryset(request, manager=manager)[:5000])

    filename = f'activity_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    exporter = AuditLogExporter()
    exporter.generate(logs, None)
    return excel_http_response(exporter.wb, filename)


@system_admin_required
def system_admin_activity_logs(request):
    logs = _activity_log_queryset(request, manager=None)
    paginator = Paginator(logs, 40)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'categories': AuditLog.CATEGORY_CHOICES,
        'current_filters': {
            'category': request.GET.get('category', ''),
            'action': request.GET.get('action', ''),
            'start_date': request.GET.get('start_date', ''),
            'end_date': request.GET.get('end_date', ''),
        },
        'is_system_view': True,
    }
    return render(request, 'core/activity_logs.html', context)


@system_admin_required
def system_admin_export_activity_logs(request):
    logs = list(_activity_log_queryset(request, manager=None)[:10000])

    filename = f'platform_activity_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    exporter = AuditLogExporter()
    exporter.generate(logs, None, include_manager=True)
    return excel_http_response(exporter.wb, filename)
