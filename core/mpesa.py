"""
M-Pesa Daraja Sandbox Integration
Handles STK Push and payment confirmation callbacks.
"""

import base64
import json
from datetime import datetime

import requests
from django.conf import settings


MPESA_RESULT_MESSAGES = {
    0: 'Payment completed successfully.',
    1: 'Payment could not be completed. Please try again.',
    1032: 'Payment was cancelled on the phone.',
    1037: 'M-Pesa prompt timed out. Please try again.',
    2001: 'Wrong M-Pesa PIN entered. Please try again.',
}


def readable_mpesa_message(value, default='M-Pesa request failed. Please try again.'):
    """Return a user-safe sentence from Daraja text, JSON, or result codes."""
    if isinstance(value, dict):
        for key in ('ResultCode', 'result_code'):
            if key in value:
                message = readable_mpesa_message(value[key], default='')
                if message:
                    return message
        for key in ('errorMessage', 'ResultDesc', 'ResponseDescription', 'error_description', 'error'):
            if value.get(key):
                return readable_mpesa_message(value[key], default=default)
        return default

    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return default
        if cleaned.startswith('{') or cleaned.startswith('['):
            try:
                return readable_mpesa_message(json.loads(cleaned), default=default)
            except ValueError:
                return default
        return cleaned

    if isinstance(value, int) and value in MPESA_RESULT_MESSAGES:
        return MPESA_RESULT_MESSAGES[value]

    if isinstance(value, str) and value.isdigit():
        code = int(value)
        if code in MPESA_RESULT_MESSAGES:
            return MPESA_RESULT_MESSAGES[code]

    return default


