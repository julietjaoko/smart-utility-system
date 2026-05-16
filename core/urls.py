from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('manager/dashboard/', views.manager_dashboard, name='manager_dashboard'),
    path('tenant/dashboard/', views.tenant_dashboard, name='tenant_dashboard'),
    path('manager/units/', views.manage_units, name='manage_units'),
    path('manager/tenants/', views.manage_tenants, name='manage_tenants'),
    path('manager/tenants/add/', views.add_tenant, name='add_tenant'),
    path('manager/readings/enter/', views.enter_meter_reading, name='enter_meter_reading'),
    path('manager/readings/', views.meter_reading_list, name='meter_reading_list'),
    path('manager/readings/<int:reading_id>/', views.meter_reading_detail, name='meter_reading_detail'),
    path('manager/units/<int:unit_id>/', views.unit_detail, name='unit_detail'),
    path('get-unit-meters/<int:unit_id>/', views.get_unit_meters, name='get_unit_meters'),
    path('api/unit/<int:unit_id>/meters/', views.get_unit_meters, name='get_unit_meters'),  # AJAX endpoint
    path('manager/analytics/', views.consumption_analytics, name='consumption_analytics'),
    path('manager/rates/', views.manage_rates, name='manage_rates'),
    path('manager/invoices/wizard/step-1/', views.billing_wizard_start, name='billing_wizard_start'),
    path('manager/invoices/wizard/step-2/', views.billing_wizard_rates, name='billing_wizard_rates'),
    path('manager/invoices/wizard/step-3/', views.billing_wizard_preview, name='billing_wizard_preview'),
    path('manager/invoices/', views.invoice_list, name='invoice_list'),
    path('manager/tenants/<int:tenant_id>/deactivate/', views.deactivate_tenant, name='deactivate_tenant'),
    path('manager/tenants/<int:tenant_id>/edit/', views.edit_tenant, name='edit_tenant'),
    path('manager/tenants/<int:tenant_id>/final-invoice/', views.generate_final_invoice, name='generate_final_invoice'),
    path('invoices/<int:invoice_id>/', views.invoice_detail, name='invoice_detail'),
    path('invoices/<int:invoice_id>/pay/', views.record_payment, name='record_payment'),
    path('manager/payments/', views.payment_list, name='payment_list'),
    path('invoices/<int:invoice_id>/mpesa/', views.initiate_mpesa_payment, name='initiate_mpesa_payment'),
    path('mpesa/callback/', views.mpesa_callback, name='mpesa_callback'),
    path('tenant/invoices/', views.tenant_invoices, name='tenant_invoices'),
    path('tenant/consumption/', views.tenant_consumption_history, name='tenant_consumption_history'),
    # PDF Downloads
    path('invoices/<int:invoice_id>/pdf/', views.download_invoice_pdf, name='download_invoice_pdf'),
    path('payments/<int:payment_id>/receipt/', views.download_payment_receipt, name='download_payment_receipt'),
    # Excel Exports
    path('manager/invoices/export/', views.export_invoices_excel, name='export_invoices_excel'),
    path('manager/payments/export/', views.export_payments_excel, name='export_payments_excel'),
    path('manager/consumption/export/', views.export_consumption_excel, name='export_consumption_excel'),
    # Advanced Analytics
    path('manager/analytics/advanced/', views.advanced_analytics, name='advanced_analytics'),
    path('manager/units/<int:unit_id>/performance/', views.unit_performance, name='unit_performance'),

    path('readings/anomaly/<int:reading_id>/<str:action>/', views.resolve_anomaly, name='resolve_anomaly'),
    # Bulk Operations
    path('manager/invoices/bulk-delete/', views.bulk_delete_invoices, name='bulk_delete_invoices'),
    path('manager/invoices/bulk-send/', views.bulk_send_invoices, name='bulk_send_invoices'),
    # Tenant Preferences & Token Logging
    path('tenant/preferences/', views.tenant_preferences, name='tenant_preferences'),
    path('tenant/electricity-tokens/', views.electricity_tokens, name='electricity_tokens'),
    path('tenant/electricity-tokens/add/', views.add_electricity_token, name='add_electricity_token'),
    path('tenant/electricity-tokens/<int:token_id>/delete/', views.delete_electricity_token, name='delete_electricity_token'),
    path('invoice/<int:invoice_id>/send-reminder/', views.send_invoice_reminder, name='send_invoice_reminder'),
    
    path('mpesa/webhook/<int:invoice_id>/', views.mpesa_webhook, name='mpesa_webhook'),
    path('security/change-password/', views.change_password, name='change_password'),
    path('payments/<int:payment_id>/delete/', views.delete_payment, name='delete_payment'),
    path("tenant/maintenance/", views.tenant_maintenance_requests, name="tenant_maintenance_requests"),
    path("tenant/maintenance/new/", views.tenant_new_maintenance_request, name="tenant_new_maintenance_request"),
    path("tenant/maintenance/<int:request_id>/", views.tenant_maintenance_detail, name="tenant_maintenance_detail"),

    path("manager/maintenance/", views.manager_maintenance_requests, name="manager_maintenance_requests"),
    path("manager/maintenance/<int:request_id>/", views.manager_maintenance_detail, name="manager_maintenance_detail"),

    path("system-admin/", views.system_admin_dashboard, name="system_admin_dashboard"),
    path("system-admin/managers/", views.system_admin_managers, name="system_admin_managers"),
    path("system-admin/managers/create/", views.system_admin_create_manager, name="system_admin_create_manager"),
    path("system-admin/users/<int:user_id>/toggle/", views.system_admin_toggle_user, name="system_admin_toggle_user"),

    # System Admin - Unit Management
    path('system-admin/units/', views.system_admin_manage_units, name='system_admin_manage_units'),
    path('system-admin/units/add/', views.system_admin_add_unit, name='system_admin_add_unit'),
    path('system-admin/units/<int:unit_id>/edit/', views.system_admin_edit_unit, name='system_admin_edit_unit'),

    # System Admin - Rates & Charges Management
    path('system-admin/rates/', views.system_admin_manage_rates, name='system_admin_manage_rates'),
    path('system-admin/rates/add/', views.system_admin_add_rate, name='system_admin_add_rate'),
    path('system-admin/charges/add/', views.system_admin_add_fixed_charge, name='system_admin_add_fixed_charge'),
    path('system-admin/charges/<int:charge_id>/delete/', views.system_admin_delete_fixed_charge, name='system_admin_delete_fixed_charge'),
    path('system-admin/analytics/', views.system_admin_analytics, name='system_admin_analytics'),
]