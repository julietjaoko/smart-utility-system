# Smart Utility System (SUMS)

A comprehensive Django-based utility management platform designed for property managers and tenants to manage water and electricity consumption, billing, and payments in real-time.

**Live Demo:** [smartutilitysystem.vercel.app](https://smartutilitysystem.vercel.app)

## What the Project Does

Smart Utility System is a complete utility management solution that helps property managers efficiently track meter readings, generate invoices, and collect payments from tenants. Tenants can view their consumption, track payments, and receive SMS/email notifications.

### Key Features

- **Meter Management**: Track water and electricity consumption with automatic anomaly detection
- **Smart Billing**: Automated invoice generation with water, electricity, and fixed charges
- **Payment Processing**: Integration with M-Pesa for seamless mobile money payments
- **Tenant Dashboard**: Real-time access to consumption data and payment history
- **SMS & Email Notifications**: Automated alerts for invoices and payment confirmations
- **Reporting**: Reports Center with financial, arrears, consumption, and anomaly reports (Excel export)
- **Audit logging**: Immutable activity log for logins, billing, payments, and meter readings
- **Photo Verification**: Attach photos to meter readings for verification
- **Role-Based Access**: Separate dashboards for property managers and tenants

## Why This Project is Useful

Managing utilities across multiple residential units is complex and error-prone. This system:

- **Eliminates manual reading & calculation errors** through automated consumption calculations
- **Speeds up billing cycles** with one-click invoice generation
- **Reduces payment delays** with integrated M-Pesa payment processing
- **Improves transparency** so tenants can verify their consumption anytime
- **Detects anomalies** automatically to catch meter issues or unusual patterns
- **Saves administrative time** with automated email and SMS notifications

## Getting Started

### Prerequisites

- Python 3.8+
- MySQL 5.7+
- pip (Python package manager)

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/julietjaoko/smart-utility-system.git
   cd smart-utility-system
   ```

2. **Create a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   Create a `.env` file in the root directory:
   ```
   DJANGO_SECRET_KEY=your-secret-key-here
   DEBUG=False
   DB_HOST=your-database-host
   DB_NAME=utility_management
   DB_USER=your-db-user
   DB_PASSWORD=your-db-password
   DB_PORT=3306
   
   MPESA_CONSUMER_KEY=your-mpesa-key
   MPESA_CONSUMER_SECRET=your-mpesa-secret
   MPESA_SHORTCODE=174379
   MPESA_PASSKEY=your-mpesa-passkey
   MPESA_CALLBACK_URL=https://your-domain/api/mpesa-callback/
   
   AFRICASTALKING_USERNAME=your-africa-talking-username
   AFRICASTALKING_API_KEY=your-api-key
   AFRICASTALKING_SENDER_ID=
   ```

5. **Set up the database:**
   ```bash
   python manage.py migrate
   ```

6. **Create a superuser account:**
   ```bash
   python manage.py createsuperuser
   ```

7. **Collect static files:**
   ```bash
   python manage.py collectstatic --noinput
   ```

8. **Run the development server:**
   ```bash
   python manage.py runserver
   ```
   Visit `http://localhost:8000` in your browser.

### Build & Deployment

For production deployment (e.g., Vercel):

```bash
./build.sh
```

This script will:
- Install all dependencies
- Collect static files
- Run database migrations

## Usage Examples

### Creating Units and Tenants

1. Log in as a property manager
2. Navigate to the admin panel and create a **Unit** with meter preferences
3. Add tenants to units with move-in dates
4. Configure utility rates and fixed charges

### Recording Meter Readings

1. Go to **Meter Readings** section
2. Enter the current meter value and optional photo
3. The system automatically:
   - Calculates consumption from previous reading
   - Detects anomalies (±30% threshold from average)
   - Flags unusual patterns for verification

### Generating Invoices

1. Navigate to **Invoice Generation**
2. Select a billing period and units
3. System automatically calculates:
   - Water charges (consumption × rate)
   - Electricity charges (if applicable)
   - Fixed charges (garbage, security, etc.)
4. Export as PDF or send directly to tenants

### Payment Processing

1. Tenants can pay via **M-Pesa** (automatic confirmation)
2. Property managers can manually record **Cash** or **Bank Transfer** payments
3. Invoice status updates automatically:
   - UNPAID → PARTIALLY_PAID → PAID

### Tenant Dashboard

Tenants can:
- View current and past invoices
- Check consumption trends
- Track payment history
- Manage notification preferences
- Log electricity tokens (for prepaid plans)

## Technology Stack

- **Backend**: Django 6.0.3 (Python)
- **Database**: MySQL 5.7+
- **Frontend**: HTML/CSS/JavaScript
- **Payments**: M-Pesa STK Push API
- **SMS**: Africa's Talking API
- **Email**: Django's email backend
- **Reporting**: ReportLab (PDF), OpenPyXL (Excel)
- **Hosting**: Vercel (serverless)

## Project Structure

```
smart-utility-system/
├── api/                      # Vercel serverless entry point
│   └── index.py
├── core/                     # Main Django app
│   ├── models.py            # Database models (User, Unit, Tenant, etc.)
│   ├── views.py             # View logic
│   ├── urls.py              # URL routing
│   ├── forms.py             # Django forms
│   ├── admin.py             # Admin panel configuration
│   ├── email_utils.py       # Email sending utilities
│   ├── sms_utils.py         # SMS notifications via Africa's Talking
│   ├── mpesa.py             # M-Pesa payment integration
│   ├── pdf_generator.py     # PDF invoice generation
│   ├── excel_exporter.py    # Excel export functionality
│   ├── decorators.py        # Custom decorators
│   └── templates/           # HTML templates
├── utility_system/          # Django project settings
│   ├── settings.py          # Configuration
│   ├── urls.py              # Project URLs
│   ├── wsgi.py              # WSGI configuration
│   └── asgi.py              # ASGI configuration
├── manage.py                # Django management script
├── requirements.txt         # Python dependencies
├── build.sh                 # Production build script
├── vercel.json              # Vercel deployment config
└── ca.pem                   # SSL certificate for Aiven MySQL

```

## Key Models

- **User**: Custom user with role-based access (Property Manager / Tenant)
- **PropertyManager**: Manager profile with estate details
- **Unit**: Residential unit with meter configuration
- **Tenant**: Tenant profile linked to a unit
- **Meter**: Water or electricity meter for a unit
- **MeterReading**: Monthly reading with automatic consumption calculation
- **Invoice**: Monthly bill with water, electricity, and fixed charges
- **Payment**: Payment record with M-Pesa support
- **RateConfig**: Configurable utility rates per property manager
- **FixedCharge**: Monthly fixed charges (garbage, security, etc.)

## API Endpoints

The system exposes several endpoints for integration:

- `/api/mpesa-callback/` - M-Pesa payment confirmation webhook
- `/admin/` - Django admin panel
- Various view endpoints for invoice generation, payment recording, and reporting

## Configuration

### Environment Variables

| Variable | Description |
|----------|-------------|
| `DJANGO_SECRET_KEY` | Django secret key for security |
| `DEBUG` | Set to False for production |
| `DB_HOST` | Database hostname |
| `DB_NAME` | Database name |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `MPESA_CONSUMER_KEY` | M-Pesa API consumer key |
| `MPESA_CONSUMER_SECRET` | M-Pesa API consumer secret |
| `MPESA_PASSKEY` | M-Pesa passkey for STK Push |
| `MPESA_CALLBACK_URL` | URL for M-Pesa payment callbacks |
| `AFRICASTALKING_USERNAME` | Africa's Talking username |
| `AFRICASTALKING_API_KEY` | Africa's Talking API key |
| `AFRICASTALKING_SENDER_ID` | SMS sender ID |

### Database Setup

The project supports both local MySQL and Aiven Cloud MySQL:
- **Local**: Uses `ssl: {'cert_reqs': 0}` for development
- **Production**: Uses CA certificate (`ca.pem`) for SSL verification

## Deployment

### Vercel Deployment

1. Connect your GitHub repository to Vercel
2. Set environment variables in Vercel dashboard
3. Deploy using the included `build.sh` script

```bash
vercel --prod
```

## Where to Get Help

### Documentation
- [Django Documentation](https://docs.djangoproject.com/)
- [M-Pesa STK Push API](https://developer.safaricom.co.ke/apis/mpesa-stk-push)
- [Africa's Talking SMS API](https://africastalking.com/sms/api)

### Support Resources
- **Issues**: Report bugs or request features on [GitHub Issues](https://github.com/julietjaoko/smart-utility-system/issues)
- **Email**: Contact the maintainer for technical support

### Common Issues

**Database Connection Error**
- Verify `DB_HOST`, `DB_USER`, and `DB_PASSWORD` in `.env`
- For Aiven cloud, ensure `ca.pem` is in the project root
- Check firewall rules allow MySQL connections

**M-Pesa Callbacks Not Working**
- Verify `MPESA_CALLBACK_URL` is publicly accessible
- Check M-Pesa API credentials in Safaricom dashboard
- Review Django logs for callback errors

**SMS Not Sending**
- Confirm `AFRICASTALKING_API_KEY` is valid
- Check sender ID is registered with Africa's Talking
- Verify recipient phone numbers are in E.164 format (+254...)

## Contributing

Contributions are welcome! Please follow these guidelines:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for detailed contribution guidelines.

## License

This project is licensed under the MIT License. See the LICENSE file for details.

## Maintainers

**Juliet Jaoko** - [@julietjaoko](https://github.com/julietjaoko)

---

**Built with ❤️ for efficient utility management in East Africa**
