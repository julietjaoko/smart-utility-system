import json
import logging
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from ..decorators import manager_required
from ..email_utils import PaymentNotification
from ..forms import PaymentForm
from ..models import (
    AccountBalance,
    Invoice,
    Payment,
    PropertyManager,
    Tenant,
)
from ..mpesa import process_mpesa_callback
from ..sms_utils import PaymentSMS
from .helpers import recalculate_tenant_ledger

logger = logging.getLogger(__name__)
User = get_user_model()

@manager_required
def record_payment(request, invoice_id):
    """
    Record a payment against an invoice using Django Forms.
    """
    manager = PropertyManager.objects.get(user=request.user)
    invoice = get_object_or_404(Invoice, id=invoice_id, unit__manager=manager)
    recalculate_tenant_ledger(invoice.tenant)
    invoice.refresh_from_db()
    
    total_paid = Payment.objects.filter(invoice=invoice).aggregate(
        total=Sum('amount_paid')
    )['total'] or Decimal('0.00')
    
    remaining_balance = invoice.total_due - total_paid

    if request.method == 'POST':
        form = PaymentForm(request.POST)
        
        if form.is_valid():
            try:
                # Payment and balance changes must commit together to keep the tenant ledger consistent.
                with transaction.atomic():
                    payment = form.save(commit=False)
                    payment.invoice = invoice
                    payment.recorded_by = request.user
                    
                    # Non-M-Pesa payments should not retain stale mobile-money details from the form.
                    if payment.payment_method != 'MPESA':
                        payment.mpesa_reference = None
                        payment.mpesa_phone = None
                        
                    payment.save()
                    
                    tenant = invoice.tenant
                    account_balance, created = AccountBalance.objects.select_for_update().get_or_create(
                        tenant=tenant,
                        defaults={'current_balance': Decimal('0.00')}
                    )
                    account_balance.current_balance -= payment.amount_paid
                    account_balance.save()
                    recalculate_tenant_ledger(tenant)

                # Receipts are sent after the database work so delivery problems do not cancel payment.
                try:
                    email_notifier = PaymentNotification()
                    email_notifier.send_payment_confirmation(payment)
                except Exception as email_error:
                    logger.error(f"Email error: {str(email_error)}")

                try:
                    sms_notifier = PaymentSMS()
                    sms_result = sms_notifier.send_payment_confirmation(payment)
                except Exception as sms_error:
                    logger.error(f"SMS error: {str(sms_error)}")

                messages.success(
                    request,
                    f'✓ Payment of KES {payment.amount_paid} recorded successfully! '
                    f'Invoice status: {invoice.get_status_display()}'
                )
                return redirect('invoice_detail', invoice_id=invoice.id)
                
            except Exception as e:
                messages.error(request, f'Database error recording payment: {str(e)}')
        else:
            messages.error(request, 'Please correct the errors in the form below.')
            
    else:
        # GET request - load form with pre-filled defaults
        form = PaymentForm(initial={
            'payment_date': timezone.now().date(),
            'amount_paid': remaining_balance if remaining_balance > 0 else Decimal('0.00'),
            'payment_method': 'MPESA'
        })
        
    context = {
        'form': form,
        'invoice': invoice,
        'total_paid': total_paid,
        'remaining_balance': remaining_balance,
    }
    
    return render(request, 'core/record_payment.html', context)

