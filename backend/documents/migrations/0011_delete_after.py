from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0010_add_storage_key_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='document', name='delete_after',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
