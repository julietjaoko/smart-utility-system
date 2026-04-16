from django.db import models
from django.contrib.auth.models import AbstractUser
from django.db.models import Sum

# Custom User Model
class User(AbstractUser):
    ROLE_CHOICES = [
        ('PROPERTY_MANAGER', 'Property Manager'),
        ('TENANT', 'Tenant'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    phone_number = models.CharField(max_length=15, blank=True)
    
    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


# Property Manager
class PropertyManager(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    estate_name = models.CharField(max_length=100)
    
    def __str__(self):
        return f"{self.user.get_full_name()} - {self.estate_name}"


# Unit
class Unit(models.Model):
    unit_number = models.CharField(max_length=20)
    estate_name = models.CharField(max_length=100)
    manager = models.ForeignKey(PropertyManager, on_delete=models.CASCADE)
    has_water_meter = models.BooleanField(default=True)
    has_electricity_meter = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['unit_number', 'estate_name']
    
    def __str__(self):
        return f"{self.unit_number} - {self.estate_name}"


# Tenant
class Tenant(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True)
    move_in_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, help_text="Uncheck to deactivate tenant instead of deleting")
    
    def __str__(self):
        status = "Active" if self.is_active else "Inactive"
        return f"{self.user.get_full_name()} - Unit {self.unit.unit_number if self.unit else 'N/A'} ({status})"

# Meter
class Meter(models.Model):
    METER_TYPE_CHOICES = [
        ('WATER', 'Water'),
        ('ELECTRICITY', 'Electricity'),
    ]
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE)
    meter_type = models.CharField(max_length=15, choices=METER_TYPE_CHOICES)
    meter_number = models.CharField(max_length=50)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ['unit', 'meter_type']
    
    def __str__(self):
        return f"{self.get_meter_type_display()} - {self.unit.unit_number}"
    
from django.utils import timezone
from decimal import Decimal

# Meter Reading Model
class MeterReading(models.Model):
    """
    Stores individual meter readings with automatic consumption calculation.
    Supports photo verification and anomaly detection.
    """
    VERIFICATION_CHOICES = [
        ('PENDING', 'Pending Review'),
        ('VERIFIED', 'Verified Valid'),
        ('REJECTED', 'Rejected - Needs Recount')
    ]
    meter = models.ForeignKey(Meter, on_delete=models.CASCADE, related_name='readings')
    reading_value = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        help_text="Current meter reading value"
    )
    reading_date = models.DateTimeField(
        default=timezone.now,
        help_text="Date and time when reading was taken"
    )
    photo = models.ImageField(
        upload_to='meter_readings/',
        null=True,
        blank=True,
        help_text="Optional photo of meter for verification"
    )
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        help_text="Property manager who recorded this reading"
    )
    consumption = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Calculated consumption since last reading"
    )
    is_anomaly = models.BooleanField(
        default=False,
        help_text="Flagged if consumption pattern is unusual"
    )
    verification_status = models.CharField(
        max_length=10,
        choices=VERIFICATION_CHOICES,
        default='VERIFIED',
        help_text="Verification status for anomaly triage"
    )
    notes = models.TextField(
        blank=True,
        help_text="Optional notes about this reading"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-reading_date']  # Most recent first
    
    def __str__(self):
        return f"{self.meter.unit.unit_number} - {self.meter.get_meter_type_display()} - {self.reading_value}"
    
    def save(self, *args, **kwargs):
        """
        Override save to automatically calculate consumption and detect anomalies.
        This runs every time a reading is saved.
        """
        # Get the previous reading for this meter
        previous_reading = MeterReading.objects.filter(
            meter=self.meter
        ).exclude(
            pk=self.pk if self.pk else None # Exclude current reading if updating
        ).order_by('-reading_date').first()
        
        if previous_reading:
            # Calculate consumption: current - previous
            self.consumption = self.reading_value - previous_reading.reading_value
            
            # Anomaly Detection: Check for unusual patterns
            # Get average consumption from last 3 readings
            recent_readings = MeterReading.objects.filter(
                meter=self.meter
            ).exclude(pk=self.pk if self.pk else None).order_by('-reading_date')[:3]
            
            if recent_readings.count() >= 2:
                avg_consumption = sum(r.consumption for r in recent_readings) / recent_readings.count()
                
                # Flag as anomaly if:
                # 1. Consumption is zero
                # 2. Consumption is negative (meter reading went backwards)
                # 3. Consumption is more than 3x average
                if (self.consumption == 0 or 
                    self.consumption < 0 or 
                    self.consumption > (avg_consumption * 3)):
                    self.is_anomaly = True
                    # Auto-set to PENDING only upon initial creation if it's an anomaly
                    if self._state.adding:
                        self.verification_status = 'PENDING'
        
        super().save(*args, **kwargs)

# Rate Configuration Model
class RateConfig(models.Model):
    """
    Stores utility rate configurations for billing calculations.
    Allows property managers to set different rates per utility type.
    """
    manager = models.ForeignKey(
        PropertyManager,
        on_delete=models.CASCADE,
        help_text="Property manager who owns this rate configuration"
    )
    utility_type = models.CharField(
        max_length=15,
        choices=[('WATER', 'Water'), ('ELECTRICITY', 'Electricity')],
        help_text="Type of utility this rate applies to"
    )
    rate_per_unit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Cost per unit (e.g., KES 50 per m³ of water)"
    )
    effective_from = models.DateField(
        help_text="Date from which this rate becomes active"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this rate is currently in use"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-effective_from']
    
    def __str__(self):
        return f"{self.get_utility_type_display()} - KES {self.rate_per_unit}/unit"


# Fixed Charge Model
class FixedCharge(models.Model):
    """
    Stores fixed monthly charges like garbage collection, security, etc.
    These are added to every invoice regardless of consumption.
    """
    manager = models.ForeignKey(
        PropertyManager,
        on_delete=models.CASCADE,
        help_text="Property manager who owns this charge"
    )
    charge_name = models.CharField(
        max_length=100,
        help_text="Name of the charge (e.g., 'Garbage Collection')"
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Fixed amount charged monthly"
    )
    effective_from = models.DateField(
        help_text="Date from which this charge becomes active"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this charge is currently applied"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['charge_name']
    
    def __str__(self):
        return f"{self.charge_name} - KES {self.amount}"


# Invoice Model
class Invoice(models.Model):
    """
    Represents a monthly utility invoice for a unit.
    Combines water, electricity, and fixed charges.
    """
    STATUS_CHOICES = [
        ('UNPAID', 'Unpaid'),
        ('PARTIALLY_PAID', 'Partially Paid'),
        ('PAID', 'Paid'),
        ('OVERDUE', 'Overdue'),
    ]
    
    unit = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        help_text="Unit this invoice is for"
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.SET_NULL,
        null=True,
        help_text="Tenant at time of invoice generation"
    )
    invoice_number = models.CharField(
        max_length=50,
        unique=True,
        help_text="Unique invoice identifier (e.g., INV-2026-03-001)"
    )
    invoice_date = models.DateField(
        help_text="Date invoice was generated"
    )
    due_date = models.DateField(
        help_text="Payment deadline"
    )
    billing_period = models.CharField(
        max_length=20,
        help_text="Billing period (e.g., 'March 2026')"
    )
    
    # Water charges
    water_units = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Water consumption in units"
    )
    water_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Rate per water unit"
    )
    water_charge = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Total water charge (units × rate)"
    )
    
    # Electricity charges (optional)
    electricity_units = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        null=True,
        blank=True,
        help_text="Electricity consumption in units"
    )
    electricity_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        null=True,
        blank=True,
        help_text="Rate per electricity unit"
    )
    electricity_charge = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Total electricity charge (units × rate)"
    )
    
    # Fixed charges
    total_fixed_charges = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Sum of all fixed monthly charges"
    )
    fixed_charges_breakdown = models.JSONField(
        default=dict,
        help_text="Breakdown of fixed charges {name: amount}"
    )
    
    # Totals and balance
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Sum of all charges before previous balance"
    )
    previous_balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Outstanding balance from previous invoices (negative = credit)"
    )
    total_due = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Final amount to be paid (subtotal + previous_balance)"
    )
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='UNPAID',
        help_text="Current payment status"
    )
    
    generated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='generated_invoices',
        help_text="User who generated this invoice"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-invoice_date']
    
    def __str__(self):
        return f"{self.invoice_number} - {self.unit.unit_number} - KES {self.total_due}"
    
    def calculate_totals(self):
        """
        Calculate all invoice totals.
        This method is called before saving.
        """
        # Calculate water charge
        self.water_charge = self.water_units * self.water_rate
        
        # Calculate electricity charge
        if self.electricity_units and self.electricity_rate:
            self.electricity_charge = self.electricity_units * self.electricity_rate
        else:
            self.electricity_charge = Decimal('0.00')
        
        # Calculate subtotal (before previous balance)
        self.subtotal = self.water_charge + self.electricity_charge + self.total_fixed_charges
        
        # Calculate total due (including previous balance)
        self.total_due = self.subtotal + self.previous_balance

    @property
    def abs_previous_balance(self):
        """Makes the balance a positive number for the display"""
        return abs(self.previous_balance)
    
    def update_status(self):
        """
        Update invoice status based on payments.
        Called after any payment is recorded.
        """
        from decimal import Decimal
        
        # Get total paid for this invoice
        total_paid = Payment.objects.filter(invoice=self).aggregate(
            total=Sum('amount_paid')
        )['total'] or Decimal('0.00')
        
        # Update status
        if total_paid >= self.total_due:
            self.status = 'PAID'
        elif total_paid > 0:
            self.status = 'PARTIALLY_PAID'
        elif self.due_date < timezone.now().date():
            self.status = 'OVERDUE'
        else:
            self.status = 'UNPAID'
        
        self.save()


