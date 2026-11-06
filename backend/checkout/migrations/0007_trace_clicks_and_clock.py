import django_cryptography.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('checkout', '0006_trace_text_columns'),
    ]

    operations = [
        migrations.AddField(model_name='checkouttracechunk', name='clicks_data',
                            field=django_cryptography.fields.encrypt(models.TextField(default='[]'))),
        migrations.AddField(model_name='checkouttracechunk', name='client_now',
                            field=models.BigIntegerField(blank=True, null=True)),
    ]
