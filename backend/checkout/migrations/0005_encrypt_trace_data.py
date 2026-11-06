import django_cryptography.fields
from django.db import migrations, models


def encrypt_existing(apps, schema_editor):
    """Copy plaintext samples into the encrypted columns (saving through the ORM encrypts them)."""
    Chunk = apps.get_model('checkout', 'CheckoutTraceChunk')
    for chunk in Chunk.objects.all().iterator():
        chunk.moves_enc = chunk.moves
        chunk.views_enc = chunk.views
        chunk.save(update_fields=['moves_enc', 'views_enc'])


class Migration(migrations.Migration):

    dependencies = [
        ('checkout', '0004_trace_chunks'),
    ]

    operations = [
        migrations.AddField(model_name='checkouttracechunk', name='moves_enc',
                            field=django_cryptography.fields.encrypt(models.JSONField(null=True))),
        migrations.AddField(model_name='checkouttracechunk', name='views_enc',
                            field=django_cryptography.fields.encrypt(models.JSONField(null=True))),
        migrations.RunPython(encrypt_existing, migrations.RunPython.noop),
        migrations.RemoveField(model_name='checkouttracechunk', name='moves'),
        migrations.RemoveField(model_name='checkouttracechunk', name='views'),
        migrations.RenameField(model_name='checkouttracechunk', old_name='moves_enc', new_name='moves'),
        migrations.RenameField(model_name='checkouttracechunk', old_name='views_enc', new_name='views'),
        migrations.AlterField(model_name='checkouttracechunk', name='moves',
                              field=django_cryptography.fields.encrypt(models.JSONField(default=list))),
        migrations.AlterField(model_name='checkouttracechunk', name='views',
                              field=django_cryptography.fields.encrypt(models.JSONField(default=list))),
    ]