@manager_required
def edit_payment(request, payment_id):
    manager = PropertyManager.objects.get(user=request.user)
    payment = get_object_or_404(Payment, id=payment_id, invoice__unit__manager=manager)
    invoice = payment.invoice
    recalculate_tenant_ledger(invoice.tenant)
    invoice.refresh_from_db()
    old_amount = payment.amount_paid
    total_paid = Payment.objects.filter(invoice=invoice).aggregate(
        total=Sum('amount_paid')
    )['total'] or Decimal('0.00')
    remaining_balance = invoice.total_due - total_paid

    if request.method == 'POST':
        form = PaymentForm(request.POST, instance=payment)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated_payment = form.save(commit=False)
                    if updated_payment.payment_method != 'MPESA':
                        updated_payment.mpesa_reference = None
                        updated_payment.mpesa_phone = None
                    updated_payment.save()

                    delta = updated_payment.amount_paid - old_amount
                    account_balance, _ = AccountBalance.objects.select_for_update().get_or_create(
                        tenant=invoice.tenant,
                        defaults={'current_balance': Decimal('0.00')}
                    )
                    account_balance.current_balance -= delta
                    account_balance.save()
                    recalculate_tenant_ledger(invoice.tenant)

                messages.success(request, 'Payment updated successfully.')
                return redirect('invoice_detail', invoice_id=invoice.id)
            except Exception as e:
                messages.error(request, f'Database error updating payment: {str(e)}')
        else:
            messages.error(request, 'Please correct the errors in the form below.')
    else:
        form = PaymentForm(instance=payment)

    context = {
        'form': form,
        'invoice': invoice,
        'payment': payment,
        'total_paid': total_paid,
        'remaining_balance': remaining_balance,
        'is_edit': True,
    }
    return render(request, 'core/record_payment.html', context)

@manager_required
def delete_payment(request, payment_id):
    """
    Safely deletes a mistakenly entered payment and reverses the 
    tenant's account balance.
    """
    manager = PropertyManager.objects.get(user=request.user)
    payment = get_object_or_404(Payment, id=payment_id, invoice__unit__manager=manager)
    invoice = payment.invoice
    tenant = invoice.tenant
    
    if request.method == 'POST':
        try:
            with transaction.atomic():
                # Deleting a payment restores the amount to the tenant's outstanding balance.
                account_balance, _ = AccountBalance.objects.select_for_update().get_or_create(
                    tenant=tenant,
                    defaults={'current_balance': Decimal('0.00')}
                )
                account_balance.current_balance += payment.amount_paid
                account_balance.save()
                
                amount_deleted = payment.amount_paid
                payment.delete()
                
                # The ledger replay keeps later invoices and balances consistent after the reversal.
                recalculate_tenant_ledger(tenant)
                
            messages.success(request, f'✓ Payment of KES {amount_deleted} safely reversed. Account balance updated.')
        except Exception as e:
            messages.error(request, f'Error reversing payment: {str(e)}')
            
        return redirect('invoice_detail', invoice_id=invoice.id)
        
    return redirect('payment_list')

@manager_required
def payment_list(request):
    """
    Display list of all payments with filtering.
    Property managers can see payment history.
    """
    # Security check
        
    try:
        manager = PropertyManager.objects.get(user=request.user)
        
        # 1. BASE QUERYSET (For accurate top-level dashboard stats)
        base_payments = Payment.objects.filter(invoice__unit__manager=manager)
        
        total_payments = base_payments.count()
        total_amount = base_payments.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
        
        # 2. TABLE QUERYSET (Apply all your filters to this one)
        payments = base_payments.select_related(
            'invoice__unit',
            'invoice__tenant__user',
            'recorded_by'
        ).order_by('-payment_date')
        
        # Filter by payment method (Force uppercase to match DB: 'MPESA', 'CASH', 'BANK')
        method_filter = request.GET.get('method', '').strip().upper()
        if method_filter:
            payments = payments.filter(payment_method=method_filter)
            
        # Filter by date range (add .strip() to be safe)
        start_date = request.GET.get('start_date', '').strip()
        end_date = request.GET.get('end_date', '').strip()
        if start_date:
            payments = payments.filter(payment_date__gte=start_date)
        if end_date:
            payments = payments.filter(payment_date__lte=end_date)
            
        # Search by invoice number or M-Pesa reference
        search_query = request.GET.get('search', '').strip()
        if search_query:
            payments = payments.filter(
                Q(invoice__invoice_number__icontains=search_query) |
                Q(mpesa_reference__icontains=search_query)
            )
            
        # Pagination
        from django.core.paginator import Paginator
        paginator = Paginator(payments, 20)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        
        context = {
            'page_obj': page_obj,
            'total_payments': total_payments,
            'total_amount': total_amount,
            'current_filters': {
                'method': request.GET.get('method', ''), # Keep original casing for HTML dropdown
                'start_date': start_date,
                'end_date': end_date,
                'search': search_query,
            }
        }
        
        return render(request, 'core/payment_list.html', context)
        
    except PropertyManager.DoesNotExist:
        messages.error(request, 'Property Manager profile not found')
        return redirect('manager_dashboard')

