import json

import django_cryptography.fields
from django.db import migrations, models


def copy_old(apps, schema_editor):
    """Carry over anything the JSON columns still hold; rows that cannot be read are dropped."""
    Chunk = apps.get_model('checkout', 'CheckoutTraceChunk')
    for chunk in list(Chunk.objects.all()):
        try:
            moves, views = chunk.moves, chunk.views
            moves = json.loads(moves) if isinstance(moves, str) else moves
            views = json.loads(views) if isinstance(views, str) else views
            chunk.moves_data = json.dumps(moves or [])
            chunk.views_data = json.dumps(views or [])
            chunk.save(update_fields=['moves_data', 'views_data'])
        except Exception:
            chunk.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('checkout', '0005_encrypt_trace_data'),
    ]

    operations = [
        migrations.AddField(model_name='checkouttracechunk', name='moves_data',
                            field=django_cryptography.fields.encrypt(models.TextField(default='[]'))),
        migrations.AddField(model_name='checkouttracechunk', name='views_data',
                            field=django_cryptography.fields.encrypt(models.TextField(default='[]'))),
        migrations.RunPython(copy_old, migrations.RunPython.noop),
        migrations.RemoveField(model_name='checkouttracechunk', name='moves'),
        migrations.RemoveField(model_name='checkouttracechunk', name='views'),
    ]
