from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sharelinks', '0014_allowlist_access_and_events'),
    ]

    operations = [
        migrations.AddField(
            model_name='sharelink',
            name='blur_when_unfocused',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='sharelink',
            name='restricted_viewing_pane',
            field=models.BooleanField(default=False),
        ),
    ]
