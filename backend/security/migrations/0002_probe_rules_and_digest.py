from django.db import migrations, models

import security.models


def switch_off_unused_stacks(apps, schema_editor):
    """Existing settings rows had no rules switched off; start PHP and Java rules disabled there too."""
    from security.engine import DEFAULT_DISABLED
    SecuritySettings = apps.get_model('security', 'SecuritySettings')
    for row in SecuritySettings.objects.all():
        if not row.probe_disabled_rules:
            row.probe_disabled_rules = list(DEFAULT_DISABLED)
            row.save(update_fields=['probe_disabled_rules'])


class Migration(migrations.Migration):

    dependencies = [
        ('security', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='securitysettings',
            name='probe_disabled_rules',
            field=models.JSONField(blank=True, default=security.models.default_disabled_rules),
        ),
        migrations.AddField(
            model_name='securityevent',
            name='alerted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(switch_off_unused_stacks, migrations.RunPython.noop),
    ]
