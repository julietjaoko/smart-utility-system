from django.core.management.base import BaseCommand

from core.baselines.services import refresh_meter_baseline
from core.models import Meter


class Command(BaseCommand):
    help = 'Rebuild per-meter consumption baselines from historical readings.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reprocess-readings',
            action='store_true',
            help='Re-run smart anomaly detection on all readings (ordered by date).',
        )

    def handle(self, *args, **options):
        if options['reprocess_readings']:
            from core.models import MeterReading
            total = MeterReading.objects.count()
            for index, reading in enumerate(
                MeterReading.objects.select_related('meter').order_by('reading_date', 'id'),
                start=1,
            ):
                reading.save()
                if index % 100 == 0:
                    self.stdout.write(f'  … {index}/{total} readings')
            self.stdout.write(self.style.SUCCESS(f'Reprocessed {total} reading(s).'))

        count = 0
        for meter in Meter.objects.filter(is_active=True).select_related('unit'):
            if refresh_meter_baseline(meter):
                count += 1
        self.stdout.write(self.style.SUCCESS(f'Updated baselines for {count} meter(s).'))
