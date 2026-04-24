import csv
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    AccountBalance,
    FixedCharge,
    Meter,
    MeterReading,
    PropertyManager,
    RateConfig,
    Tenant,
    User,
    Unit,
)


class Command(BaseCommand):
    help = "Import Twiga-style apartment billing CSV data and seed readings/configs."

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str, help="Path to the CSV file")
        parser.add_argument(
            "manager_username",
            type=str,
            help="Username of the property manager who owns these units",
        )

    def handle(self, *args, **kwargs):
        file_path = kwargs["file_path"]
        manager_username = kwargs["manager_username"]

        try:
            manager_user = User.objects.get(username=manager_username)
        except User.DoesNotExist as exc:
            raise CommandError(f"User '{manager_username}' does not exist.") from exc

        try:
            manager = PropertyManager.objects.get(user=manager_user)
        except PropertyManager.DoesNotExist as exc:
            raise CommandError(
                f"User '{manager_username}' does not have a PropertyManager profile."
            ) from exc

        with open(file_path, mode="r", newline="", encoding="utf-8-sig") as file:
            reader = list(csv.reader(file))

        reading_date = self._extract_reading_date(reader)
        rate_per_unit = self._extract_rate(reader)
        header_index = self._find_header_index(reader)
        data_rows = self._extract_data_rows(reader, header_index)

        if not data_rows:
            raise CommandError("No unit rows were found in the CSV.")

        self.stdout.write(
            f"Importing {len(data_rows)} unit rows for {manager.estate_name} on {reading_date:%Y-%m-%d}"
        )

        imported_units = 0
        updated_balances = 0
        skipped_balances = []
        garbage_amount = None

        with transaction.atomic():
            if rate_per_unit is not None:
                RateConfig.objects.filter(
                    manager=manager, utility_type="WATER", is_active=True
                ).exclude(effective_from=reading_date).update(is_active=False)

                RateConfig.objects.update_or_create(
                    manager=manager,
                    utility_type="WATER",
                    effective_from=reading_date,
                    defaults={
                        "rate_per_unit": rate_per_unit,
                        "is_active": True,
                    },
                )

            for row in data_rows:
                unit_no = row[0].strip()
                last_reading = self._to_decimal(row[1])
                current_reading = self._to_decimal(row[2])
                garbage = self._to_decimal(row[5])
                outstanding = self._to_decimal(row[6])

                unit, _ = Unit.objects.get_or_create(
                    unit_number=unit_no,
                    estate_name=manager.estate_name,
                    manager=manager,
                    defaults={"has_water_meter": True},
                )

                if not unit.has_water_meter:
                    unit.has_water_meter = True
                    unit.save(update_fields=["has_water_meter"])

                meter, _ = Meter.objects.get_or_create(
                    unit=unit,
                    meter_type="WATER",
                    defaults={
                        "meter_number": f"WTR-{unit_no}",
                        "is_active": True,
                    },
                )

                if not meter.is_active:
                    meter.is_active = True
                    meter.save(update_fields=["is_active"])

                current_dt = datetime.combine(reading_date, time(12, 0))
                previous_dt = current_dt - timedelta(minutes=1)

                if last_reading is not None:
                    MeterReading.objects.update_or_create(
                        meter=meter,
                        reading_date=previous_dt,
                        defaults={
                            "reading_value": last_reading,
                            "recorded_by": manager_user,
                            "verification_status": "VERIFIED",
                            "notes": "Seeded previous reading from Twiga import.",
                        },
                    )

                if current_reading is not None:
                    MeterReading.objects.update_or_create(
                        meter=meter,
                        reading_date=current_dt,
                        defaults={
                            "reading_value": current_reading,
                            "recorded_by": manager_user,
                            "verification_status": "VERIFIED",
                            "notes": "Seeded current reading from Twiga import.",
                        },
                    )

                if garbage is not None and garbage_amount is None:
                    garbage_amount = garbage

                if outstanding not in (None, Decimal("0")):
                    tenant = Tenant.objects.filter(unit=unit, is_active=True).first()
                    if tenant:
                        balance, _ = AccountBalance.objects.get_or_create(tenant=tenant)
                        balance.current_balance = outstanding
                        balance.save(update_fields=["current_balance", "last_updated"])
                        updated_balances += 1
                    else:
                        skipped_balances.append(unit_no)

                imported_units += 1
                self.stdout.write(self.style.SUCCESS(f"Imported unit {unit_no}"))

            if garbage_amount is not None:
                FixedCharge.objects.filter(
                    manager=manager, charge_name="Garbage", is_active=True
                ).exclude(effective_from=reading_date).update(is_active=False)

                FixedCharge.objects.update_or_create(
                    manager=manager,
                    charge_name="Garbage",
                    effective_from=reading_date,
                    defaults={
                        "amount": garbage_amount,
                        "is_active": True,
                    },
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {imported_units} units. "
                f"Water rate: {rate_per_unit if rate_per_unit is not None else 'not set'}. "
                f"Garbage charge: {garbage_amount if garbage_amount is not None else 'not set'}."
            )
        )

        if updated_balances:
            self.stdout.write(
                self.style.SUCCESS(f"Updated outstanding balances for {updated_balances} tenants.")
            )

        if skipped_balances:
            self.stdout.write(
                self.style.WARNING(
                    "Skipped outstanding balances for units without active tenants: "
                    + ", ".join(skipped_balances)
                )
            )

    def _extract_reading_date(self, rows):
        for row in rows:
            for index, value in enumerate(row):
                if value.strip().lower() == "meter reading date:" and index + 1 < len(row):
                    try:
                        return datetime.strptime(row[index + 1].strip(), "%d/%m/%Y").date()
                    except ValueError as exc:
                        raise CommandError(
                            f"Invalid meter reading date '{row[index + 1]}'. Expected DD/MM/YYYY."
                        ) from exc
        raise CommandError("Could not find 'Meter reading date:' in the CSV.")

    def _extract_rate(self, rows):
        for row in rows:
            for index, value in enumerate(row):
                if value.strip().lower() == "rate per unit (ksh):" and index + 1 < len(row):
                    return self._to_decimal(row[index + 1])
        return None

    def _find_header_index(self, rows):
        for index, row in enumerate(rows):
            if row and row[0].strip().lower() == "hse no.":
                return index
        raise CommandError("Could not find the unit header row starting with 'Hse no.'.")

    def _extract_data_rows(self, rows, header_index):
        data_rows = []
        for row in rows[header_index + 1 :]:
            if not any(cell.strip() for cell in row):
                continue
            if not row[0].strip():
                continue
            if row[0].strip().lower() == "hse no.":
                continue
            data_rows.append(row)
        return data_rows

    def _to_decimal(self, value):
        cleaned = (value or "").strip().replace(",", "")
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise CommandError(f"Could not parse decimal value '{value}'.") from exc
