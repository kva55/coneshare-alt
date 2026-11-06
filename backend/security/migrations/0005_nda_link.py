from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('security', '0004_login_notice_and_tracing'),
    ]

    operations = [
        migrations.AddField(model_name='securitysettings', name='nda_link_url',
                            field=models.CharField(blank=True, default='', max_length=500)),
        migrations.AddField(model_name='securitysettings', name='nda_link_text',
                            field=models.CharField(blank=True, default='Acceptable Use Policy (AUP)', max_length=120)),
    ]