# Payment Model
class Payment(models.Model):
    """
    Records payments made against invoices.
    Supports both manual entry and M-Pesa confirmation.
    """
    PAYMENT_METHOD_CHOICES = [
        ('MPESA', 'M-Pesa'),
        ('CASH', 'Cash'),
        ('BANK', 'Bank Transfer'),
    ]
    
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='payments',
        help_text="Invoice this payment is for"
    )
    payment_date = models.DateField(
        help_text="Date payment was received"
    )
    amount_paid = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Amount paid"
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        help_text="How payment was made"
    )
    mpesa_reference = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="M-Pesa transaction code (e.g., QGJ4X7Y8ZW)"
    )
    mpesa_phone = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        help_text="Phone number used for M-Pesa"
    )
    notes = models.TextField(
        blank=True,
        help_text="Additional payment notes"
    )
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        help_text="User who recorded this payment"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-payment_date']
    
    def __str__(self):
        return f"Payment KES {self.amount_paid} for {self.invoice.invoice_number}"
    
    def save(self, *args, **kwargs):
        """
        Override save to update invoice status after payment.
        """
        super().save(*args, **kwargs)
        # Update invoice status
        self.invoice.update_status()


# Account Balance Model
class AccountBalance(models.Model):
    """
    Tracks running balance for each tenant.
    Negative balance = tenant has credit.
    Positive balance = tenant owes money.
    """
    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name='account_balance',
        help_text="Tenant this balance belongs to"
    )
    current_balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Current balance (positive = debt, negative = credit)"
    )
    last_updated = models.DateTimeField(
        auto_now=True,
        help_text="Last time balance was updated"
    )
    
    def __str__(self):
        balance_type = "Credit" if self.current_balance < 0 else "Debt"
        return f"{self.tenant.user.get_full_name()} - {balance_type}: KES {abs(self.current_balance)}"
    
    @property
    def abs_balance(self):
        """Returns the positive value of the balance for the template"""
        return abs(self.current_balance)
