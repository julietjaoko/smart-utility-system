"""One-time script to split core/views.py into core/views/ package."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "core" / "views.py"
VIEWS_PKG = ROOT / "core" / "views"

MODULE_MAP = {
    "helpers": [
        "refresh_invoice_statuses",
        "recalculate_tenant_ledger",
        "tenant_can_log_tokens",
        "recalculate_meter_readings",
    ],
    "auth": ["login_view", "logout_view", "change_password"],
    "dashboards": ["manager_dashboard", "tenant_dashboard"],
    "units": ["manage_units", "add_unit", "edit_unit", "unit_detail", "get_unit_meters"],
    "tenants": [
        "manage_tenants",
        "add_tenant",
        "edit_tenant",
        "deactivate_tenant",
        "generate_final_invoice",
    ],
    "meter_readings": [
        "enter_meter_reading",
        "edit_meter_reading",
        "meter_reading_list",
        "meter_reading_detail",
        "resolve_anomaly",
    ],
    "rates": ["manage_rates", "add_rate", "add_fixed_charge", "delete_fixed_charge"],
    "billing": [
        "billing_wizard_start",
        "billing_wizard_rates",
        "billing_wizard_preview",
        "invoice_list",
        "invoice_detail",
        "bulk_delete_invoices",
        "bulk_send_invoices",
        "send_invoice_reminder",
    ],
    "payments": [
        "record_payment",
        "edit_payment",
        "delete_payment",
        "payment_list",
        "initiate_mpesa_payment",
        "mpesa_callback",
        "mpesa_webhook",
    ],
    "tenant_portal": [
        "tenant_invoices",
        "tenant_consumption_history",
        "tenant_preferences",
        "electricity_tokens",
        "add_electricity_token",
        "delete_electricity_token",
    ],
    "analytics": [
        "consumption_analytics",
        "advanced_analytics",
        "all_unit_performance",
        "unit_performance",
    ],
    "exports": [
        "download_invoice_pdf",
        "download_payment_receipt",
        "export_invoices_excel",
        "export_payments_excel",
        "export_consumption_excel",
    ],
    "maintenance": [
        "tenant_maintenance_requests",
        "tenant_new_maintenance_request",
        "tenant_maintenance_detail",
        "manager_maintenance_requests",
        "manager_maintenance_detail",
    ],
    "system_admin": [
        "system_admin_dashboard",
        "system_admin_manager_detail",
        "system_admin_toggle_tenant",
        "system_admin_managers",
        "system_admin_create_manager",
        "system_admin_toggle_user",
        "system_admin_edit_manager",
        "system_admin_edit_tenant",
    ],
}

MANAGER_ONLY = {
    "manage_tenants",
    "meter_reading_list",
    "meter_reading_detail",
    "consumption_analytics",
    "manage_rates",
    "add_rate",
    "add_fixed_charge",
    "delete_fixed_charge",
    "billing_wizard_start",
    "billing_wizard_rates",
    "billing_wizard_preview",
    "invoice_list",
    "payment_list",
    "export_invoices_excel",
    "export_payments_excel",
    "export_consumption_excel",
    "advanced_analytics",
    "all_unit_performance",
    "unit_performance",
    "resolve_anomaly",
    "unit_detail",
    "edit_unit",
    "deactivate_tenant",
    "bulk_delete_invoices",
    "bulk_send_invoices",
    "edit_tenant",
    "generate_final_invoice",
    "manager_maintenance_requests",
    "manager_maintenance_detail",
}

TENANT_ONLY = {
    "tenant_invoices",
    "tenant_consumption_history",
    "tenant_preferences",
    "electricity_tokens",
    "add_electricity_token",
    "delete_electricity_token",
    "tenant_maintenance_requests",
    "tenant_new_maintenance_request",
    "tenant_maintenance_detail",
}

IMPORT_HEADER = '''import json
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
'''

HELPERS_HEADER = '''import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ..models import AccountBalance, Invoice, Meter, MeterReading, Payment, Tenant

logger = logging.getLogger(__name__)
'''

MANAGER_CHECK_PATTERNS = [
    re.compile(
        r"\n    if request\.user\.role != ['\"]PROPERTY_MANAGER['\"]:\n"
        r"        messages\.error\(request, [^\n]+\)\n"
        r"        return redirect\(['\"]tenant_dashboard['\"]\)\n",
        re.MULTILINE,
    ),
    re.compile(
        r"\n    if request\.user\.role != ['\"]PROPERTY_MANAGER['\"]:\n"
        r"        return redirect\(['\"]tenant_dashboard['\"]\)\n",
        re.MULTILINE,
    ),
    re.compile(
        r'\n    if request\.user\.role != "PROPERTY_MANAGER":\n'
        r'        messages\.error\(request, "Access denied"\)\n'
        r'        return redirect\("tenant_dashboard"\)\n',
        re.MULTILINE,
    ),
]

TENANT_CHECK_PATTERNS = [
    re.compile(
        r"\n    if request\.user\.role != ['\"]TENANT['\"]:\n"
        r"        messages\.error\(request, [^\n]+\)\n"
        r"        return redirect\(['\"]manager_dashboard['\"]\)\n",
        re.MULTILINE,
    ),
    re.compile(
        r'\n    if request\.user\.role != "TENANT":\n'
        r'        messages\.error\(request, "Access denied"\)\n'
        r'        return redirect\("manager_dashboard"\)\n',
        re.MULTILINE,
    ),
]


def extract_functions(source: str):
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    result = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            start = (
                node.decorator_list[0].lineno - 1
                if node.decorator_list
                else node.lineno - 1
            )
            result[node.name] = "".join(lines[start : node.end_lineno])
    return result


def standardize_function(name: str, code: str) -> str:
    if name in MANAGER_ONLY:
        for pattern in MANAGER_CHECK_PATTERNS:
            code = pattern.sub("\n", code)
        code = code.replace("@login_required\n", "")
        if "@manager_required" not in code:
            code = code.replace(f"def {name}(", f"@manager_required\ndef {name}(")
    elif name in TENANT_ONLY:
        for pattern in TENANT_CHECK_PATTERNS:
            code = pattern.sub("\n", code)
        code = code.replace("@login_required\n", "")
        if "@tenant_required" not in code:
            code = code.replace(f"def {name}(", f"@tenant_required\ndef {name}(")
    elif name == "deactivate_tenant":
        # POST handler keeps an inner role check; remove only the outer duplicate.
        code = re.sub(
            r"^@login_required\n",
            "",
            code,
            count=1,
            flags=re.MULTILINE,
        )
        if "@manager_required" not in code:
            code = code.replace("def deactivate_tenant(", "@manager_required\ndef deactivate_tenant(")
    return code


def main():
    legacy = ROOT / "core" / "views_legacy.py"
    if not SOURCE.exists() and legacy.exists():
        SOURCE.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")

    source = SOURCE.read_text(encoding="utf-8")
    functions = extract_functions(source)

    expected = {name for names in MODULE_MAP.values() for name in names}
    missing = expected - set(functions)
    if missing:
        raise SystemExit(f"Missing functions in source: {sorted(missing)}")

    if VIEWS_PKG.exists():
        import shutil
        shutil.rmtree(VIEWS_PKG)

    VIEWS_PKG.mkdir(exist_ok=True)

    all_exports = []
    for module_name, func_names in MODULE_MAP.items():
        header = HELPERS_HEADER if module_name == "helpers" else IMPORT_HEADER
        parts = [header.rstrip(), ""]
        for func_name in func_names:
            code = functions[func_name]
            if module_name != "helpers":
                code = standardize_function(func_name, code)
            parts.append(code.rstrip())
            parts.append("")
            all_exports.append(func_name)

        out_path = VIEWS_PKG / f"{module_name}.py"
        out_path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
        print(f"Wrote {out_path}")

    init_lines = ['"""View package – split from monolithic views.py."""', ""]
    for module_name, func_names in MODULE_MAP.items():
        imported = ", ".join(func_names)
        init_lines.append(f"from .{module_name} import {imported}")
    init_lines.append("")
    init_lines.append("__all__ = [")
    for name in all_exports:
        init_lines.append(f'    "{name}",')
    init_lines.append("]")
    init_lines.append("")

    (VIEWS_PKG / "__init__.py").write_text("\n".join(init_lines), encoding="utf-8")
    print("Wrote core/views/__init__.py")

    backup = ROOT / "core" / "views_legacy.py"
    if not backup.exists():
        backup.write_text(source, encoding="utf-8")
        print(f"Backed up original to {backup}")

    SOURCE.unlink()
    print(f"Removed {SOURCE}")


if __name__ == "__main__":
    main()