@login_required
def initiate_mpesa_payment(request, invoice_id):
    """
    Initiates an M-Pesa STK push.
    Securely handles requests from both Property Managers and Tenants.
    """
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'error': 'Invalid request method. Must be POST.'
        })

    phone_number = request.POST.get('phone_number')
    if not phone_number:
        return JsonResponse({
            'success': False,
            'error': 'Phone number is required.'
        })

    invoice = None
    if request.user.role == 'PROPERTY_MANAGER':
        manager = PropertyManager.objects.get(user=request.user)
        invoice = get_object_or_404(Invoice, id=invoice_id, unit__manager=manager)
    elif request.user.role == 'TENANT':
        tenant = Tenant.objects.get(user=request.user)
        invoice = get_object_or_404(Invoice, id=invoice_id, tenant=tenant)
    else:
        return JsonResponse({
            'success': False,
            'error': 'Unauthorized role.'
        })

    recalculate_tenant_ledger(invoice.tenant)
    invoice.refresh_from_db()

    if invoice.status == 'PAID':
        return JsonResponse({
            'success': False,
            'error': 'This invoice is already fully paid.'
        })

    total_paid = Payment.objects.filter(invoice=invoice).aggregate(
        total=Sum('amount_paid')
    )['total'] or Decimal('0.00')

    amount_due = invoice.total_due - total_paid

    if amount_due <= 0:
        return JsonResponse({
            'success': False,
            'error': 'No pending balance for this invoice.'
        })

    try:
        from ..mpesa import MpesaDarajaSandbox

        mpesa_client = MpesaDarajaSandbox()

        base_callback_url = settings.MPESA_CALLBACK_URL.rstrip('/')
        dynamic_callback_url = f"{base_callback_url}{reverse('mpesa_webhook', args=[invoice.id])}"

        response = mpesa_client.initiate_stk_push(
            phone_number=phone_number,
            amount=int(amount_due),
            account_reference=invoice.invoice_number,
            transaction_desc=f"Payment for {invoice.invoice_number}",
            callback_url=dynamic_callback_url
        )

        if response.get('success'):
            return JsonResponse({
                'success': True,
                'message': response.get(
                    'response_description',
                    'STK Push sent successfully. Check your phone.'
                ),
                'checkout_request_id': response.get('checkout_request_id'),
                'merchant_request_id': response.get('merchant_request_id'),
            })

        return JsonResponse({
            'success': False,
            'error': response.get('error', 'M-Pesa API error'),
            'response_code': response.get('response_code')
        })

    except Exception as e:
        logger.exception(f"M-Pesa push failed for invoice {invoice.id}: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'M-Pesa request failed: {str(e)}'
        })

