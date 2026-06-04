import logging
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render
from ..audit_log import log_audit
from ..models import (
    PropertyManager,
    Tenant,
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
            log_audit(
                request=request,
                category='AUTH',
                action='LOGIN',
                message=f'User {user.username} signed in',
                actor=user,
                object_type='User',
                object_id=user.id,
                object_repr=user.username,
            )

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
    log_audit(
        request=request,
        category='AUTH',
        action='LOGOUT',
        message=f'User {request.user.username} signed out',
        object_type='User',
        object_id=request.user.id,
        object_repr=request.user.username,
    )
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
            log_audit(
                request=request,
                category='AUTH',
                action='PASSWORD_CHANGED',
                message=f'User {user.username} changed their password',
                object_type='User',
                object_id=user.id,
                object_repr=user.username,
            )
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
