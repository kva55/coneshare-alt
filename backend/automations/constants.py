"""Events the generic webhook can subscribe to, and which events send the "viewed" email."""

# Subscribable webhook events -> short description shown in the UI.
WEBHOOK_EVENTS = {
    'file_requested': 'A signed-in visitor asked to check a file out',
    'file_checked_out': 'A file check-out was granted',
    'checkout_denied': 'A check-out was refused (attempt limit or policy)',
    'checkout_approval_required': 'A check-out request is waiting for admin approval',
    'access_granted': 'A visitor passed the share-link allow-list and was let in',
    'document_viewed': 'Someone viewed a shared document',
}

# Events that trigger "email me when someone views my document" (when the link has it switched on).
VIEW_EMAIL_EVENTS = ('document_viewed', 'dataroom_opened', 'document_downloaded')

# At most one "viewed" email per link, visitor and event within this window.
VIEW_EMAIL_DEDUPE_SECONDS = 600

MAX_DELIVERY_ATTEMPTS = 3
BASE_RETRY_SECONDS = 60
RESPONSE_EXCERPT_LIMIT = 200
MAX_WEBHOOKS_PER_ORG = 20

# Keys that never leave the server in a webhook body.
INTERNAL_PAYLOAD_KEYS = ('owner_user_id', 'dataroom_owner_user_id', 'organization_id')