class MpesaDarajaSandbox:
    def __init__(self):
        self.consumer_key = getattr(settings, 'MPESA_CONSUMER_KEY', '')
        self.consumer_secret = getattr(settings, 'MPESA_CONSUMER_SECRET', '')
        self.business_short_code = getattr(settings, 'MPESA_SHORTCODE', '174379')
        self.passkey = getattr(settings, 'MPESA_PASSKEY', '')
        self.callback_url = getattr(settings, 'MPESA_CALLBACK_URL', '')

        self.auth_url = 'https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials'
        self.stk_push_url = 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
        self.stk_query_url = 'https://sandbox.safaricom.co.ke/mpesa/stkpushquery/v1/query'

    def get_access_token(self):
        if not self.consumer_key or not self.consumer_secret:
            return {
                'success': False,
                'error': 'M-Pesa consumer key/secret is missing in settings.',
            }

        try:
            response = requests.get(
                self.auth_url,
                auth=(self.consumer_key, self.consumer_secret),
                timeout=30,
            )

            try:
                result = response.json()
            except ValueError:
                result = {}

            if response.status_code == 200 and result.get('access_token'):
                return {
                    'success': True,
                    'access_token': result['access_token'],
                }

            return {
                'success': False,
                'error': readable_mpesa_message(result or response.text, 'Could not authenticate with M-Pesa. Please try again.'),
                'response': result,
            }

        except requests.RequestException as exc:
            return {
                'success': False,
                'error': f'Authentication request failed: {exc}',
            }

    def initiate_stk_push(self, phone_number, amount, account_reference, transaction_desc, callback_url=None):
        token_result = self.get_access_token()
        if not token_result.get('success'):
            return token_result

        if not self.passkey:
            return {
                'success': False,
                'error': 'M-Pesa passkey is missing in settings.',
            }

        callback = callback_url or self.callback_url
        if not callback:
            return {
                'success': False,
                'error': 'M-Pesa callback URL is missing.',
            }

        try:
            phone_number = self.format_phone_number(phone_number)
        except ValueError as exc:
            return {
                'success': False,
                'error': str(exc),
            }

        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password_str = f"{self.business_short_code}{self.passkey}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode()

        payload = {
            'BusinessShortCode': self.business_short_code,
            'Password': password,
            'Timestamp': timestamp,
            'TransactionType': 'CustomerPayBillOnline',
            'Amount': int(amount),
            'PartyA': phone_number,
            'PartyB': self.business_short_code,
            'PhoneNumber': phone_number,
            'CallBackURL': callback,
            'AccountReference': account_reference,
            'TransactionDesc': transaction_desc,
        }

        headers = {
            'Authorization': f"Bearer {token_result['access_token']}",
            'Content-Type': 'application/json',
        }

        try:
            response = requests.post(
                self.stk_push_url,
                json=payload,
                headers=headers,
                timeout=30,
            )

            try:
                result = response.json()
            except ValueError:
                return {
                    'success': False,
                    'error': 'M-Pesa returned an unreadable response. Please try again.',
                    'raw_response': response.text,
                }

            if response.status_code == 200 and result.get('ResponseCode') == '0':
                return {
                    'success': True,
                    'checkout_request_id': result.get('CheckoutRequestID'),
                    'merchant_request_id': result.get('MerchantRequestID'),
                    'response_description': result.get('ResponseDescription'),
                    'customer_message': result.get('CustomerMessage'),
                    'response_code': result.get('ResponseCode'),
                }

            return {
                'success': False,
                'error': readable_mpesa_message(result or response.text),
                'response_code': result.get('ResponseCode'),
                'response': result,
            }

        except requests.RequestException as exc:
            return {
                'success': False,
                'error': f'STK push request failed: {exc}',
            }

    def query_stk_status(self, checkout_request_id):
        token_result = self.get_access_token()
        if not token_result.get('success'):
            return token_result

        if not self.passkey:
            return {
                'success': False,
                'error': 'M-Pesa passkey is missing in settings.',
            }

        if not checkout_request_id:
            return {
                'success': False,
                'error': 'Checkout request ID is required.',
            }

        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password_str = f"{self.business_short_code}{self.passkey}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode()

        payload = {
            'BusinessShortCode': self.business_short_code,
            'Password': password,
            'Timestamp': timestamp,
            'CheckoutRequestID': checkout_request_id,
        }
        headers = {
            'Authorization': f"Bearer {token_result['access_token']}",
            'Content-Type': 'application/json',
        }

        try:
            response = requests.post(
                self.stk_query_url,
                json=payload,
                headers=headers,
                timeout=15,
            )

            try:
                result = response.json()
            except ValueError:
                return {
                    'success': False,
                    'status': 'FAILED',
                    'error': 'M-Pesa returned an unreadable status response. Please try again.',
                    'raw_response': response.text,
                }

            result_code = result.get('ResultCode')
            response_code = result.get('ResponseCode')
            result_desc = result.get('ResultDesc') or result.get('ResponseDescription') or ''

            if response.status_code == 200 and response_code == '0':
                if result_code in (0, '0'):
                    return {
                        'success': True,
                        'status': 'PAID',
                        'result_code': result_code,
                        'result_desc': readable_mpesa_message(result_code, result_desc or 'Payment completed successfully.'),
                        'response': result,
                    }

                if result_code is not None:
                    return {
                        'success': False,
                        'status': 'FAILED',
                        'result_code': result_code,
                        'result_desc': readable_mpesa_message(result_code, result_desc or 'Payment was not completed.'),
                        'response': result,
                    }

                return {
                    'success': True,
                    'status': 'PENDING',
                    'result_desc': result_desc or 'Payment is still pending.',
                    'response': result,
                }

            return {
                'success': False,
                'status': 'FAILED',
                'response_code': response_code,
                'error': readable_mpesa_message(result or result_desc or response.text),
                'response': result,
            }

        except requests.RequestException as exc:
            return {
                'success': False,
                'status': 'FAILED',
                'error': f'STK status query failed: {exc}',
            }

    def format_phone_number(self, phone_number):
        phone_number = (phone_number or '').strip().replace(' ', '').replace('+', '')

        if phone_number.startswith('0') and len(phone_number) == 10:
            return '254' + phone_number[1:]

        if phone_number.startswith('254') and len(phone_number) == 12:
            return phone_number

        raise ValueError('Phone number must be in format 07XXXXXXXX or 2547XXXXXXXX.')


def process_mpesa_callback(callback_data):
    try:
        stk_callback = callback_data.get('Body', {}).get('stkCallback', {})
        result_code = stk_callback.get('ResultCode')

        if result_code == 0:
            callback_metadata = stk_callback.get('CallbackMetadata', {}).get('Item', [])

            payment_details = {}
            for item in callback_metadata:
                name = item.get('Name')
                value = item.get('Value')
                payment_details[name] = value

            return {
                'success': True,
                'amount': payment_details.get('Amount'),
                'mpesa_receipt': payment_details.get('MpesaReceiptNumber'),
                'phone_number': payment_details.get('PhoneNumber'),
                'transaction_date': payment_details.get('TransactionDate'),
            }

        return {
            'success': False,
            'result_code': result_code,
            'result_desc': readable_mpesa_message(result_code, stk_callback.get('ResultDesc') or 'Payment was not completed.'),
        }

    except Exception as exc:
        return {
            'success': False,
            'error': f'Error processing callback: {exc}',
        }
