"""
Data retention: find files that are due and hard-delete them.

A file is due when its own ``delete_after`` has passed, or, if it has none, when the global policy
says so. "Hard" means: the stored original, converted file and page images are removed from storage
(overwritten first when the policy asks for it), and the database rows are deleted, which also removes
the share links, access logs, check-outs and session-report data that belonged to the file.
"""
import calendar
import logging
from datetime import datetime, time, timedelta, timezone as dt_timezone

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from documents.models import Document

from .models import RetentionLog, RetentionSettings

logger = logging.getLogger(__name__)

LOCK_KEY = 'retention:run'


def _add_months(moment, months):
    index = moment.year * 12 + (moment.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def policy_cutoff(cfg, now):
    """
    Files without their own date are due if uploaded at or before the returned moment; None if the
    policy is off.
    """
    if cfg.mode == RetentionSettings.MODE_AFTER_UPLOAD:
        value = max(1, cfg.after_value)
        if cfg.after_unit == 'days':
            return now - timedelta(days=value)
        if cfg.after_unit == 'weeks':
            return now - timedelta(weeks=value)
        if cfg.after_unit == 'months':
            return _add_months(now, -value)
        return _add_months(now, -12 * value)
    if cfg.mode == RetentionSettings.MODE_YEARLY:
        month = min(max(cfg.yearly_month, 1), 12)
        day = min(max(cfg.yearly_day, 1), calendar.monthrange(now.year, month)[1])
        occurrence = datetime.combine(now.date().replace(month=month, day=day), time.min, tzinfo=dt_timezone.utc)
        if occurrence > now:   # this year's date has not come yet: the last one was a year ago
            year = now.year - 1
            day = min(max(cfg.yearly_day, 1), calendar.monthrange(year, month)[1])
            occurrence = datetime(year, month, day, tzinfo=dt_timezone.utc)
        return occurrence
    return None


def due_documents(now=None, cfg=None):
    """[(document, reason)], including documents that are in the trash."""
    now = now or timezone.now()
    cfg = cfg or RetentionSettings.load()
    due = {}
    for doc in Document.objects.filter(delete_after__isnull=False, delete_after__lte=now):
        due[doc.pk] = (doc, RetentionLog.REASON_FILE)
    cutoff = policy_cutoff(cfg, now)
    if cutoff is not None:
        reason = (RetentionLog.REASON_YEARLY if cfg.mode == RetentionSettings.MODE_YEARLY
                  else RetentionLog.REASON_AFTER_UPLOAD)
        # the yearly date removes files uploaded *before* it; the after-upload rule files that are old enough
        lookup = {'created_at__lt': cutoff} if cfg.mode == RetentionSettings.MODE_YEARLY else {'created_at__lte': cutoff}
        for doc in Document.objects.filter(delete_after__isnull=True, **lookup):
            due.setdefault(doc.pk, (doc, reason))
    return list(due.values())


def run(now=None):
    """Delete everything that is due. Returns (deleted, failed). Safe to call from several workers."""
    from documents.services import delete_document_and_files
    if not cache.add(LOCK_KEY, 1, 3600):
        return 0, 0
    deleted = failed = 0
    try:
        cfg = RetentionSettings.load()
        for doc, reason in due_documents(now, cfg):
            entry = dict(document_id=str(doc.pk), document_name=doc.name[:255],
                         organization_id=str(doc.organization_id), reason=reason, overwritten=cfg.overwrite)
            try:
                with transaction.atomic():
                    delete_document_and_files(doc, secure=cfg.overwrite)
                RetentionLog.objects.create(**entry)
                deleted += 1
            except Exception as exc:  # the document is kept and tried again on the next run
                logger.exception("Retention could not delete document %s", doc.pk)
                RetentionLog.objects.create(**entry, ok=False, error=str(exc)[:500])
                failed += 1
    finally:
        cache.delete(LOCK_KEY)
    return deleted, failed
