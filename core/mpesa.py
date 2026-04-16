"""
M-Pesa Daraja Sandbox Integration
Handles STK Push and payment confirmation callbacks.
"""

import requests
import base64
from datetime import datetime
from django.conf import settings
import json

class MpesaDarajaSandbox:
    """
    M-Pesa Daraja API wrapper for sandbox environment.
    Handles authentication and STK Push requests.
    """
    
    def __init__(self):
        """
        Initialize M-Pesa credentials.
        These should be stored in settings.py or environment variables.
        """
        # Sandbox credentials (replace with your actual sandbox credentials)
        self.consumer_key = getattr(settings, 'MPESA_CONSUMER_KEY', 'your_consumer_key_here')
        self.consumer_secret = getattr(settings, 'MPESA_CONSUMER_SECRET', 'your_consumer_secret_here')
        self.business_short_code = getattr(settings, 'MPESA_SHORTCODE', '174379')
        self.passkey = getattr(settings, 'MPESA_PASSKEY', 'bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919')
        self.callback_url = getattr(settings, 'MPESA_CALLBACK_URL', 'https://yourdomain.com/mpesa/callback/')
        
        # Sandbox URLs
        self.auth_url = 'https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials'
        self.stk_push_url = 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
    
    def get_access_token(self):
        """
        Generate OAuth access token for API authentication.
        Token is valid for 1 hour.
        
        Returns:
            str: Access token or None if failed
        """
        try:
            # Create basic auth credentials
            credentials = f"{self.consumer_key}:{self.consumer_secret}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            
            headers = {
                'Authorization': f'Basic {encoded_credentials}'
            }
            
            response = requests.get(self.auth_url, headers=headers)
            
            if response.status_code == 200:
                result = response.json()
                return result.get('access_token')
            else:
                print(f"Error getting access token: {response.text}")
                return None
        
        except Exception as e:
            print(f"Exception in get_access_token: {str(e)}")
            return None
    
    def initiate_stk_push(self, phone_number, amount, account_reference, transaction_desc):
        """
        Initiate STK Push (Lipa Na M-Pesa Online) payment request.
        
        Args:
            phone_number (str): Phone number in format 254XXXXXXXXX
            amount (int): Amount to be paid
            account_reference (str): Invoice number or reference
            transaction_desc (str): Description of transaction
        
        Returns:
            dict: Response from M-Pesa API or error details
        """
        try:
            # Get access token
            access_token = self.get_access_token()
            if not access_token:
                return {'success': False, 'error': 'Failed to get access token'}
            
            # Format phone number (remove + and spaces)
            phone_number = phone_number.replace('+', '').replace(' ', '')
            if phone_number.startswith('0'):
                phone_number = '254' + phone_number[1:]
            
            # Generate timestamp
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            
            # Generate password (Base64 of Shortcode + Passkey + Timestamp)
            password_str = f"{self.business_short_code}{self.passkey}{timestamp}"
            password = base64.b64encode(password_str.encode()).decode()
            
            # Prepare request headers
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            # Prepare request payload
            payload = {
                'BusinessShortCode': self.business_short_code,
                'Password': password,
                'Timestamp': timestamp,
                'TransactionType': 'CustomerPayBillOnline',
                'Amount': int(amount),
                'PartyA': phone_number,
                'PartyB': self.business_short_code,
                'PhoneNumber': phone_number,
                'CallBackURL': self.callback_url,
                'AccountReference': account_reference,
                'TransactionDesc': transaction_desc
            }
            
            # Make request
            response = requests.post(
                self.stk_push_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            
            result = response.json()
            
            if response.status_code == 200 and result.get('ResponseCode') == '0':
                return {
                    'success': True,
                    'checkout_request_id': result.get('CheckoutRequestID'),
                    'merchant_request_id': result.get('MerchantRequestID'),
                    'response_description': result.get('ResponseDescription')
                }
            else:
                return {
                    'success': False,
                    'error': result.get('errorMessage') or result.get('ResponseDescription'),
                    'response_code': result.get('ResponseCode')
                }
        
        except Exception as e:
            return {
                'success': False,
                'error': f'Exception occurred: {str(e)}'
            }


def process_mpesa_callback(callback_data):
    """
    Process M-Pesa callback after STK Push completion.
    This function is called when M-Pesa sends payment confirmation.
    
    Args:
        callback_data (dict): Callback data from M-Pesa
    
    Returns:
        dict: Processing result
    """
    try:
        # Extract callback data
        result_code = callback_data.get('Body', {}).get('stkCallback', {}).get('ResultCode')
        
        # Check if payment was successful
        if result_code == 0:
            # Payment successful
            callback_metadata = callback_data.get('Body', {}).get('stkCallback', {}).get('CallbackMetadata', {}).get('Item', [])
            
            # Extract payment details
            payment_details = {}
            for item in callback_metadata:
                name = item.get('Name')
                value = item.get('Value')
                payment_details[name] = value
            
            # Extract key information
            amount = payment_details.get('Amount')
            mpesa_receipt = payment_details.get('MpesaReceiptNumber')
            phone_number = payment_details.get('PhoneNumber')
            transaction_date = payment_details.get('TransactionDate')
            
            return {
                'success': True,
                'amount': amount,
                'mpesa_receipt': mpesa_receipt,
                'phone_number': phone_number,
                'transaction_date': transaction_date
            }
        else:
            # Payment failed
            return {
                'success': False,
                'result_code': result_code,
                'result_desc': callback_data.get('Body', {}).get('stkCallback', {}).get('ResultDesc')
            }
    
    except Exception as e:
        return {
            'success': False,
            'error': f'Error processing callback: {str(e)}'
        }