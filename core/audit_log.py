"""Application audit trail helpers."""

from .models import AuditLog, PropertyManager


def get_client_ip(request):
    if not request:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def resolve_property_manager(request, manager=None):
    if manager is not None:
        return manager
    if not request or not getattr(request, 'user', None) or not request.user.is_authenticated:
        return None
    if request.user.role != 'PROPERTY_MANAGER':
        return None
    return PropertyManager.objects.filter(user=request.user).first()


def log_audit(
    *,
    category,
    action,
    message,
    request=None,
    actor=None,
    property_manager=None,
    severity='INFO',
    object_type='',
    object_id=None,
    object_repr='',
    metadata=None,
):
    """Persist one audit log entry. Never raises — logging must not break workflows."""
    try:
        if actor is None and request and getattr(request, 'user', None):
            user = request.user
            actor = user if user.is_authenticated else None

        manager = property_manager
        if manager is None:
            manager = resolve_property_manager(request, manager=None)

        AuditLog.objects.create(
            actor=actor,
            property_manager=manager,
            category=category,
            action=action,
            message=message,
            severity=severity,
            object_type=object_type or '',
            object_id=object_id,
            object_repr=object_repr or '',
            metadata=metadata or {},
            ip_address=get_client_ip(request),
        )
    except Exception:
        pass
