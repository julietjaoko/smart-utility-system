from django import forms
from .models import Unit, MeterReading, Meter, Payment

class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        # List the fields you want the user to fill out
        fields = ['unit_number', 'estate_name', 'has_water_meter', 'has_electricity_meter']
        
        # We can apply your existing CSS classes directly here!
        widgets = {
            'unit_number': forms.TextInput(attrs={
                'placeholder': 'e.g., A-101',
                'required': True
            }),
            'estate_name': forms.TextInput(attrs={
                'placeholder': 'e.g., Sunrise Apartments',
                'required': True
            }),
            'has_water_meter': forms.CheckboxInput(),
            'has_electricity_meter': forms.CheckboxInput(),
        }

class MeterReadingForm(forms.ModelForm):
    # We add 'unit' as a custom field to build the UI, even though 
    # it is not directly saved to the MeterReading model itself.
    unit = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        required=True,
        empty_label="-- Select a unit --",
        widget=forms.Select(attrs={'id': 'unit', 'onchange': 'loadMeters()'})
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
        # Extract the manager passed from the view
        manager = kwargs.pop('manager', None)
        super().__init__(*args, **kwargs)
        
        if manager:
            # Only show units belonging to this property manager
            self.fields['unit'].queryset = Unit.objects.filter(manager=manager)
            
        # By default, the meter dropdown is empty until JS fetches them
        self.fields['meter'].queryset = Meter.objects.none()
        
        # MAGIC TRICK: If the form is submitted (POST), populate the meter 
        # queryset based on the submitted unit so Django validation passes!
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
            # We map this ID and onchange event so your JavaScript still works perfectly!
            'payment_method': forms.Select(attrs={'id': 'payment_method', 'onchange': 'toggleMpesaFields()'}),
            'mpesa_reference': forms.TextInput(attrs={
                'id': 'mpesa_reference', 
                'placeholder': 'e.g., QGJ4X7Y8ZW',
                'style': 'text-transform: uppercase; font-family: monospace;'
            }),
            'mpesa_phone': forms.TextInput(attrs={'id': 'mpesa_phone', 'placeholder': 'e.g., 0712345678'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Any additional notes about this payment...'}),
        }

