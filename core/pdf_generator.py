"""
PDF Generation utility for invoices and receipts.
Uses ReportLab to create professional PDF documents.
"""

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.pdfgen import canvas
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from datetime import datetime
from io import BytesIO

from .export_helpers import tenant_display_name


class InvoicePDF:
    """
    Generate professional PDF invoices.
    """
    
    def __init__(self, invoice):
        self.invoice = invoice
        self.pagesize = A4
        self.width, self.height = self.pagesize
        
    def generate_bytes(self):
        """Build PDF in memory and return raw bytes."""
        buffer = BytesIO()
        self._build_document(buffer)
        return buffer.getvalue()

    def generate(self, filename):
        """Generate PDF and save to file."""
        self._build_document(filename)
        return filename

    def _build_document(self, dest):
        doc = SimpleDocTemplate(
            dest,
            pagesize=self.pagesize,
            rightMargin=0.75*inch,
            leftMargin=0.75*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch
        )
        
        # Container for PDF elements
        elements = []
        
        # Define styles
        styles = getSampleStyleSheet()
        
        # Custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#059669'),
            spaceAfter=12,
            alignment=TA_LEFT
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#0F172A'),
            spaceAfter=12,
            spaceBefore=12
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#0F172A')
        )
        
        # Header - Company Info
        header_data = [
            [Paragraph('<b>SMART UTILITY MANAGEMENT SYSTEM</b>', title_style)],
            [Paragraph('Estate Utility Billing & Management', normal_style)],
            [Paragraph(f'Generated: {datetime.now().strftime("%B %d, %Y")}', normal_style)]
        ]
        
        header_table = Table(header_data, colWidths=[6*inch])
        header_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 0.3*inch))
        
        # Invoice Title
        elements.append(Paragraph(f'INVOICE #{self.invoice.invoice_number}', title_style))
        elements.append(Spacer(1, 0.2*inch))
        
        # Invoice Details
        invoice_info_data = [
            ['Billing Period:', self.invoice.billing_period],
            ['Invoice Date:', self.invoice.invoice_date.strftime('%B %d, %Y')],
            ['Due Date:', self.invoice.due_date.strftime('%B %d, %Y')],
            ['Status:', self.invoice.get_status_display().upper()],
        ]
        
        invoice_info_table = Table(invoice_info_data, colWidths=[1.5*inch, 2*inch])
        invoice_info_table.setStyle(TableStyle([
            ('FONT', (0, 0), (0, -1), 'Helvetica-Bold', 10),
            ('FONT', (1, 0), (1, -1), 'Helvetica', 10),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#64748B')),
            ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#0F172A')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        elements.append(invoice_info_table)
        elements.append(Spacer(1, 0.3*inch))
        
        # Bill To Section
        elements.append(Paragraph('BILL TO', heading_style))
        
        tenant_name = tenant_display_name(self.invoice)
        tenant_email = ''
        if self.invoice.tenant and self.invoice.tenant.user:
            tenant_email = self.invoice.tenant.user.email or ''

        bill_to_data = [
            [Paragraph(f'<b>{tenant_name}</b>', normal_style)],
            [f'Unit: {self.invoice.unit.unit_number}'],
            [f'Estate: {self.invoice.unit.estate_name}'],
        ]
        if tenant_email:
            bill_to_data.append([f'Email: {tenant_email}'])
        
        bill_to_table = Table(bill_to_data, colWidths=[3*inch])
        bill_to_table.setStyle(TableStyle([
            ('FONT', (0, 0), (0, 0), 'Helvetica-Bold', 11),
            ('FONT', (0, 1), (0, -1), 'Helvetica', 10),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#0F172A')),
        ]))
        elements.append(bill_to_table)
        elements.append(Spacer(1, 0.3*inch))
        
        # Charges Table
        elements.append(Paragraph('CHARGES BREAKDOWN', heading_style))
        
        charges_data = [
            ['Description', 'Quantity', 'Rate', 'Amount (KES)']
        ]
        
        water_units = self.invoice.water_units or 0
        if water_units > 0:
            charges_data.append([
                'Water Consumption',
                f'{water_units} m³',
                f'{self.invoice.water_rate}',
                f'{self.invoice.water_charge:,.2f}'
            ])
        
        electricity_units = self.invoice.electricity_units or 0
        if electricity_units > 0:
            charges_data.append([
                'Electricity Consumption',
                f'{electricity_units} kWh',
                f'{self.invoice.electricity_rate}',
                f'{self.invoice.electricity_charge:,.2f}'
            ])
        
        # Add fixed charges
        if self.invoice.fixed_charges_breakdown:
            for charge_name, amount in self.invoice.fixed_charges_breakdown.items():
                charges_data.append([
                    charge_name,
                    '1 month',
                    amount,
                    f'{float(amount):,.2f}'
                ])
        
        charges_table = Table(charges_data, colWidths=[2.5*inch, 1.2*inch, 1*inch, 1.3*inch])
        charges_table.setStyle(TableStyle([
            # Header row
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#059669')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 10),
            ('ALIGN', (0, 0), (-1, 0), 'LEFT'),
            
            # Data rows
            ('FONT', (0, 1), (-1, -1), 'Helvetica', 9),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#0F172A')),
            ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
            ('ALIGN', (0, 1), (0, -1), 'LEFT'),
            
            # Grid
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
        ]))
        elements.append(charges_table)
        elements.append(Spacer(1, 0.2*inch))
        
        # Summary Section
        summary_data = [
            ['Subtotal (Current Charges):', f'KES {self.invoice.subtotal:,.2f}'],
        ]
        
        # ALWAYS show previous balance for clarity
        if self.invoice.previous_balance < 0:
            summary_data.append([
                'Credit from Previous Month:',
                f'- KES {abs(self.invoice.previous_balance):,.2f}'
            ])
        else:
            summary_data.append([
                'Balance Brought Forward:',
                f'KES {self.invoice.previous_balance:,.2f}'
            ])
        
        summary_data.append([
            'TOTAL AMOUNT DUE:',
            f'KES {self.invoice.total_due:,.2f}',
        ])

        summary_table = Table(summary_data, colWidths=[4 * inch, 2 * inch])
        summary_table.setStyle(TableStyle([
            ('FONT', (0, 0), (0, -2), 'Helvetica', 10),
            ('FONT', (1, 0), (1, -2), 'Helvetica', 10),
            ('FONT', (0, -1), (-1, -1), 'Helvetica-Bold', 12),
            ('TEXTCOLOR', (0, 0), (-1, -2), colors.HexColor('#0F172A')),
            ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor('#059669')),
            ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
            ('LINEABOVE', (0, -1), (-1, -1), 2, colors.HexColor('#059669')),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 0.3*inch))
        
        # Payment Instructions
        elements.append(Paragraph('PAYMENT INSTRUCTIONS', heading_style))
        
        payment_info = """
        <b>M-Pesa Paybill:</b> 456902<br/>
        <b>Account Number:</b> {invoice_number}<br/>
        <br/>
        <b>Bank Transfer:</b><br/>
        Bank: Demo Commercial Bank<br/>
        Account Name: SUMS Utilities Collection<br/>
        Account Number: 000012345678<br/>
        Reference: {invoice_number}<br/>
        <br/>
        Please quote your invoice number ({invoice_number}) when making your payment.
        """.format(invoice_number=self.invoice.invoice_number)
        
        elements.append(Paragraph(payment_info, normal_style))
        elements.append(Spacer(1, 0.3*inch))
        
        # Footer
        footer_text = """
        <i>This is a computer-generated invoice. For any inquiries, please contact your property manager.</i>
        """
        footer_style = ParagraphStyle(
            'Footer',
            parent=styles['Normal'],
            fontSize=8,
            textColor=colors.HexColor('#64748B'),
            alignment=TA_CENTER
        )
        elements.append(Paragraph(footer_text, footer_style))
        
        doc.build(elements)


class PaymentReceiptPDF:
    """
    Generate payment receipt PDF.
    """
    
    def __init__(self, payment):
        self.payment = payment
        self.invoice = payment.invoice
        self.pagesize = A4
        self.width, self.height = self.pagesize
    
    def generate_bytes(self):
        buffer = BytesIO()
        self._build_document(buffer)
        return buffer.getvalue()

    def generate(self, filename):
        self._build_document(filename)
        return filename

    def _build_document(self, dest):
        doc = SimpleDocTemplate(
            dest,
            pagesize=self.pagesize,
            rightMargin=0.75*inch,
            leftMargin=0.75*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch
        )
        
        elements = []
        styles = getSampleStyleSheet()
        
        # Custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#059669'),
            spaceAfter=12,
            alignment=TA_CENTER
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#0F172A')
        )
        
        # Title
        elements.append(Paragraph('PAYMENT RECEIPT', title_style))
        elements.append(Spacer(1, 0.3*inch))
        
        # Receipt Details
        receipt_data = [
            ['Payment Date:', self.payment.payment_date.strftime('%B %d, %Y')],
            ['Invoice Number:', self.invoice.invoice_number],
            ['Payment Method:', self.payment.get_payment_method_display()],
        ]
        
        if self.payment.mpesa_reference:
            receipt_data.append(['M-Pesa Reference:', self.payment.mpesa_reference])
        
        receipt_data.append(['Amount Paid:', f'KES {self.payment.amount_paid:,.2f}'])
        
        receipt_table = Table(receipt_data, colWidths=[2*inch, 3*inch])
        receipt_table.setStyle(TableStyle([
            ('FONT', (0, 0), (0, -1), 'Helvetica-Bold', 11),
            ('FONT', (1, 0), (1, -1), 'Helvetica', 11),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#0F172A')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#D1FAE5')),
        ]))
        elements.append(receipt_table)
        elements.append(Spacer(1, 0.3*inch))
        
        tenant_data = [
            ['Tenant:', tenant_display_name(self.invoice)],
            ['Unit:', self.invoice.unit.unit_number],
            ['Estate:', self.invoice.unit.estate_name],
        ]
        
        tenant_table = Table(tenant_data, colWidths=[2*inch, 3*inch])
        tenant_table.setStyle(TableStyle([
            ('FONT', (0, 0), (0, -1), 'Helvetica-Bold', 10),
            ('FONT', (1, 0), (1, -1), 'Helvetica', 10),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#0F172A')),
        ]))
        elements.append(tenant_table)
        elements.append(Spacer(1, 0.5*inch))
        
        # Thank you message
        thank_you = Paragraph(
            '<b>Thank you for your payment!</b>',
            ParagraphStyle('ThankYou', parent=styles['Normal'], fontSize=14, alignment=TA_CENTER)
        )
        elements.append(thank_you)
        
        doc.build(elements)