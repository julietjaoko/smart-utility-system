"""SMS notification utilities using Africa's Talking API."""

import logging
import requests
import urllib3
from django.conf import settings

# -------------------------------------------------------
# SSL patch for Windows dev environment
# The AT SDK uses bare requests.get/post (no session),
# so we must monkey-patch requests at the module level.
# TODO: Remove this entire block before production deployment
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
_original_get = requests.get
_original_post = requests.post

def _patched_get(*args, **kwargs):
    kwargs['verify'] = False
    return _original_get(*args, **kwargs)

def _patched_post(*args, **kwargs):
    kwargs['verify'] = False
    return _original_post(*args, **kwargs)

requests.get = _patched_get
requests.post = _patched_post
# -------------------------------------------------------

import africastalking  # import AFTER the patch

logger = logging.getLogger(__name__)


class AfricasTalkingSMS:
    """
    SMS notification service using Africa's Talking API.
    """
    
    def __init__(self):
        self.username = settings.AFRICASTALKING_USERNAME
        self.api_key = settings.AFRICASTALKING_API_KEY
        self.sender_id = settings.AFRICASTALKING_SENDER_ID
        
        # Initialize SDK
        africastalking.initialize(self.username, self.api_key)
        self.sms = africastalking.SMS

    def normalize_phone_number(self, phone_number):
        """Convert local Kenyan phone numbers to E.164 format."""
        phone_number = str(phone_number or '').strip().replace(' ', '').replace('-', '')

        if phone_number.startswith('0'):
            return '+254' + phone_number[1:]
        if phone_number.startswith('254'):
            return '+' + phone_number
        if phone_number.startswith('+'):
            return phone_number
        return '+254' + phone_number

    def _recipient_result(self, response):
        recipients = response.get('SMSMessageData', {}).get('Recipients', [])
        return recipients[0] if recipients else {}
        
    def send_sms(self, phone_number, message):
        """
        Send SMS to a phone number.
        
        Args:
            phone_number (str): Phone number in format +254XXXXXXXXX or 254XXXXXXXXX
            message (str): SMS message (max 160 characters for single SMS)
        
        Returns:
            dict: Response with success status and details
        """
        try:
            phone_number = self.normalize_phone_number(phone_number)
                
            # Send SMS (Sender ID removed for sandbox compatibility!)
            response = self.sms.send(
                message=message,
                recipients=[phone_number]
            )

            recipient = self._recipient_result(response)
            provider_status = recipient.get('status')
            provider_message = response.get('SMSMessageData', {}).get('Message', '')
            success = provider_status == 'Success'

            if success:
                logger.info(
                    "Africa's Talking SMS accepted for %s: %s",
                    phone_number,
                    provider_message,
                )
            else:
                logger.warning(
                    "Africa's Talking SMS failed for %s: status=%s response=%s",
                    phone_number,
                    provider_status or 'MissingRecipientStatus',
                    response,
                )

            return {
                'success': success,
                'response': response,
                'recipient': recipient,
                'status': provider_status,
                'status_code': recipient.get('statusCode'),
                'error': None if success else provider_status or provider_message or 'Unknown SMS failure',
                'message': 'SMS sent successfully' if success else provider_message or 'SMS failed',
            }
            
        except Exception as e:
            logger.exception("Africa's Talking SMS error for %s", phone_number)
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to send SMS: {str(e)}'
            }


class InvoiceSMS:
    """
    SMS notifications for invoices.
    """
    
    def __init__(self):
        self.sms_service = AfricasTalkingSMS()
    
    def send_invoice_notification(self, invoice):
        """
        Send SMS notification when invoice is generated.
        
        Args:
            invoice: Invoice object
        
        Returns:
            dict: Response with success status
        """
        # Get tenant phone number
        phone_number = invoice.tenant.phone_number
        
        if not phone_number:
            return {
                'success': False,
                'error': 'No phone number found for tenant'
            }
        
        # Construct message (keep under 160 characters for single SMS)
        message = (
            f"SUMS: New invoice {invoice.invoice_number} for {invoice.billing_period}. "
            f"Amount: KES {invoice.total_due:,.2f}. "
            f"Due: {invoice.due_date.strftime('%d %b %Y')}. "
            f"Pay via M-Pesa or visit your account."
        )
        
        return self.sms_service.send_sms(phone_number, message)
    
    def send_overdue_reminder(self, invoice):
        """
        Send overdue reminder SMS.
        
        Args:
            invoice: Invoice object
        
        Returns:
            dict: Response with success status
        """
        phone_number = invoice.tenant.phone_number
        
        if not phone_number:
            return {
                'success': False,
                'error': 'No phone number found for tenant'
            }
        
        # Calculate days overdue
        from datetime import datetime
        days_overdue = (datetime.now().date() - invoice.due_date).days
        
        message = (
            f"URGENT: Invoice {invoice.invoice_number} is {days_overdue} days overdue. "
            f"Amount: KES {invoice.total_due:,.2f}. "
            f"Pay now to avoid service interruption."
        )
        
        return self.sms_service.send_sms(phone_number, message)


class PaymentSMS:
    """
    SMS notifications for payments.
    """
    
    def __init__(self):
        self.sms_service = AfricasTalkingSMS()
    
    def send_payment_confirmation(self, payment):
        """
        Send payment confirmation SMS.
        
        Args:
            payment: Payment object
        
        Returns:
            dict: Response with success status
        """
        phone_number = payment.invoice.tenant.phone_number
        
        if not phone_number:
            return {
                'success': False,
                'error': 'No phone number found for tenant'
            }
        
        # Calculate remaining balance
        from django.db.models import Sum
        from decimal import Decimal
        
        total_paid = payment.invoice.payments.aggregate(
            total=Sum('amount_paid')
        )['total'] or Decimal('0.00')
        
        remaining = payment.invoice.total_due - total_paid
        
        message = (
            f"SUMS: Payment received! KES {payment.amount_paid:,.2f} for invoice {payment.invoice.invoice_number}. "
        )
        
        if remaining <= 0:
            message += "Invoice PAID IN FULL. Thank you!"
        else:
            message += f"Balance: KES {remaining:,.2f}."
        
        return self.sms_service.send_sms(phone_number, message)


class TokenSMS:
    """
    SMS notifications for electricity tokens.
    """
    
    def __init__(self):
        self.sms_service = AfricasTalkingSMS()
    
    def send_token_notification(self, token_log):
        """
        Send electricity token SMS.
        
        Args:
            token_log: ElectricityToken object
        
        Returns:
            dict: Response with success status
        """
        phone_number = token_log.tenant.phone_number
        
        if not phone_number:
            return {
                'success': False,
                'error': 'No phone number found for tenant'
            }
        
        message = (
            f"SUMS Electricity Token for {token_log.tenant.unit.unit_number}:\n"
            f"Token: {token_log.token_number}\n"
            f"Units: {token_log.units} kWh\n"
            f"Amount: KES {token_log.amount:,.2f}\n"
            f"Valid until: {token_log.expiry_date.strftime('%d %b %Y') if token_log.expiry_date else 'No expiry'}"
        )
        
        return self.sms_service.send_sms(phone_number, message)
