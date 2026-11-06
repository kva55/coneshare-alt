from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('security', '0002_probe_rules_and_digest'),
    ]

    operations = [
        migrations.AddField(model_name='securitysettings', name='throttling_enabled', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='securitysettings', name='user_requests_per_minute', field=models.PositiveIntegerField(default=600)),
        migrations.AddField(model_name='securitysettings', name='viewer_requests_per_minute', field=models.PositiveIntegerField(default=1500)),
        migrations.AddField(model_name='securitysettings', name='general_requests_per_minute', field=models.PositiveIntegerField(default=300)),
        migrations.AddField(model_name='securitysettings', name='capture_actions', field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name='securitysettings', name='capture_lockout_minutes', field=models.PositiveSmallIntegerField(default=5)),
    ]
