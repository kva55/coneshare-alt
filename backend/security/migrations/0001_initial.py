from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import core.fields

ULID_ARGS = dict(default=core.fields.generate_ulid, editable=False, max_length=26, primary_key=True,
                 serialize=False, unique=True, validators=[core.fields.validate_ulid])


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0011_english_only_user_language'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SecuritySettings',
            fields=[
                ('id', core.fields.ULIDField(**ULID_ARGS)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code_requests_per_email', models.PositiveSmallIntegerField(default=5)),
                ('code_requests_per_ip', models.PositiveSmallIntegerField(default=30)),
                ('code_request_window_minutes', models.PositiveSmallIntegerField(default=15)),
                ('failed_auth_alerts_enabled', models.BooleanField(default=True)),
                ('failed_auth_threshold', models.PositiveSmallIntegerField(default=3)),
                ('failed_auth_window_minutes', models.PositiveSmallIntegerField(default=15)),
                ('alert_group_email', models.EmailField(blank=True, max_length=254)),
                ('alert_notify_account', models.BooleanField(default=True)),
                ('probe_detection_enabled', models.BooleanField(default=False)),
                ('probe_log_authenticated', models.BooleanField(default=True)),
                ('probe_log_unknown_areas', models.BooleanField(default=True)),
                ('probe_alert_enabled', models.BooleanField(default=False)),
                ('probe_alert_cooldown_minutes', models.PositiveSmallIntegerField(default=10)),
                ('probe_disabled_rules', models.JSONField(blank=True, default=list)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'abstract': False},
        ),
        migrations.CreateModel(
            name='SecurityEvent',
            fields=[
                ('id', core.fields.ULIDField(**ULID_ARGS)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('kind', models.CharField(max_length=40)),
                ('rule', models.CharField(blank=True, max_length=60)),
                ('method', models.CharField(blank=True, max_length=10)),
                ('path', models.CharField(blank=True, max_length=512)),
                ('query_excerpt', models.CharField(blank=True, max_length=200)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.CharField(blank=True, max_length=255)),
                ('authenticated', models.BooleanField(default=False)),
                ('actor', models.CharField(blank=True, max_length=254)),
                ('detail', models.JSONField(blank=True, default=dict)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='securityevent',
            index=models.Index(fields=['-created_at'], name='sec_event_time_idx'),
        ),
        migrations.AddIndex(
            model_name='securityevent',
            index=models.Index(fields=['kind', '-created_at'], name='sec_event_kind_idx'),
        ),
    ]
