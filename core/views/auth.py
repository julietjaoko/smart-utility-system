import json
import logging
import os
from calendar import month_name, monthrange
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, F, Max, Q, Sum
from django.db.models.functions import TruncMonth, TruncYear
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt

from ..decorators import manager_required, system_admin_required, tenant_required
from ..email_utils import InvoiceNotification, PaymentNotification
from ..excel_exporter import ConsumptionExporter, InvoiceExporter, PaymentExporter
from ..forms import (
    MaintenanceMessageForm,
    MaintenanceRequestForm,
    MeterReadingForm,
    PaymentForm,
    PropertyManagerCreationForm,
    PropertyManagerUpdateForm,
    TenantCreationForm,
    TenantUpdateForm,
    UnitForm,
)
from ..models import (
    AccountBalance,
    ElectricityToken,
    FixedCharge,
    Invoice,
    MaintenanceMessage,
    MaintenanceRequest,
    Meter,
    MeterReading,
    Payment,
    PropertyManager,
    RateConfig,
    Tenant,
    TenantPreferences,
    Unit,
)
from ..mpesa import process_mpesa_callback
from ..pdf_generator import InvoicePDF, PaymentReceiptPDF
from ..sms_utils import InvoiceSMS, PaymentSMS, TokenSMS
from .helpers import (
    recalculate_meter_readings,
    recalculate_tenant_ledger,
    refresh_invoice_statuses,
    tenant_can_log_tokens,
)

logger = logging.getLogger(__name__)
User = get_user_model()

def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_superuser or request.user.role == "SYSTEM_ADMIN":
            return redirect("system_admin_dashboard")

        if request.user.role == "PROPERTY_MANAGER":
            if PropertyManager.objects.filter(user=request.user).exists():
                return redirect("manager_dashboard")

            logout(request)
            messages.error(request, "Property Manager profile not found. Please contact admin.")
            return redirect("login")

        if request.user.role == "TENANT":
            if Tenant.objects.filter(user=request.user).exists():
                return redirect("tenant_dashboard")

            logout(request)
            messages.error(request, "Tenant profile not found. Please contact admin.")
            return redirect("login")

        return redirect("/admin/")

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)

            if user.is_superuser or user.role == "SYSTEM_ADMIN":
                return redirect("system_admin_dashboard")

            if user.role == "PROPERTY_MANAGER" and not PropertyManager.objects.filter(user=user).exists():
                messages.error(request, "Property Manager profile not found. Please contact admin.")
                return render(request, "core/login.html")

            if user.role == "TENANT" and not Tenant.objects.filter(user=user).exists():
                messages.error(request, "Tenant profile not found. Please contact admin.")
                return render(request, "core/login.html")

            if user.role == "PROPERTY_MANAGER":
                return redirect("manager_dashboard")
            elif user.role == "TENANT":
                return redirect("tenant_dashboard")
            else:
                return redirect("/admin/")

        messages.error(request, "Invalid username or password")

    return render(request, "core/login.html")

@login_required
def logout_view(request):
    logout(request)
    return redirect('login')

@login_required
def change_password(request):
    """
    Allows both Tenants and Property Managers to change their password securely.
    """
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            # Django rotates the auth hash on password change, so the session must be refreshed.
            update_session_auth_hash(request, user)
            
            messages.success(request, '✓ Your password was successfully updated!')
            
            if request.user.role == 'PROPERTY_MANAGER':
                return redirect('manager_dashboard')
            else:
                return redirect('tenant_dashboard')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = PasswordChangeForm(request.user)
        
    return render(request, 'core/change_password.html', {'form': form})
