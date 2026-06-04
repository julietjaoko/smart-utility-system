"""Shared helpers for PDF/Excel downloads."""

import re
from io import BytesIO

from django.http import HttpResponse


def tenant_display_name(invoice):
    if not invoice.tenant or not invoice.tenant.user:
        return '—'
    user = invoice.tenant.user
    return user.get_full_name() or user.username


def safe_download_filename(base_name, extension):
    """Strip characters that break Content-Disposition or filesystem paths."""
    cleaned = re.sub(r'[^\w.\-]+', '_', base_name).strip('_')
    return f'{cleaned or "download"}.{extension}'


def excel_http_response(workbook, filename):
    buffer = BytesIO()
    workbook.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def pdf_http_response(pdf_bytes, filename):
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
