# Smart Utility System (SUMS)

Smart Utility System is a Django utility-management platform for rental estates. It helps property managers record water and electricity readings, detect abnormal consumption, generate invoices, track payments, export reports, and give tenants a transparent self-service portal.

Live demo: [smartutilitysystem.vercel.app](https://smartutilitysystem.vercel.app)

## Highlights

- Role-based dashboards for system admins, property managers, and tenants.
- Unit and tenant management with active/vacant unit tracking.
- Water and electricity meter readings with automatic consumption calculation.
- Smart anomaly detection using per-meter baselines and manager-defined hard limits.
- Tenant-facing high consumption alerts controlled by tenant preferences.
- Billing wizard with anomaly review gate before invoices are generated.
- Invoice PDFs, payment receipts, and Excel exports for invoices, payments, consumption, reports, and audit logs.
- M-Pesa STK Push support and manual cash/bank payment recording.
- Email and SMS notification preferences.
- Maintenance request workflow for tenants and managers.
- Immutable audit logs for authentication, billing, payments, readings, tenants, units, and maintenance.

## Demo Flow

For a presentation, this sequence shows the strongest parts of the system:

1. Log in as a property manager and open the dashboard.
2. Show smart insights, open readings that require review, then verify or reject an anomaly.
3. Enter a new meter reading and point out automatic consumption and anomaly detection.
4. Open the billing wizard and show that pending anomalies block invoice generation.
5. Export meter readings or reports to Excel.
6. Log in as a tenant and show invoices, consumption history, high consumption alerts, and preferences.
7. Toggle High Consumption Alerts in tenant preferences to show that the tenant controls those warnings.

## How Anomaly Detection Works

The anomaly check runs automatically whenever a meter reading is saved.

Main files:

- `core/models.py`: `MeterReading.save()` calculates consumption and calls the anomaly service.
- `core/baselines/services.py`: contains baseline calculation and `detect_reading_anomalies()`.
- `core/views/meter_readings.py`: displays readings, pending anomalies, and verify/reject actions.
- `core/views/billing.py`: blocks invoice generation while pending anomalies exist.
- `core/tenant_alerts.py`: converts high-consumption anomalies into tenant-facing alerts.

The system checks for:

- Meter rollback: the new meter value is lower than the previous value.
- Zero or negative consumption.
- Consumption above the manager's hard limit for water or electricity.
- Consumption above or below the unit meter's learned baseline.
- Sudden spike/drop fallback when there is not enough baseline history.

Smart baselines use up to 6 recent non-rejected readings and require at least 3 readings before the statistical baseline becomes active. New anomalous readings are marked as pending review.

## Getting Started

### Requirements

- Python 3.12 recommended
- MySQL 5.7+ or compatible cloud MySQL
- pip

### Setup

```bash
git clone https://github.com/julietjaoko/smart-utility-system.git
cd smart-utility-system
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
DJANGO_SECRET_KEY=your-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000

DB_HOST=localhost
DB_NAME=utility_management
DB_USER=root
DB_PASSWORD=
DB_PORT=3306

MPESA_CONSUMER_KEY=your-mpesa-key
MPESA_CONSUMER_SECRET=your-mpesa-secret
MPESA_SHORTCODE=174379
MPESA_PASSKEY=your-mpesa-passkey
MPESA_CALLBACK_URL=https://your-domain/mpesa/callback/

AFRICASTALKING_USERNAME=your-username
AFRICASTALKING_API_KEY=your-api-key
AFRICASTALKING_SENDER_ID=
```

Run migrations and start the app:

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000`.

## Optional Test Data

The project includes a seed command that creates an additional demo manager, units, tenants, meters, and readings:

```bash
python manage.py populate_test_data
```

Generated demo users from that command:

- Manager: `manager2`
- Tenants: `tenant_alex1`, `tenant_alex2`
- Password: `password123`

Use demo credentials only in local development.

## Useful Commands

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test core
python manage.py refresh_usage_baselines
python manage.py refresh_usage_baselines --reprocess-readings
```

## Project Structure

```text
smart-utility-system/
├── api/                         # Vercel serverless entry point
├── core/                        # Main Django app
│   ├── baselines/               # Meter baseline and anomaly services
│   ├── insights/                # Manager dashboard insight services
│   ├── management/commands/     # Seed and maintenance commands
│   ├── templates/core/          # Django templates
│   ├── views/                   # Split view modules
│   ├── models.py                # Core database models
│   ├── forms.py                 # Django forms
│   ├── excel_exporter.py        # Excel workbook generation
│   ├── pdf_generator.py         # Invoice and receipt PDF generation
│   ├── tenant_alerts.py         # Tenant high-consumption alert helpers
│   ├── sms_utils.py             # SMS helpers
│   └── email_utils.py           # Email helpers
├── media/                       # Uploaded media
├── utility_system/              # Django settings and root URLs
├── manage.py
├── requirements.txt
├── build.sh
├── vercel.json
└── ca.pem                       # CA certificate for cloud MySQL
```

## Key Models

- `User`: custom user with role-based access.
- `PropertyManager`: estate owner profile and anomaly thresholds.
- `Unit`: rentable unit with meter configuration.
- `Tenant`: tenant profile linked to a unit.
- `Meter`: water or electricity meter for a unit.
- `MeterReading`: reading value, consumption, anomaly status, verification status, and optional photo.
- `UnitMeterBaseline`: rolling per-meter consumption profile.
- `Invoice`: monthly bill with utility and fixed charges.
- `Payment`: manual and M-Pesa payment records.
- `RateConfig`: water/electricity rates.
- `FixedCharge`: recurring estate charges.
- `TenantPreferences`: tenant notification and feature toggles.
- `MaintenanceRequest`: tenant maintenance tickets.
- `AuditLog`: immutable system activity history.

## Exports and Reports

Managers can export:

- Invoices
- Payments
- Meter readings/consumption
- Financial reports
- Arrears reports
- Consumption summaries
- Anomaly reports
- Activity logs

Exports are generated with OpenPyXL and returned as `.xlsx` downloads.

## Deployment

The project includes Vercel configuration:

```bash
./build.sh
vercel --prod
```

Before deploying:

- Set all required environment variables in Vercel.
- Configure `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`.
- Confirm the production database is reachable.
- Keep `DEBUG=False`.
- Ensure `ca.pem` is present when using cloud MySQL with SSL.

## Troubleshooting

### Database connection fails

- Check `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, and `DB_PORT`.
- Confirm MySQL is running or the cloud database is reachable.
- Confirm `ca.pem` is present for cloud MySQL.

### M-Pesa callbacks fail

- Confirm `MPESA_CALLBACK_URL` is public and points to the correct route.
- Check Safaricom credentials and shortcode settings.
- Review Django logs for callback payload errors.

### SMS messages do not send

- Confirm Africa's Talking credentials.
- Use valid phone numbers, preferably in `+254...` format.
- Check whether tenant SMS notifications are enabled.

### Static files warning in development

If Django warns that `staticfiles/` does not exist, run:

```bash
python manage.py collectstatic --noinput
```

## Maintainer

Juliet Jaoko - [@julietjaoko](https://github.com/julietjaoko)

Built for practical utility management in East Africa.
