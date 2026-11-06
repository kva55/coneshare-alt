from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('checkout', '0002_guests_links_nda_approval'),
    ]

    operations = [
        migrations.AddField(model_name='checkoutfile', name='visible', field=models.BooleanField(default=True)),
        migrations.AddField(model_name='checkoutfile', name='requires_nda', field=models.BooleanField(blank=True, null=True)),
        migrations.AddField(model_name='checkoutfile', name='allow_download', field=models.BooleanField(blank=True, null=True)),
    ]
