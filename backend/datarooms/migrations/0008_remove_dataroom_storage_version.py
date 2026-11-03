from django.db import migrations


class Migration(migrations.Migration):
    """All datarooms use the system vault ('__datarooms__/<id>') storage layout.

    The legacy v1 layout ('Dataroom Uploads/<name>') and its upgrade path were
    removed, so the storage_version discriminator is no longer needed.
    """

    dependencies = [
        ('datarooms', '0007_dataroom_banner_mode_dataroom_brand_logo_override_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='dataroom',
            name='storage_version',
        ),
    ]
