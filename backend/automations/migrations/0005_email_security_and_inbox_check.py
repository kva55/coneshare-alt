from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('automations', '0004_generic_webhooks_and_email_settings'),
    ]

    operations = [
        migrations.AddField(model_name='emailsettings', name='allow_insecure', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='emailsettings', name='imap_host', field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name='emailsettings', name='imap_port', field=models.PositiveIntegerField(default=993)),
        migrations.AddField(model_name='emailsettings', name='imap_use_ssl', field=models.BooleanField(default=True)),
        migrations.AddField(model_name='emailsettings', name='imap_username', field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name='emailsettings', name='imap_password_encrypted', field=models.TextField(blank=True)),
        migrations.AddField(model_name='emailsettings', name='imap_test_address', field=models.EmailField(blank=True, max_length=254)),
    ]
