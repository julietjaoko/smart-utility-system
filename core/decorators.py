from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages

def manager_required(view_func):
    """Decorator for views that checks that the user is a property manager."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
            
        if request.user.role != 'PROPERTY_MANAGER':
            messages.error(request, 'Access denied: Property Manager privileges required.')
            return redirect('tenant_dashboard')
            
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def tenant_required(view_func):
    """Decorator for views that checks that the user is a tenant."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
            
        if request.user.role != 'TENANT':
            messages.error(request, 'Access denied: Tenant privileges required.')
            return redirect('manager_dashboard')
            
        return view_func(request, *args, **kwargs)
    return _wrapped_view