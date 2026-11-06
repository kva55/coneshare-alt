from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_english_only_user_language'),
    ]

    operations = [
        migrations.AddField(
            model_name='user', name='must_change_password',
            field=models.BooleanField(default=False),
        ),
    ]
