from django.db import migrations, models


def reset_languages_to_english(apps, schema_editor):
    User = apps.get_model('core', 'User')
    User.objects.exclude(language='en').update(language='en')


class Migration(migrations.Migration):
    """The application ships in English only; normalize stored preferences."""

    dependencies = [
        ('core', '0010_delete_apikey'),
    ]

    operations = [
        migrations.RunPython(reset_languages_to_english, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='user',
            name='language',
            field=models.CharField(
                choices=[('en', 'English')],
                default='en',
                help_text='Preferred UI language',
                max_length=10,
            ),
        ),
    ]
