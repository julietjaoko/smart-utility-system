"""
Management command to send overdue invoice reminders.
Run this daily via cron job or task scheduler.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Invoice
from core.email_utils import OverdueNotification


class Command(BaseCommand):
    help = 'Send overdue invoice reminder emails to tenants'
    
    def handle(self, *args, **options):
        """
        Find all overdue invoices and send reminder emails.
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
        
        for invoice in overdue_invoices:
            try:
                success = email_notifier.send_overdue_reminder(invoice)
                if success:
                    sent_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'✓ Sent overdue reminder for {invoice.invoice_number} to {invoice.tenant.user.email}'
                        )
                    )
                else:
                    failed_count += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'✗ Failed to send reminder for {invoice.invoice_number}'
                        )
                    )
            except Exception as e:
                failed_count += 1
                self.stdout.write(
                    self.style.ERROR(
                        f'✗ Error sending reminder for {invoice.invoice_number}: {str(e)}'
                    )
                )
        
        # Summary
        self.stdout.write(
            self.style.SUCCESS(
                f'\n{"="*50}\n'
                f'Overdue Reminders Summary:\n'
                f'Total Overdue Invoices: {overdue_invoices.count()}\n'
                f'Emails Sent: {sent_count}\n'
                f'Failed: {failed_count}\n'
                f'{"="*50}'
            )
        )