import logging
from datetime import datetime

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect

from ..decorators import manager_required
from ..excel_exporter import ConsumptionExporter, InvoiceExporter, PaymentExporter
from ..export_helpers import excel_http_response, pdf_http_response, safe_download_filename
from ..models import (
    Invoice,
    MeterReading,
    Payment,
    PropertyManager,
    Tenant,
)
from ..pdf_generator import InvoicePDF, PaymentReceiptPDF

logger = logging.getLogger(__name__)
User = get_user_model()


def _payments_export_queryset(manager, request):
    payments = Payment.objects.filter(
        invoice__unit__manager=manager,
    ).select_related(
        'invoice__unit',
        'invoice__tenant__user',
        'recorded_by',
    ).order_by('-payment_date')

    method_filter = request.GET.get('method', '').strip().upper()
    if method_filter:
        payments = payments.filter(payment_method=method_filter)

    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()
    if start_date:
        payments = payments.filter(payment_date__gte=start_date)
    if end_date:
        payments = payments.filter(payment_date__lte=end_date)

    search_query = request.GET.get('search', '').strip()
    if search_query:
        payments = payments.filter(
            Q(invoice__invoice_number__icontains=search_query)
            | Q(mpesa_reference__icontains=search_query)
        )
    return payments


@login_required
def download_invoice_pdf(request, invoice_id):
    """Generate and download invoice as PDF."""
    try:
        if request.user.role == 'PROPERTY_MANAGER':
            manager = PropertyManager.objects.get(user=request.user)
            invoice = get_object_or_404(Invoice, id=invoice_id, unit__manager=manager)
        else:
            tenant = Tenant.objects.get(user=request.user)
            invoice = get_object_or_404(Invoice, id=invoice_id, tenant=tenant)

        pdf_bytes = InvoicePDF(invoice).generate_bytes()
        filename = safe_download_filename(f'invoice_{invoice.invoice_number}', 'pdf')
        return pdf_http_response(pdf_bytes, filename)
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')
    except Tenant.DoesNotExist:
        messages.error(request, 'Tenant profile not found')
        return redirect('tenant_dashboard')
    except Exception as exc:
        logger.exception('Invoice PDF generation failed for invoice %s', invoice_id)
        messages.error(request, f'Could not generate PDF: {exc}')
        return redirect('invoice_detail', invoice_id=invoice_id)


@login_required
def download_payment_receipt(request, payment_id):
    """Generate and download payment receipt as PDF."""
    try:
        if request.user.role == 'PROPERTY_MANAGER':
            manager = PropertyManager.objects.get(user=request.user)
            payment = get_object_or_404(Payment, id=payment_id, invoice__unit__manager=manager)
        else:
            tenant = Tenant.objects.get(user=request.user)
            payment = get_object_or_404(Payment, id=payment_id, invoice__tenant=tenant)

        pdf_bytes = PaymentReceiptPDF(payment).generate_bytes()
        filename = safe_download_filename(
            f'receipt_{payment.invoice.invoice_number}_{payment.id}',
            'pdf',
        )
        return pdf_http_response(pdf_bytes, filename)
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')
    except Tenant.DoesNotExist:
        messages.error(request, 'Tenant profile not found')
        return redirect('tenant_dashboard')
    except Exception as exc:
        logger.exception('Payment receipt PDF failed for payment %s', payment_id)
        messages.error(request, f'Could not generate receipt: {exc}')
        return redirect('payment_list')


@manager_required
def export_invoices_excel(request):
    """Export invoices to Excel."""
    try:
        manager = PropertyManager.objects.get(user=request.user)

        invoices = Invoice.objects.filter(
            unit__manager=manager,
        ).select_related('unit', 'tenant__user').order_by('-invoice_date')

        status_filter = request.GET.get('status')
        if status_filter:
            invoices = invoices.filter(status=status_filter)

        unit_filter = request.GET.get('unit')
        if unit_filter:
            invoices = invoices.filter(unit__id=unit_filter)

        exporter = InvoiceExporter()
        exporter.generate(invoices, None)
        filename = f'invoices_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        return excel_http_response(exporter.wb, filename)
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')
    except Exception as exc:
        logger.exception('Invoice Excel export failed')
        messages.error(request, f'Could not export invoices: {exc}')
        return redirect('invoice_list')


@manager_required
def export_payments_excel(request):
    """Export payments to Excel."""
    try:
        manager = PropertyManager.objects.get(user=request.user)
        payments = _payments_export_queryset(manager, request)

        exporter = PaymentExporter()
        exporter.generate(payments, None)
        filename = f'payments_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        return excel_http_response(exporter.wb, filename)
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')
    except Exception as exc:
        logger.exception('Payment Excel export failed')
        messages.error(request, f'Could not export payments: {exc}')
        return redirect('payment_list')


@manager_required
def export_consumption_excel(request):
    """Export consumption data to Excel."""
    try:
        manager = PropertyManager.objects.get(user=request.user)

        readings = MeterReading.objects.filter(
            meter__unit__manager=manager,
        ).select_related('meter__unit', 'recorded_by').order_by('-reading_date')

        unit_filter = request.GET.get('unit')
        if unit_filter:
            readings = readings.filter(meter__unit__id=unit_filter)

        meter_type_filter = request.GET.get('meter_type')
        if meter_type_filter:
            readings = readings.filter(meter__meter_type=meter_type_filter)

        if request.GET.get('anomalies') == 'true':
            readings = readings.filter(is_anomaly=True)

        search_query = request.GET.get('search', '').strip()
        if search_query:
            readings = readings.filter(
                Q(meter__unit__unit_number__icontains=search_query)
                | Q(notes__icontains=search_query)
            )

        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        if start_date:
            readings = readings.filter(reading_date__date__gte=start_date)
        if end_date:
            readings = readings.filter(reading_date__date__lte=end_date)

        exporter = ConsumptionExporter()
        exporter.generate(readings, None)
        filename = f'consumption_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        return excel_http_response(exporter.wb, filename)
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')
    except Exception as exc:
        logger.exception('Consumption Excel export failed')
        messages.error(request, f'Could not export consumption data: {exc}')
        return redirect('meter_reading_list')
