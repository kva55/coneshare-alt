from django.conf import settings
from django.db import models

from core.models import BaseModel


class RetentionSettings(BaseModel):
    """
    Deployment-wide data retention policy (a single row, edited under Admin > Data retention).
    A file's own ``delete_after`` date always wins over this policy; files without one follow it.
    """
    MODE_OFF, MODE_AFTER_UPLOAD, MODE_YEARLY = 'off', 'after_upload', 'yearly'
    MODE_CHOICES = [(MODE_OFF, 'No automatic deletion'),
                    (MODE_AFTER_UPLOAD, 'Delete a set time after upload'),
                    (MODE_YEARLY, 'Delete everything on a date every year')]
    UNIT_CHOICES = [('days', 'days'), ('weeks', 'weeks'), ('months', 'months'), ('years', 'years')]

    mode = models.CharField(max_length=20, choices=MODE_CHOICES, default=MODE_OFF)
    after_value = models.PositiveSmallIntegerField(default=1)
    after_unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default='years')
    # "Everything is deleted every January": on this day each year, every file uploaded before it goes.
    yearly_month = models.PositiveSmallIntegerField(default=1)
    yearly_day = models.PositiveSmallIntegerField(default=1)
    # Overwrite stored data before it is unlinked (NIST SP 800-88 Rev. 1 "Clear": one pass). On by default.
    overwrite = models.BooleanField(default=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    @classmethod
    def load(cls):
        return cls.objects.order_by('created_at').first() or cls()


class RetentionLog(BaseModel):
    """What the retention job deleted. Holds the name and the reason, never any content."""
    REASON_FILE, REASON_AFTER_UPLOAD, REASON_YEARLY = 'file_date', 'policy_after_upload', 'policy_yearly'

    document_id = models.CharField(max_length=26, db_index=True)
    document_name = models.CharField(max_length=255)
    organization_id = models.CharField(max_length=26, blank=True)
    reason = models.CharField(max_length=30)
    overwritten = models.BooleanField(default=False)
    ok = models.BooleanField(default=True)
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ['-created_at']
