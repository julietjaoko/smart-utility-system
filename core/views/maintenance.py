import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import manager_required, tenant_required
from ..forms import (
    MaintenanceMessageForm,
    MaintenanceRequestForm,
)
from ..models import (
    MaintenanceMessage,
    MaintenanceRequest,
    PropertyManager,
    Tenant,
)

logger = logging.getLogger(__name__)
User = get_user_model()

@tenant_required
def tenant_maintenance_requests(request):

    tenant = get_object_or_404(Tenant.objects.select_related("unit__manager"), user=request.user)
    requests_qs = MaintenanceRequest.objects.filter(tenant=tenant).select_related("unit", "manager")

    return render(request, "core/tenant_maintenance_requests.html", {"requests": requests_qs, "tenant": tenant})

@tenant_required
def tenant_new_maintenance_request(request):

    tenant = get_object_or_404(Tenant.objects.select_related("unit__manager"), user=request.user)

    if not tenant.unit:
        messages.error(request, "You need an assigned unit before logging a maintenance request.")
        return redirect("tenant_dashboard")

    if request.method == "POST":
        form = MaintenanceRequestForm(request.POST, request.FILES)
        if form.is_valid():
            maintenance_request = form.save(commit=False)
            maintenance_request.tenant = tenant
            maintenance_request.unit = tenant.unit
            maintenance_request.manager = tenant.unit.manager
            maintenance_request.save()

            MaintenanceMessage.objects.create(
                request=maintenance_request,
                sender=request.user,
                message=maintenance_request.description,
            )

            messages.success(request, "Maintenance request submitted successfully.")
            return redirect("tenant_maintenance_detail", request_id=maintenance_request.id)
        messages.error(request, "Please correct the errors below.")
    else:
        form = MaintenanceRequestForm()

    return render(request, "core/tenant_new_maintenance_request.html", {"form": form})

@tenant_required
def tenant_maintenance_detail(request, request_id):

    tenant = get_object_or_404(Tenant, user=request.user)
    maintenance_request = get_object_or_404(
        MaintenanceRequest.objects.select_related("tenant__user", "unit", "manager"),
        id=request_id,
        tenant=tenant,
    )

    if request.method == "POST":
        form = MaintenanceMessageForm(request.POST)
        if form.is_valid():
            reply = form.save(commit=False)
            reply.request = maintenance_request
            reply.sender = request.user
            reply.save()
            maintenance_request.save()
            messages.success(request, "Reply sent.")
            return redirect("tenant_maintenance_detail", request_id=request_id)
    else:
        form = MaintenanceMessageForm()

    return render(request, "core/maintenance_detail.html", {
        "maintenance_request": maintenance_request,
        "reply_form": form,
        "is_manager": False,
    })

@manager_required
def manager_maintenance_requests(request):

    manager = get_object_or_404(PropertyManager, user=request.user)
    requests_qs = MaintenanceRequest.objects.filter(manager=manager).select_related("tenant__user", "unit")

    status_filter = request.GET.get("status", "").strip().upper()
    if status_filter:
        requests_qs = requests_qs.filter(status=status_filter)

    return render(request, "core/manager_maintenance_requests.html", {
        "requests": requests_qs,
        "current_status": status_filter,
        "statuses": MaintenanceRequest.STATUS_CHOICES,
    })

@manager_required
def manager_maintenance_detail(request, request_id):

    manager = get_object_or_404(PropertyManager, user=request.user)
    maintenance_request = get_object_or_404(
        MaintenanceRequest.objects.select_related("tenant__user", "unit", "manager"),
        id=request_id,
        manager=manager,
    )

    if request.method == "POST":
        if "status" in request.POST:
            new_status = request.POST.get("status")
            valid_statuses = dict(MaintenanceRequest.STATUS_CHOICES)
            if new_status in valid_statuses:
                maintenance_request.status = new_status
                maintenance_request.save()
                messages.success(request, "Status updated.")
                return redirect("manager_maintenance_detail", request_id=request_id)

        form = MaintenanceMessageForm(request.POST)
        if form.is_valid():
            reply = form.save(commit=False)
            reply.request = maintenance_request
            reply.sender = request.user
            reply.save()
            maintenance_request.save()
            messages.success(request, "Reply sent.")
            return redirect("manager_maintenance_detail", request_id=request_id)
    else:
        form = MaintenanceMessageForm()

    return render(request, "core/maintenance_detail.html", {
        "maintenance_request": maintenance_request,
        "reply_form": form,
        "is_manager": True,
        "statuses": MaintenanceRequest.STATUS_CHOICES,
    })
