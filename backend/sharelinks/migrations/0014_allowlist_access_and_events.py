from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('sharelinks', '0013_sharelinkdataroomsetting_unique_sharelink_dataroom_document_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ShareLinkAllowedEmail',
            fields=[
                ('id', core.fields.ULIDField(default=core.fields.generate_ulid, editable=False, max_length=26, primary_key=True, serialize=False, unique=True, validators=[core.fields.validate_ulid])),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('email', models.EmailField(max_length=254)),
                ('invitation_sent_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('invited_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('share_link', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='allowed_emails', to='sharelinks.sharelink')),
            ],
            options={'ordering': ['created_at']},
        ),
        migrations.AddConstraint(
            model_name='sharelinkallowedemail',
            constraint=models.UniqueConstraint(fields=('share_link', 'email'), name='unique_sharelink_allowed_email'),
        ),
        migrations.CreateModel(
            name='AccessRequestToken',
            fields=[
                ('id', core.fields.ULIDField(default=core.fields.generate_ulid, editable=False, max_length=26, primary_key=True, serialize=False, unique=True, validators=[core.fields.validate_ulid])),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('email', models.EmailField(max_length=254)),
                ('token_hash', models.CharField(max_length=64)),
                ('expires_at', models.DateTimeField()),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('failed_attempts', models.PositiveSmallIntegerField(default=0)),
                ('requested_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('share_link', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='access_request_tokens', to='sharelinks.sharelink')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='accessrequesttoken',
            index=models.Index(fields=['share_link', 'email', '-created_at'], name='sl_acctoken_lnk_em_idx'),
        ),
        migrations.CreateModel(
            name='ShareLinkEvent',
            fields=[
                ('id', core.fields.ULIDField(default=core.fields.generate_ulid, editable=False, max_length=26, primary_key=True, serialize=False, unique=True, validators=[core.fields.validate_ulid])),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('event_type', models.CharField(db_index=True, max_length=40)),
                ('email', models.EmailField(blank=True, default='', max_length=254)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.CharField(blank=True, default='', max_length=512)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('share_link', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='sharelinks.sharelink')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='sharelinkevent',
            index=models.Index(fields=['share_link', '-created_at'], name='sl_event_link_time_idx'),
        ),
        migrations.AddIndex(
            model_name='sharelinkevent',
            index=models.Index(fields=['share_link', 'event_type', '-created_at'], name='sl_event_link_type_idx'),
        ),
    ]
