from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import core.fields


def _base():
    return [
        ('id', core.fields.ULIDField(default=core.fields.generate_ulid, editable=False, max_length=26, primary_key=True, serialize=False, unique=True, validators=[core.fields.validate_ulid])),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
    ]


class Migration(migrations.Migration):

    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name='RetentionSettings',
            fields=_base() + [
                ('mode', models.CharField(choices=[('off', 'No automatic deletion'), ('after_upload', 'Delete a set time after upload'), ('yearly', 'Delete everything on a date every year')], default='off', max_length=20)),
                ('after_value', models.PositiveSmallIntegerField(default=1)),
                ('after_unit', models.CharField(choices=[('days', 'days'), ('weeks', 'weeks'), ('months', 'months'), ('years', 'years')], default='years', max_length=10)),
                ('yearly_month', models.PositiveSmallIntegerField(default=1)),
                ('yearly_day', models.PositiveSmallIntegerField(default=1)),
                ('overwrite', models.BooleanField(default=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'abstract': False},
        ),
        migrations.CreateModel(
            name='RetentionLog',
            fields=_base() + [
                ('document_id', models.CharField(db_index=True, max_length=26)),
                ('document_name', models.CharField(max_length=255)),
                ('organization_id', models.CharField(blank=True, max_length=26)),
                ('reason', models.CharField(max_length=30)),
                ('overwritten', models.BooleanField(default=False)),
                ('ok', models.BooleanField(default=True)),
                ('error', models.CharField(blank=True, max_length=500)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
