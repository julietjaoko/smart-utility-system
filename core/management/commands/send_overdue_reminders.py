"""
Management command to send overdue invoice reminders.
Run this daily via cron job or task scheduler.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Invoice
from core.email_utils import OverdueNotification
from core.sms_utils import InvoiceSMS  # Added new import

class Command(BaseCommand):
    help = 'Send overdue invoice reminder emails and SMS to tenants'
    
    def handle(self, *args, **options):
        """
        Find all overdue invoices and send reminder emails and SMS.
        """
        # Get all overdue invoices
        today = timezone.now().date()
        overdue_invoices = Invoice.objects.filter(
            due_date__lt=today,
            status__in=['UNPAID', 'PARTIALLY_PAID']
        ).select_related('tenant__user', 'unit')
        
        sent_count = 0
        failed_count = 0
        
        email_notifier = OverdueNotification()
        sms_notifier = InvoiceSMS()
        
        for invoice in overdue_invoices:
            try:
                email_success = False
                sms_result = {'success': False}

                # 1. Attempt to send Email safely
                try:
                    email_success = email_notifier.send_overdue_reminder(invoice)
                except Exception as email_err:
                    self.stdout.write(self.style.WARNING(f'  [!] Email error for {invoice.invoice_number}: {str(email_err)}'))

                # 2. Attempt to send SMS safely
                try:
                    sms_result = sms_notifier.send_overdue_reminder(invoice)
                except Exception as sms_err:
                    self.stdout.write(self.style.WARNING(f'  [!] SMS error for {invoice.invoice_number}: {str(sms_err)}'))
                
                # 3. Evaluate combined success
                sms_success = sms_result.get('success', False)
                
                if email_success or sms_success:
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'✓ Sent reminder for {invoice.invoice_number} '
                            f'(Email: {bool(email_success)}, SMS: {bool(sms_success)})'
                        )
                    )
                else:
                    failed_count += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'✗ Failed to send both email and SMS for {invoice.invoice_number}'
                        )
                    )
                    
            except Exception as e:
                failed_count += 1
                self.stdout.write(
                    self.style.ERROR(f'✗ Unexpected error processing {invoice.invoice_number}: {str(e)}')
                )
        
        # Summary
        self.stdout.write(
            self.style.SUCCESS(
                f'\n{"="*50}\n'
                f'Overdue Reminders Summary:\n'
                f'Total Overdue Invoices: {overdue_invoices.count()}\n'
                f'Successful Reminders (Email OR SMS): {sent_count}\n'
                f'Failed Completely: {failed_count}\n'
                f'{"="*50}'
            )
        )