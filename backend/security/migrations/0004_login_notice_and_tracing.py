from django.db import migrations, models

import security.models


class Migration(migrations.Migration):

    dependencies = [
        ('security', '0003_request_limits_and_capture_actions'),
    ]

    operations = [
        migrations.AddField(
            model_name='securitysettings', name='monitoring_enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='securitysettings', name='login_notice_enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='securitysettings', name='login_notice_text',
            field=models.TextField(blank=True, default=security.models.DEFAULT_LOGIN_NOTICE),
        ),
        migrations.AddField(
            model_name='securitysettings', name='checkout_tracing_enabled',
            field=models.BooleanField(default=True),
        ),
    ]
