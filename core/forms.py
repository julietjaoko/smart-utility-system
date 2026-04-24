from django import forms
from django.contrib.auth import get_user_model

from .models import Meter, MeterReading, Payment, Tenant, Unit

User = get_user_model()


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ['unit_number', 'estate_name', 'has_water_meter', 'has_electricity_meter']
        widgets = {
            'unit_number': forms.TextInput(attrs={
                'placeholder': 'e.g., A-101',
                'required': True,
            }),
            'estate_name': forms.TextInput(attrs={
                'placeholder': 'e.g., Sunrise Apartments',
                'required': True,
            }),
            'has_water_meter': forms.CheckboxInput(),
            'has_electricity_meter': forms.CheckboxInput(),
        }


class MeterReadingForm(forms.ModelForm):
    unit = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        required=True,
        empty_label="-- Select a unit --",
        widget=forms.Select(attrs={'id': 'unit', 'onchange': 'loadMeters()'}),
    )

    class Meta:
        model = MeterReading
        fields = ['meter', 'reading_date', 'reading_value', 'photo', 'notes']
        widgets = {
            'meter': forms.Select(attrs={'id': 'meter', 'onchange': 'showPreviousReading()'}),
            'reading_date': forms.DateInput(attrs={'type': 'date'}),
            'reading_value': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'placeholder': 'e.g., 1250.50'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Optional notes...', 'style': 'resize: vertical;'}),
        }

    def __init__(self, *args, **kwargs):
        manager = kwargs.pop('manager', None)
        super().__init__(*args, **kwargs)

        if manager:
            self.fields['unit'].queryset = Unit.objects.filter(manager=manager)

        self.fields['meter'].queryset = Meter.objects.none()

        if 'unit' in self.data:
            try:
                unit_id = int(self.data.get('unit'))
                self.fields['meter'].queryset = Meter.objects.filter(unit_id=unit_id, is_active=True)
            except (ValueError, TypeError):
                pass


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['payment_date', 'amount_paid', 'payment_method', 'mpesa_reference', 'mpesa_phone', 'notes']
        widgets = {
            'payment_date': forms.DateInput(attrs={'type': 'date'}),
            'amount_paid': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01', 'placeholder': 'Enter amount received'}),
            'payment_method': forms.Select(attrs={'id': 'payment_method', 'onchange': 'toggleMpesaFields()'}),
            'mpesa_reference': forms.TextInput(attrs={
                'id': 'mpesa_reference',
                'placeholder': 'e.g., QGJ4X7Y8ZW',
                'style': 'text-transform: uppercase; font-family: monospace;',
            }),
            'mpesa_phone': forms.TextInput(attrs={'id': 'mpesa_phone', 'placeholder': 'e.g., 0712345678'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Any additional notes about this payment...'}),
        }


class TenantCreationForm(forms.Form):
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'placeholder': 'John', 'class': 'form-control'}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'placeholder': 'Doe', 'class': 'form-control'}))
    email = forms.EmailField(
        help_text="They will use this email to log in.",
        widget=forms.EmailInput(attrs={'placeholder': 'john.doe@example.com', 'class': 'form-control'}),
    )
    phone_number = forms.CharField(
        max_length=15,
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'e.g., 0712345678', 'class': 'form-control'}),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Temporary Password', 'class': 'form-control'}),
        help_text="Set a temporary password. They can change it later.",
    )
    unit = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        required=False,
        empty_label="-- Select a Unit --",
        widget=forms.Select(attrs={'class': 'form-control'}),
    )

    def __init__(self, *args, **kwargs):
        manager = kwargs.pop('manager', None)
        super().__init__(*args, **kwargs)

        if manager:
            occupied_units = Tenant.objects.filter(
                unit__manager=manager,
                unit__isnull=False,
                is_active=True,
            ).values_list('unit_id', flat=True)

            self.fields['unit'].queryset = Unit.objects.filter(
                manager=manager
            ).exclude(id__in=occupied_units)

    def clean_email(self):
        email = self.cleaned_data.get('email')

        if User.objects.filter(email=email).exists() or User.objects.filter(username=email).exists():
            raise forms.ValidationError("A user with this email already exists in the system.")

        return email

    def clean_unit(self):
        unit = self.cleaned_data.get('unit')

        if unit and Tenant.objects.filter(unit=unit, is_active=True).exists():
            raise forms.ValidationError(
                f"Action Blocked: Unit {unit.unit_number} was just occupied by another tenant. Please select a different unit."
            )

        return unit


class TenantUpdateForm(forms.Form):
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class': 'form-control'}))
    phone_number = forms.CharField(max_length=15, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    unit = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        required=False,
        empty_label="-- Unassigned / Vacated --",
        widget=forms.Select(attrs={'class': 'form-control'}),
    )

    def __init__(self, *args, **kwargs):
        self.manager = kwargs.pop('manager', None)
        self.current_tenant = kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)

        if self.manager:
            occupied_units = Tenant.objects.filter(
                unit__manager=self.manager,
                unit__isnull=False,
                is_active=True,
            ).exclude(
                id=self.current_tenant.id if self.current_tenant else None
            ).values_list('unit_id', flat=True)

            self.fields['unit'].queryset = Unit.objects.filter(
                manager=self.manager
            ).exclude(id__in=occupied_units)

    def clean_unit(self):
        unit = self.cleaned_data.get('unit')

        if unit and self.current_tenant:
            is_occupied_by_other = Tenant.objects.filter(
                unit=unit,
                is_active=True,
            ).exclude(id=self.current_tenant.id).exists()
            if is_occupied_by_other:
                raise forms.ValidationError(
                    f"Action Blocked: Unit {unit.unit_number} is occupied by another tenant."
                )

        return unit