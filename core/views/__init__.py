"""View package – split from monolithic views.py."""

from .helpers import refresh_invoice_statuses, recalculate_tenant_ledger, tenant_can_log_tokens, recalculate_meter_readings
from .auth import login_view, logout_view, change_password
from .dashboards import manager_dashboard, tenant_dashboard
from .units import manage_units, add_unit, edit_unit, unit_detail, get_unit_meters
from .tenants import manage_tenants, add_tenant, edit_tenant, deactivate_tenant, generate_final_invoice
from .meter_readings import enter_meter_reading, edit_meter_reading, meter_reading_list, meter_reading_detail, resolve_anomaly
from .rates import manage_rates, add_rate, add_fixed_charge, delete_fixed_charge
from .billing import billing_wizard_start, billing_wizard_rates, billing_wizard_preview, invoice_list, invoice_detail, bulk_delete_invoices, bulk_send_invoices, send_invoice_reminder
from .payments import record_payment, edit_payment, delete_payment, payment_list, initiate_mpesa_payment, mpesa_callback, mpesa_webhook
from .tenant_portal import tenant_invoices, tenant_consumption_history, tenant_preferences, electricity_tokens, add_electricity_token, delete_electricity_token
from .analytics import consumption_analytics, advanced_analytics, all_unit_performance, unit_performance
from .exports import download_invoice_pdf, download_payment_receipt, export_invoices_excel, export_payments_excel, export_consumption_excel
from .maintenance import tenant_maintenance_requests, tenant_new_maintenance_request, tenant_maintenance_detail, manager_maintenance_requests, manager_maintenance_detail
from .system_admin import system_admin_dashboard, system_admin_manager_detail, system_admin_toggle_tenant, system_admin_managers, system_admin_create_manager, system_admin_toggle_user, system_admin_edit_manager, system_admin_edit_tenant

__all__ = [
    "refresh_invoice_statuses",
    "recalculate_tenant_ledger",
    "tenant_can_log_tokens",
    "recalculate_meter_readings",
    "login_view",
    "logout_view",
    "change_password",
    "manager_dashboard",
    "tenant_dashboard",
    "manage_units",
    "add_unit",
    "edit_unit",
    "unit_detail",
    "get_unit_meters",
    "manage_tenants",
    "add_tenant",
    "edit_tenant",
    "deactivate_tenant",
    "generate_final_invoice",
    "enter_meter_reading",
    "edit_meter_reading",
    "meter_reading_list",
    "meter_reading_detail",
    "resolve_anomaly",
    "manage_rates",
    "add_rate",
    "add_fixed_charge",
    "delete_fixed_charge",
    "billing_wizard_start",
    "billing_wizard_rates",
    "billing_wizard_preview",
    "invoice_list",
    "invoice_detail",
    "bulk_delete_invoices",
    "bulk_send_invoices",
    "send_invoice_reminder",
    "record_payment",
    "edit_payment",
    "delete_payment",
    "payment_list",
    "initiate_mpesa_payment",
    "mpesa_callback",
    "mpesa_webhook",
    "tenant_invoices",
    "tenant_consumption_history",
    "tenant_preferences",
    "electricity_tokens",
    "add_electricity_token",
    "delete_electricity_token",
    "consumption_analytics",
    "advanced_analytics",
    "all_unit_performance",
    "unit_performance",
    "download_invoice_pdf",
    "download_payment_receipt",
    "export_invoices_excel",
    "export_payments_excel",
    "export_consumption_excel",
    "tenant_maintenance_requests",
    "tenant_new_maintenance_request",
    "tenant_maintenance_detail",
    "manager_maintenance_requests",
    "manager_maintenance_detail",
    "system_admin_dashboard",
    "system_admin_manager_detail",
    "system_admin_toggle_tenant",
    "system_admin_managers",
    "system_admin_create_manager",
    "system_admin_toggle_user",
    "system_admin_edit_manager",
    "system_admin_edit_tenant",
]
