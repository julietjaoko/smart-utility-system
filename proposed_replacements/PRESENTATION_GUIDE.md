# Smart Utility System Presentation Guide

## Replacement Files

These files were generated without directly changing your existing templates:

- `proposed_replacements/core/templates/core/base.html`
- `proposed_replacements/core/templates/core/manage_units.html`
- `proposed_replacements/core/templates/core/manage_tenants.html`
- `proposed_replacements/core/templates/core/manage_rates.html`
- `proposed_replacements/core/templates/core/unit_detail.html`
- `proposed_replacements/core/templates/core/add_unit.html`
- `proposed_replacements/core/templates/core/edit_unit.html`
- `proposed_replacements/core/templates/core/add_tenant.html`
- `proposed_replacements/core/templates/core/edit_tenant.html`
- `proposed_replacements/core/templates/core/add_rate.html`
- `proposed_replacements/core/templates/core/add_fixed_charge.html`

## What Changes These Files Give You

1. Sidebar starts at the same top level as the header.
2. Sidebar keeps the current system icon and system name.
3. Collapse button sits inside the sidebar.
4. Collapsed sidebar shows icons only.
5. Logout is moved to the bottom of the sidebar.
6. Header shows the system icon and name.
7. Header has a profile button with a dropdown for account management.
8. Add and edit actions for units, tenants, and rates open as popups on the current page.

## How The Popup Works

The new `base.html` opens form pages inside a modal iframe.

- When you click `Add Unit`, `Add Tenant`, `Edit Unit`, `Edit Tenant`, `Set Rate`, or `Add Charge`, the form opens on top of the current page.
- When the form saves successfully and redirects, the modal closes and the page reloads automatically.
- If the form has validation errors, the modal stays open so you can correct them.

## How To Test Before Presentation

### 1. General UI Flow

1. Log in as property manager.
2. Collapse and expand the sidebar.
3. Confirm that collapsed mode shows icons only.
4. Open the profile dropdown from the header.
5. Open `Units`, `Tenants`, and `Rates & Charges`.
6. Click the add and edit buttons and confirm they open in a popup instead of navigating away.

### 2. Email Testing

Your current settings file uses:

- `EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'`

That means emails are not sent to Gmail or Outlook yet. They print in the Django terminal.

Demo path:

1. Start the server.
2. Generate or send an invoice email from the app.
3. Show the email content in the terminal window.

If you want real email before presentation, switch the email backend in `utility_system/settings.py` to SMTP and add real credentials.

### 3. SMS Testing

Your project is currently configured for Africa's Talking sandbox:

- `AFRICASTALKING_USERNAME = 'sandbox'`
- SMS helper is in `core/sms_utils.py`

Demo path:

1. Make sure the tenant has a valid Kenyan test phone number format.
2. Trigger an SMS event:
   - send invoice reminder
   - record payment
   - add electricity token
3. If sandbox delivery is limited, show the workflow in the UI and explain that the integration is already wired to Africa's Talking sandbox.

Important:

- Phone numbers should be entered like `0712345678` or `254712345678`.
- The code converts local format to `+254...`.

### 4. M-Pesa Testing

Your project is wired to Safaricom Daraja sandbox:

- credentials are read in `core/mpesa.py`
- callback base is in `utility_system/settings.py`
- webhook endpoint is `mpesa/webhook/<invoice_id>/`

Demo path:

1. Make sure your `ngrok` URL is still active.
2. Confirm `MPESA_CALLBACK_URL` points to that live public URL.
3. Open an invoice that is not fully paid.
4. Trigger STK push from the app.
5. Use a Safaricom sandbox test number if you have one available.
6. Confirm payment records appear after callback.

What can break the demo:

- expired `ngrok` URL
- wrong callback URL
- missing sandbox credentials
- phone number not in the expected format

Important callback note:

- In your current `utility_system/settings.py`, `MPESA_CALLBACK_URL` already includes `/mpesa/webhook/5/`.
- In `core/views.py`, the app appends `reverse('mpesa_webhook', args=[invoice.id])` again.
- That can produce a broken callback like:
  `https://your-ngrok-url/mpesa/webhook/5/mpesa/webhook/12/`
- For the presentation, set `MPESA_CALLBACK_URL` to just the public base URL, for example:
  `https://your-ngrok-url`

### 5. Quick Demo Backup Plan

If external services fail tomorrow, still present confidently:

1. Show that tenant phone and email are stored.
2. Show the invoice send button and reminder flow.
3. Show the M-Pesa payment initiation button.
4. Explain:
   - email currently prints to console in development
   - SMS uses Africa's Talking sandbox
   - M-Pesa uses Daraja sandbox plus `ngrok` callback

## Strongly Recommended Cleanup Before Presentation

1. Remove hard-coded secrets from `utility_system/settings.py` and move them to environment variables only.
2. Fix odd success message encoding in `core/views.py` where some messages display as broken characters instead of checkmarks.
3. Add a proper `Manage Account` page later if you want more than password change.
4. Consider adding modal popup support to payment recording next, because that flow also fits the same pattern.

## Safe Order To Swap Files

1. Replace `base.html` first.
2. Replace `manage_units.html`, `manage_tenants.html`, `manage_rates.html`, and `unit_detail.html`.
3. Replace the add/edit form templates after that.
4. Reload the browser and test each popup once.
