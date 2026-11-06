from django.contrib.auth.signals import user_login_failed
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import SecuritySettings
from .services import clear_cache, report_failed_auth


@receiver(post_save, sender=SecuritySettings)
def _clear_settings_cache(sender, **kwargs):
    clear_cache()


@receiver(user_login_failed)
def _count_failed_login(sender, credentials=None, request=None, **kwargs):
    credentials = credentials or {}
    identifier = credentials.get('email') or credentials.get('username') or ''
    if not identifier:
        return
    from core.models import User
    account = User.objects.filter(email__iexact=identifier, is_active=True).first()
    report_failed_auth('login', identifier, request, account_email=account.email if account else '')
