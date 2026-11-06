"""Replace the rule/destination engine (Slack, Discord, WeChat, FeiShu, ...) with a single generic
webhook plus UI-managed e-mail settings. The old tables held delivery logs and rules only."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import core.fields



def _base():
    return [
        ('id', core.fields.ULIDField(default=core.fields.generate_ulid, editable=False, max_length=26,
                                     primary_key=True, serialize=False, unique=True,
                                     validators=[core.fields.validate_ulid])),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
    ]


class Migration(migrations.Migration):

    dependencies = [
        ('automations', '0003_alter_automationdelivery_status'),
        ('core', '0011_english_only_user_language'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.DeleteModel(name='AutomationAssignment'),
        migrations.DeleteModel(name='AutomationDelivery'),
        migrations.DeleteModel(name='AutomationRule'),
        migrations.DeleteModel(name='AutomationDestination'),
        migrations.CreateModel(
            name='WebhookEndpoint',
            fields=_base() + [
                ('name', models.CharField(max_length=255)),
                ('url', models.URLField(max_length=2048)),
                ('events', models.JSONField(blank=True, default=list)),
                ('is_active', models.BooleanField(default=True)),
                ('secret_version', models.PositiveIntegerField(default=1)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='webhook_endpoints_created', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='webhook_endpoints', to='core.organization')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='WebhookDelivery',
            fields=_base() + [
                ('event_type', models.CharField(max_length=100)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('success', 'Success'), ('failed', 'Failed (will retry)'), ('dead_letter', 'Gave up')], default='pending', max_length=20)),
                ('response_code', models.IntegerField(blank=True, null=True)),
                ('response_body_excerpt', models.TextField(blank=True)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('next_retry_at', models.DateTimeField(blank=True, null=True)),
                ('delivered_at', models.DateTimeField(blank=True, null=True)),
                ('idempotency_key', models.CharField(blank=True, max_length=100)),
                ('endpoint', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='automations.webhookendpoint')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='webhookdelivery',
            index=models.Index(fields=['endpoint', '-created_at'], name='wh_delivery_ep_time_idx'),
        ),
        migrations.CreateModel(
            name='EmailSettings',
            fields=_base() + [
                ('enabled', models.BooleanField(default=False)),
                ('host', models.CharField(blank=True, max_length=255)),
                ('port', models.PositiveIntegerField(default=587)),
                ('username', models.CharField(blank=True, max_length=255)),
                ('password_encrypted', models.TextField(blank=True)),
                ('use_tls', models.BooleanField(default=True)),
                ('use_ssl', models.BooleanField(default=False)),
                ('from_email', models.EmailField(blank=True, max_length=254)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
