"""
Email notification utilities.
Handles sending emails for invoices, payments, and alerts.
"""

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from decimal import Decimal


class EmailNotification:
    """
    Base class for email notifications.
    """
    
    def __init__(self):
        self.from_email = settings.DEFAULT_FROM_EMAIL
    
    def send_email(self, subject, to_email, text_content, html_content):
        """
        Send email with both text and HTML versions.
        
        Args:
            subject (str): Email subject
            to_email (str): Recipient email
            text_content (str): Plain text version
            html_content (str): HTML version
        
        Returns:
            bool: True if sent successfully
        """
        try:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=self.from_email,
                to=[to_email]
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send()
            return True
        except Exception as e:
            print(f"Error sending email: {str(e)}")
            return False


class InvoiceNotification(EmailNotification):
    """
    Send invoice generation notifications to tenants.
    """
    
    def send_invoice_notification(self, invoice):
        """
        Send invoice notification email.
        
        Args:
            invoice: Invoice object
        
        Returns:
            bool: True if sent successfully
        """
        tenant_email = invoice.tenant.user.email
        
        # Subject
        subject = f'New Invoice - {invoice.invoice_number} for {invoice.billing_period}'
        
        # Plain text version
        text_content = f"""
Dear {invoice.tenant.user.get_full_name() or invoice.tenant.user.username},

Your utility invoice for {invoice.billing_period} has been generated.

Invoice Details:
----------------
Invoice Number: {invoice.invoice_number}
Billing Period: {invoice.billing_period}
Due Date: {invoice.due_date.strftime('%B %d, %Y')}
Total Amount Due: KES {invoice.total_due:,.2f}

Charges Breakdown:
------------------
Water: KES {invoice.water_charge:,.2f}
Electricity: KES {invoice.electricity_charge:,.2f}
Fixed Charges: KES {invoice.total_fixed_charges:,.2f}
Subtotal: KES {invoice.subtotal:,.2f}
Previous Balance: KES {invoice.previous_balance:,.2f}
------------------
TOTAL DUE: KES {invoice.total_due:,.2f}

Payment Instructions:
---------------------
Please make payment before the due date to avoid late fees.
Log in to your account to view the full invoice and make payment.

Thank you,
Smart Utility Management System
        """
        
        # HTML version
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            line-height: 1.6;
            color: #0F172A;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .header {{
            background: linear-gradient(135deg, #059669 0%, #047857 100%);
            color: white;
            padding: 30px 20px;
            border-radius: 12px 12px 0 0;
            text-align: center;
        }}
        .header h1 {{
            margin: 0;
            font-size: 24px;
        }}
        .content {{
            background: #FFFFFF;
            padding: 30px 20px;
            border: 1px solid #E2E8F0;
            border-top: none;
        }}
        .invoice-details {{
            background: #F8FAFC;
            padding: 20px;
            border-radius: 8px;
            margin: 20px 0;
        }}
        .detail-row {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #E2E8F0;
        }}
        .detail-row:last-child {{
            border-bottom: none;
        }}
        .label {{
            color: #64748B;
            font-weight: 600;
        }}
        .value {{
            color: #0F172A;
            font-weight: 600;
        }}
        .total-row {{
            background: rgba(5, 150, 105, 0.1);
            padding: 15px;
            border-radius: 8px;
            margin-top: 20px;
        }}
        .total-row .value {{
            color: #059669;
            font-size: 20px;
        }}
        .button {{
            display: inline-block;
            background: #059669;
            color: white;
            padding: 12px 30px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 600;
            margin: 20px 0;
        }}
        .footer {{
            text-align: center;
            color: #64748B;
            font-size: 12px;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #E2E8F0;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📧 New Invoice Generated</h1>
    </div>
    
    <div class="content">
        <p>Dear <strong>{invoice.tenant.user.get_full_name() or invoice.tenant.user.username}</strong>,</p>
        
        <p>Your utility invoice for <strong>{invoice.billing_period}</strong> has been generated.</p>
        
        <div class="invoice-details">
            <div class="detail-row">
                <span class="label">Invoice Number:</span>
                <span class="value">{invoice.invoice_number}</span>
            </div>
            <div class="detail-row">
                <span class="label">Unit:</span>
                <span class="value">{invoice.unit.unit_number}</span>
            </div>
            <div class="detail-row">
                <span class="label">Due Date:</span>
                <span class="value">{invoice.due_date.strftime('%B %d, %Y')}</span>
            </div>
        </div>
        
        <h3 style="color: #0F172A; margin-top: 30px;">Charges Breakdown</h3>
        
        <div class="invoice-details">
            <div class="detail-row">
                <span class="label">Water Charges:</span>
                <span class="value">KES {invoice.water_charge:,.2f}</span>
            </div>
            <div class="detail-row">
                <span class="label">Electricity Charges:</span>
                <span class="value">KES {invoice.electricity_charge:,.2f}</span>
            </div>
            <div class="detail-row">
                <span class="label">Fixed Charges:</span>
                <span class="value">KES {invoice.total_fixed_charges:,.2f}</span>
            </div>
            <div class="detail-row">
                <span class="label">Subtotal:</span>
                <span class="value">KES {invoice.subtotal:,.2f}</span>
            </div>
            <div class="detail-row">
                <span class="label">Previous Balance:</span>
                <span class="value">KES {invoice.previous_balance:,.2f}</span>
            </div>
        </div>
        
        <div class="total-row">
            <div class="detail-row">
                <span class="label" style="font-size: 16px;">TOTAL AMOUNT DUE:</span>
                <span class="value">KES {invoice.total_due:,.2f}</span>
            </div>
        </div>
        
        <center>
            <a href="#" class="button">View Full Invoice</a>
        </center>
        
        <p style="margin-top: 30px;">Please make payment before the due date to avoid late fees.</p>
        
        <div class="footer">
            <p>This is an automated email from Smart Utility Management System.</p>
            <p>Please do not reply to this email.</p>
        </div>
    </div>
</body>
</html>
        """
        
        return self.send_email(subject, tenant_email, text_content, html_content)


class PaymentNotification(EmailNotification):
    """
    Send payment confirmation notifications.
    """
    
    def send_payment_confirmation(self, payment):
        """
        Send payment confirmation email.
        
        Args:
            payment: Payment object
        
        Returns:
            bool: True if sent successfully
        """
        tenant_email = payment.invoice.tenant.user.email
        invoice = payment.invoice
        
        # Calculate remaining balance
        from django.db.models import Sum
        total_paid = invoice.payments.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
        remaining = invoice.total_due - total_paid
        
        # Subject
        subject = f'Payment Received - {invoice.invoice_number}'
        
        # Plain text version
        text_content = f"""
Dear {invoice.tenant.user.get_full_name() or invoice.tenant.user.username},

Your payment has been received and recorded.

Payment Details:
----------------
Payment Date: {payment.payment_date.strftime('%B %d, %Y')}
Amount Paid: KES {payment.amount_paid:,.2f}
Payment Method: {payment.get_payment_method_display()}
Invoice Number: {invoice.invoice_number}

Invoice Summary:
----------------
Total Due: KES {invoice.total_due:,.2f}
Total Paid: KES {total_paid:,.2f}
Remaining Balance: KES {remaining:,.2f}
Status: {invoice.get_status_display()}

Thank you for your payment!

Smart Utility Management System
        """
        
        # HTML version
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            line-height: 1.6;
            color: #0F172A;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .header {{
            background: linear-gradient(135deg, #10B981 0%, #059669 100%);
            color: white;
            padding: 30px 20px;
            border-radius: 12px 12px 0 0;
            text-align: center;
        }}
        .header h1 {{
            margin: 0;
            font-size: 24px;
        }}
        .content {{
            background: #FFFFFF;
            padding: 30px 20px;
            border: 1px solid #E2E8F0;
            border-top: none;
        }}
        .success-badge {{
            background: #D1FAE5;
            color: #065F46;
            padding: 10px 20px;
            border-radius: 8px;
            display: inline-block;
            font-weight: 600;
            margin: 20px 0;
        }}
        .payment-details {{
            background: #F8FAFC;
            padding: 20px;
            border-radius: 8px;
            margin: 20px 0;
        }}
        .detail-row {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #E2E8F0;
        }}
        .detail-row:last-child {{
            border-bottom: none;
        }}
        .label {{
            color: #64748B;
            font-weight: 600;
        }}
        .value {{
            color: #0F172A;
            font-weight: 600;
        }}
        .footer {{
            text-align: center;
            color: #64748B;
            font-size: 12px;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #E2E8F0;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>✅ Payment Received</h1>
    </div>
    
    <div class="content">
        <p>Dear <strong>{invoice.tenant.user.get_full_name() or invoice.tenant.user.username}</strong>,</p>
        
        <center>
            <div class="success-badge">Payment Successfully Recorded</div>
        </center>
        
        <p>We have received your payment for invoice <strong>{invoice.invoice_number}</strong>.</p>
        
        <h3 style="color: #0F172A; margin-top: 30px;">Payment Details</h3>
        
        <div class="payment-details">
            <div class="detail-row">
                <span class="label">Payment Date:</span>
                <span class="value">{payment.payment_date.strftime('%B %d, %Y')}</span>
            </div>
            <div class="detail-row">
                <span class="label">Amount Paid:</span>
                <span class="value" style="color: #10B981;">KES {payment.amount_paid:,.2f}</span>
            </div>
            <div class="detail-row">
                <span class="label">Payment Method:</span>
                <span class="value">{payment.get_payment_method_display()}</span>
            </div>
            {f'<div class="detail-row"><span class="label">M-Pesa Reference:</span><span class="value">{payment.mpesa_reference}</span></div>' if payment.mpesa_reference else ''}
        </div>
        
        <h3 style="color: #0F172A; margin-top: 30px;">Invoice Summary</h3>
        
        <div class="payment-details">
            <div class="detail-row">
                <span class="label">Invoice Number:</span>
                <span class="value">{invoice.invoice_number}</span>
            </div>
            <div class="detail-row">
                <span class="label">Total Due:</span>
                <span class="value">KES {invoice.total_due:,.2f}</span>
            </div>
            <div class="detail-row">
                <span class="label">Total Paid:</span>
                <span class="value" style="color: #10B981;">KES {total_paid:,.2f}</span>
            </div>
            <div class="detail-row">
                <span class="label">Remaining Balance:</span>
                <span class="value" style="color: {'#EF4444' if remaining > 0 else '#10B981'};">KES {remaining:,.2f}</span>
            </div>
            <div class="detail-row">
                <span class="label">Status:</span>
                <span class="value">{invoice.get_status_display()}</span>
            </div>
        </div>
        
        <p style="margin-top: 30px; text-align: center; font-size: 18px; color: #059669;">
            <strong>Thank you for your payment!</strong>
        </p>
        
        <div class="footer">
            <p>This is an automated email from Smart Utility Management System.</p>
            <p>Please do not reply to this email.</p>
        </div>
    </div>
</body>
</html>
        """
        
        return self.send_email(subject, tenant_email, text_content, html_content)


class OverdueNotification(EmailNotification):
    """
    Send overdue invoice reminders.
    """
    
    def send_overdue_reminder(self, invoice):
        """
        Send overdue reminder email.
        
        Args:
            invoice: Invoice object
        
        Returns:
            bool: True if sent successfully
        """
        tenant_email = invoice.tenant.user.email
        
        # Calculate days overdue
        from datetime import datetime
        days_overdue = (datetime.now().date() - invoice.due_date).days
        
        # Subject
        subject = f'OVERDUE: Invoice {invoice.invoice_number} - Payment Required'
        
        # Plain text version
        text_content = f"""
URGENT: PAYMENT OVERDUE

Dear {invoice.tenant.user.get_full_name() or invoice.tenant.user.username},

This is a reminder that your invoice {invoice.invoice_number} is now {days_overdue} day(s) overdue.

Invoice Details:
----------------
Invoice Number: {invoice.invoice_number}
Due Date: {invoice.due_date.strftime('%B %d, %Y')}
Amount Due: KES {invoice.total_due:,.2f}
Days Overdue: {days_overdue}

Please make payment immediately to avoid service interruption or late fees.

Payment can be made via:
- M-Pesa
- Bank Transfer
- Cash at the property manager's office

For any queries, please contact your property manager.

Smart Utility Management System
        """
        
        # HTML version
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            line-height: 1.6;
            color: #0F172A;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .header {{
            background: linear-gradient(135deg, #EF4444 0%, #DC2626 100%);
            color: white;
            padding: 30px 20px;
            border-radius: 12px 12px 0 0;
            text-align: center;
        }}
        .header h1 {{
            margin: 0;
            font-size: 24px;
        }}
        .content {{
            background: #FFFFFF;
            padding: 30px 20px;
            border: 1px solid #E2E8F0;
            border-top: none;
        }}
        .urgent-badge {{
            background: #FEE2E2;
            color: #991B1B;
            padding: 10px 20px;
            border-radius: 8px;
            display: inline-block;
            font-weight: 700;
            margin: 20px 0;
            border: 2px solid #EF4444;
        }}
        .invoice-details {{
            background: #FEF3C7;
            padding: 20px;
            border-radius: 8px;
            margin: 20px 0;
            border-left: 4px solid #F59E0B;
        }}
        .detail-row {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
        }}
        .label {{
            color: #78350F;
            font-weight: 600;
        }}
        .value {{
            color: #0F172A;
            font-weight: 600;
        }}
        .button {{
            display: inline-block;
            background: #EF4444;
            color: white;
            padding: 12px 30px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 600;
            margin: 20px 0;
        }}
        .footer {{
            text-align: center;
            color: #64748B;
            font-size: 12px;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #E2E8F0;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>⚠️ PAYMENT OVERDUE</h1>
    </div>
    
    <div class="content">
        <p>Dear <strong>{invoice.tenant.user.get_full_name() or invoice.tenant.user.username}</strong>,</p>
        
        <center>
            <div class="urgent-badge">URGENT: ACTION REQUIRED</div>
        </center>
        
        <p>This is a reminder that your invoice <strong>{invoice.invoice_number}</strong> is now <strong>{days_overdue} day(s) overdue</strong>.</p>
        
        <div class="invoice-details">
            <div class="detail-row">
                <span class="label">Invoice Number:</span>
                <span class="value">{invoice.invoice_number}</span>
            </div>
            <div class="detail-row">
                <span class="label">Due Date:</span>
                <span class="value">{invoice.due_date.strftime('%B %d, %Y')}</span>
            </div>
            <div class="detail-row">
                <span class="label">Amount Due:</span>
                <span class="value" style="color: #EF4444; font-size: 18px;">KES {invoice.total_due:,.2f}</span>
            </div>
            <div class="detail-row">
                <span class="label">Days Overdue:</span>
                <span class="value" style="color: #EF4444;">{days_overdue} days</span>
            </div>
        </div>
        
        <p style="font-weight: 600; color: #991B1B;">Please make payment immediately to avoid service interruption or late fees.</p>
        
        <center>
            <a href="#" class="button">Make Payment Now</a>
        </center>
        
        <p style="margin-top: 30px;">For any queries, please contact your property manager.</p>
        
        <div class="footer">
            <p>This is an automated email from Smart Utility Management System.</p>
            <p>Please do not reply to this email.</p>
        </div>
    </div>
</body>
</html>
        """
        
        return self.send_email(subject, tenant_email, text_content, html_content)