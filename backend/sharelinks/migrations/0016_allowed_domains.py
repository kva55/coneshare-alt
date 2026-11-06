from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('sharelinks', '0015_experimental_viewing_protections'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ShareLinkAllowedDomain',
            fields=[
                ('id', core.fields.ULIDField(default=core.fields.generate_ulid, editable=False, max_length=26, primary_key=True, serialize=False, unique=True, validators=[core.fields.validate_ulid])),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('domain', models.CharField(max_length=253)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('invited_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('share_link', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='allowed_domains', to='sharelinks.sharelink')),
            ],
            options={'ordering': ['created_at']},
        ),
        migrations.AddConstraint(
            model_name='sharelinkalloweddomain',
            constraint=models.UniqueConstraint(fields=('share_link', 'domain'), name='unique_sharelink_allowed_domain'),
        ),
    ]
