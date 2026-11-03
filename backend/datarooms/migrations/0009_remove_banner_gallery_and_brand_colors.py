from django.db import migrations, models


def gallery_to_single(apps, schema_editor):
    Dataroom = apps.get_model('datarooms', 'Dataroom')
    Dataroom.objects.filter(banner_mode='gallery').update(banner_mode='single')


class Migration(migrations.Migration):
    """Remove the dataroom banner gallery and per-dataroom brand colours.

    Datarooms keep a single optional banner image and an optional logo
    override. Rooms that were in 'gallery' mode fall back to 'single'.
    Uploaded gallery image files are not deleted from MEDIA_ROOT.
    """

    dependencies = [
        ('datarooms', '0008_remove_dataroom_storage_version'),
    ]

    operations = [
        migrations.RunPython(gallery_to_single, migrations.RunPython.noop),
        migrations.DeleteModel(
            name='DataroomBannerImage',
        ),
        migrations.RemoveField(
            model_name='dataroom',
            name='brand_primary_color',
        ),
        migrations.RemoveField(
            model_name='dataroom',
            name='brand_secondary_color',
        ),
        migrations.RemoveField(
            model_name='dataroom',
            name='brand_accent_color',
        ),
        migrations.AlterField(
            model_name='dataroom',
            name='banner_mode',
            field=models.CharField(
                choices=[('single', 'Single banner image'), ('none', 'No banner')],
                default='single',
                help_text='Banner surface rendering mode',
                max_length=16,
            ),
        ),
    ]
