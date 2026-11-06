from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import core.fields


def _base():
    return [
        ('id', core.fields.ULIDField(default=core.fields.generate_ulid, editable=False, max_length=26, primary_key=True, serialize=False, unique=True, validators=[core.fields.validate_ulid])),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
    ]


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0011_english_only_user_language'),
        ('datarooms', '0009_remove_banner_gallery_and_brand_colors'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CheckoutPolicy',
            fields=_base() + [
                ('enabled', models.BooleanField(default=False)),
                ('allowed_domains', models.JSONField(blank=True, default=list)),
                ('checkout_minutes', models.PositiveSmallIntegerField(default=5)),
                ('max_attempts', models.PositiveSmallIntegerField(default=3)),
                ('downloads_per_checkout', models.PositiveSmallIntegerField(default=1)),
                ('notify_mode', models.CharField(choices=[('log', 'Internal log only'), ('owner', 'Email the document owner'), ('group', 'Email a group address')], default='log', max_length=10)),
                ('notify_email', models.EmailField(blank=True, max_length=254)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('dataroom', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='checkout_policy', to='datarooms.dataroom')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='checkout_policies', to='core.organization')),
            ],
            options={'ordering': ['dataroom__name']},
        ),
        migrations.CreateModel(
            name='CheckoutFile',
            fields=_base() + [
                ('dataroom_document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='datarooms.dataroomdocument')),
                ('policy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='files', to='checkout.checkoutpolicy')),
            ],
            options={'ordering': ['created_at']},
        ),
        migrations.AddConstraint(
            model_name='checkoutfile',
            constraint=models.UniqueConstraint(fields=('policy', 'dataroom_document'), name='unique_checkout_file'),
        ),
        migrations.CreateModel(
            name='CheckoutAuthCode',
            fields=_base() + [
                ('email', models.EmailField(max_length=254)),
                ('token_hash', models.CharField(max_length=64)),
                ('expires_at', models.DateTimeField()),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('failed_attempts', models.PositiveSmallIntegerField(default=0)),
                ('requested_ip', models.GenericIPAddressField(blank=True, null=True)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='checkoutauthcode',
            index=models.Index(fields=['email', '-created_at'], name='co_authcode_email_idx'),
        ),
        migrations.CreateModel(
            name='CheckoutSession',
            fields=_base() + [
                ('email', models.EmailField(max_length=254)),
                ('expires_at', models.DateTimeField()),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='Checkout',
            fields=_base() + [
                ('email', models.EmailField(max_length=254)),
                ('expires_at', models.DateTimeField()),
                ('downloads', models.PositiveSmallIntegerField(default=0)),
                ('max_downloads', models.PositiveSmallIntegerField(default=1)),
                ('last_download_at', models.DateTimeField(blank=True, null=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('file', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='checkouts', to='checkout.checkoutfile')),
                ('policy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='checkouts', to='checkout.checkoutpolicy')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='checkouts', to='checkout.checkoutsession')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='checkout',
            index=models.Index(fields=['policy', 'email', '-created_at'], name='co_checkout_pol_em_idx'),
        ),
        migrations.CreateModel(
            name='CheckoutEvent',
            fields=[
                ('id', core.fields.ULIDField(default=core.fields.generate_ulid, editable=False, max_length=26, primary_key=True, serialize=False, unique=True, validators=[core.fields.validate_ulid])),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('event_type', models.CharField(max_length=40)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.CharField(blank=True, max_length=512)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('policy', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='events', to='checkout.checkoutpolicy')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='checkoutevent',
            index=models.Index(fields=['-created_at'], name='co_event_time_idx'),
        ),
        migrations.AddIndex(
            model_name='checkoutevent',
            index=models.Index(fields=['event_type', '-created_at'], name='co_event_type_idx'),
        ),
        migrations.AddIndex(
            model_name='checkoutevent',
            index=models.Index(fields=['email', '-created_at'], name='co_event_email_idx'),
        ),
    ]