@csrf_exempt
def mpesa_callback(request):
    """
    M-Pesa callback endpoint.
    Receives payment confirmation from Safaricom.
    This endpoint is called by M-Pesa servers after payment.
    """
    if request.method == 'POST':
        try:
            # Get callback data
            callback_data = json.loads(request.body)
            
            # Process the callback
            from ..mpesa import process_mpesa_callback
            result = process_mpesa_callback(callback_data)
            
            if result.get('success'):
                # Payment successful - auto-record payment
                # Extract account reference (invoice number)
                account_reference = callback_data.get('Body', {}).get('stkCallback', {}).get('AccountReference')
                
                if account_reference:
                    try:
                        # Find the invoice
                        invoice = Invoice.objects.get(invoice_number=account_reference)
                        
                        # Create payment record
                        Payment.objects.create(
                            invoice=invoice,
                            payment_date=timezone.now().date(),
                            amount_paid=result.get('amount'),
                            payment_method='MPESA',
                            mpesa_reference=result.get('mpesa_receipt'),
                            mpesa_phone=result.get('phone_number'),
                            notes='Auto-recorded from M-Pesa callback',
                            recorded_by=None  # System-generated
                        )
                        
                        # Note: Invoice status updates automatically via Payment.save()
                        
                    except Invoice.DoesNotExist:
                        logger.error(
                            f"CRITICAL: Unallocated M-Pesa Payment. "
                            f"Ref: {result.get('mpesa_receipt')}, "
                            f"Amount: KES {result.get('amount')}, "
                            f"Phone: {result.get('phone_number')}, "
                            f"Attempted Invoice: {account_reference}"
                        )  # Invoice not found, log this in production
            
            # Always return success to M-Pesa
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Success'})
        
        except Exception as e:
            # Log error but still return success to M-Pesa
            print(f"M-Pesa callback error: {str(e)}")
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Success'})
    
    return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Invalid request'}, status=400)

@csrf_exempt
def mpesa_webhook(request, invoice_id):
    """
    Receives the payment confirmation from Safaricom.
    Because we append the invoice_id to the URL, we know exactly who paid!
    """
    if request.method == 'POST':
        try:
            # Parse Safaricom's JSON payload
            callback_data = json.loads(request.body)
            print("\n=== SAFARICOM WEBHOOK RECEIVED ===")
            print(json.dumps(callback_data, indent=2))
            print("==================================\n")
            
            result = process_mpesa_callback(callback_data)
            
            if result.get('success'):
                amount_paid = Decimal(str(result['amount']))
                mpesa_receipt = result['mpesa_receipt']
                phone_used = result['phone_number']
                
                # Fetch the invoice
                invoice = Invoice.objects.get(id=invoice_id)
                tenant = invoice.tenant
                
                # Prevent duplicate processing of the same receipt
                if Payment.objects.filter(mpesa_reference=mpesa_receipt).exists():
                    return HttpResponse('Already Processed', status=200)

                with transaction.atomic():
                    # 1. Create the Payment Record
                    payment = Payment.objects.create(
                        invoice=invoice,
                        amount_paid=amount_paid,
                        payment_date=timezone.now().date(),
                        payment_method='MPESA',
                        mpesa_reference=mpesa_receipt,
                        mpesa_phone=phone_used,
                        notes=f"Automated STK Push Payment. Receipt: {mpesa_receipt}"
                    )
                    
                    # 2. Update the Account Balance safely
                    account_balance, _ = AccountBalance.objects.select_for_update().get_or_create(
                        tenant=tenant,
                        defaults={'current_balance': Decimal('0.00')}
                    )
                    account_balance.current_balance -= amount_paid
                    account_balance.save()
                    recalculate_tenant_ledger(tenant)

                # 3. Send automated receipt (Outside atomic block)
                try:
                    email_notifier = PaymentNotification() # Assuming from email_utils
                    email_notifier.send_payment_confirmation(payment)
                except Exception as e:
                    logger.error(f"Failed to send email receipt for M-Pesa payment {mpesa_receipt}: {e}")

            # Always return a 200 OK so Safaricom knows we received the message
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})
            
        except Exception as e:
            logger.error(f"M-Pesa Webhook Error: {str(e)}")
            return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Failed'}, status=500)

    # Reject GET requests
    return HttpResponse('Method Not Allowed', status=405)
