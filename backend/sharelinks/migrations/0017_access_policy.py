from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django_cryptography.fields

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('sharelinks', '0016_allowed_domains'),
        ('documents', '0010_add_storage_key_indexes'),
        ('datarooms', '0009_remove_banner_gallery_and_brand_colors'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AccessPolicy',
            fields=[
                ('id', core.fields.ULIDField(default=core.fields.generate_ulid, editable=False, max_length=26, primary_key=True, serialize=False, unique=True, validators=[core.fields.validate_ulid])),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('password', django_cryptography.fields.encrypt(models.CharField(blank=True, max_length=255, null=True))),
                ('requires_email', models.BooleanField(default=False)),
                ('requires_email_verification', models.BooleanField(default=False)),
                ('require_nda', models.BooleanField(default=False)),
                ('nda_text', models.TextField(blank=True, default='')),
                ('nda_version', models.IntegerField(default=1)),
                ('allow_download', models.BooleanField(blank=True, null=True)),
                ('enable_watermark', models.BooleanField(default=False)),
                ('watermark_text', models.CharField(blank=True, max_length=255)),
                ('blur_when_unfocused', models.BooleanField(default=False)),
                ('restricted_viewing_pane', models.BooleanField(default=False)),
                ('allowed_emails', models.JSONField(blank=True, default=list)),
                ('allowed_domains', models.JSONField(blank=True, default=list)),
                ('dataroom', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='access_policy', to='datarooms.dataroom')),
                ('document', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='access_policy', to='documents.document')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='accesspolicy',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('dataroom__isnull', True), ('document__isnull', False)), models.Q(('dataroom__isnull', False), ('document__isnull', True)), _connector='OR'), name='accesspolicy_exactly_one_target'),
        ),
    ]
