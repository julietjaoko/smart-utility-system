import logging
import os
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect

from ..decorators import manager_required
from ..excel_exporter import ConsumptionExporter, InvoiceExporter, PaymentExporter
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

@login_required
def download_invoice_pdf(request, invoice_id):
    """
    Generate and download invoice as PDF.
    """
    # Get invoice based on user role
    if request.user.role == 'PROPERTY_MANAGER':
        manager = PropertyManager.objects.get(user=request.user)
        invoice = get_object_or_404(Invoice, id=invoice_id, unit__manager=manager)
    else:  # Tenant
        tenant = Tenant.objects.get(user=request.user)
        invoice = get_object_or_404(Invoice, id=invoice_id, tenant=tenant)
    
    # Generate PDF
    pdf_generator = InvoicePDF(invoice)
    
    # Create temp directory if it doesn't exist
    temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp')
    os.makedirs(temp_dir, exist_ok=True)
    
    # Generate filename
    filename = f'invoice_{invoice.invoice_number}.pdf'
    filepath = os.path.join(temp_dir, filename)
    
    # Generate PDF
    pdf_generator.generate(filepath)
    
    # Return PDF as download
    response = FileResponse(open(filepath, 'rb'), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response

@login_required
def download_payment_receipt(request, payment_id):
    """
    Generate and download payment receipt as PDF.
    """
    # Get payment based on user role
    if request.user.role == 'PROPERTY_MANAGER':
        manager = PropertyManager.objects.get(user=request.user)
        payment = get_object_or_404(Payment, id=payment_id, invoice__unit__manager=manager)
    else:  # Tenant
        tenant = Tenant.objects.get(user=request.user)
        payment = get_object_or_404(Payment, id=payment_id, invoice__tenant=tenant)
    
    # Generate PDF
    pdf_generator = PaymentReceiptPDF(payment)
    
    # Create temp directory
    temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp')
    os.makedirs(temp_dir, exist_ok=True)
    
    # Generate filename
    filename = f'receipt_{payment.invoice.invoice_number}_{payment.id}.pdf'
    filepath = os.path.join(temp_dir, filename)
    
    # Generate PDF
    pdf_generator.generate(filepath)
    
    # Return PDF
    response = FileResponse(open(filepath, 'rb'), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response

@manager_required
def export_invoices_excel(request):
    """
    Export invoices to Excel.
    """
    # Security check
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # Get invoices with same filters as invoice list
        invoices = Invoice.objects.filter(
            unit__manager=manager
        ).select_related('unit', 'tenant__user').order_by('-invoice_date')
        
        # Apply filters if any
        status_filter = request.GET.get('status')
        if status_filter:
            invoices = invoices.filter(status=status_filter)
        
        unit_filter = request.GET.get('unit')
        if unit_filter:
            invoices = invoices.filter(unit__id=unit_filter)
        
        # Generate Excel
        exporter = InvoiceExporter()
        
        # Create temp directory
        temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename = f'invoices_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        filepath = os.path.join(temp_dir, filename)
        
        exporter.generate(invoices, filepath)
        
        # Return Excel file
        with open(filepath, 'rb') as f:
            response = HttpResponse(
                f.read(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')

@manager_required
def export_payments_excel(request):
    """
    Export payments to Excel.
    """
    # Security check
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # Get payments with filters
        payments = Payment.objects.filter(
            invoice__unit__manager=manager
        ).select_related('invoice__unit', 'invoice__tenant__user', 'recorded_by').order_by('-payment_date')
        
        # Apply filters
        method_filter = request.GET.get('method')
        if method_filter:
            payments = payments.filter(payment_method=method_filter)
        
        start_date = request.GET.get('start_date')
        if start_date:
            payments = payments.filter(payment_date__gte=start_date)
        
        end_date = request.GET.get('end_date')
        if end_date:
            payments = payments.filter(payment_date__lte=end_date)
        
        # Generate Excel
        exporter = PaymentExporter()
        
        temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename = f'payments_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        filepath = os.path.join(temp_dir, filename)
        
        exporter.generate(payments, filepath)
        
        # Return Excel file
        with open(filepath, 'rb') as f:
            response = HttpResponse(
                f.read(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')

@manager_required
def export_consumption_excel(request):
    """
    Export consumption data to Excel.
    """
    # Security check
    
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # Get readings with filters
        readings = MeterReading.objects.filter(
            meter__unit__manager=manager
        ).select_related('meter__unit', 'recorded_by').order_by('-reading_date')
        
        # Apply filters
        unit_filter = request.GET.get('unit')
        if unit_filter:
            readings = readings.filter(meter__unit__id=unit_filter)
        
        meter_type_filter = request.GET.get('meter_type')
        if meter_type_filter:
            readings = readings.filter(meter__meter_type=meter_type_filter)
        
        anomalies_only = request.GET.get('anomalies')
        if anomalies_only == 'true':
            readings = readings.filter(is_anomaly=True)

        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        if start_date:
            readings = readings.filter(reading_date__date__gte=start_date)
        if end_date:
            readings = readings.filter(reading_date__date__lte=end_date)
        
        # Generate Excel
        exporter = ConsumptionExporter()
        
        temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename = f'consumption_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        filepath = os.path.join(temp_dir, filename)
        
        exporter.generate(readings, filepath)
        
        # Return Excel file
        with open(filepath, 'rb') as f:
            response = HttpResponse(
                f.read(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')
