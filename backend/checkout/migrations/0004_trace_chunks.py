from django.db import migrations, models
import django.db.models.deletion

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('checkout', '0003_per_file_access_settings'),
    ]

    operations = [
        migrations.CreateModel(
            name='CheckoutTraceChunk',
            fields=[
                ('id', core.fields.ULIDField(default=core.fields.generate_ulid, editable=False, max_length=26, primary_key=True, serialize=False, unique=True, validators=[core.fields.validate_ulid])),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('moves', models.JSONField(default=list)),
                ('views', models.JSONField(default=list)),
                ('checkout', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='trace_chunks', to='checkout.checkout')),
            ],
            options={'ordering': ['created_at']},
        ),
        migrations.AddIndex(
            model_name='checkouttracechunk',
            index=models.Index(fields=['checkout', 'created_at'], name='co_trace_checkout_idx'),
        ),
    ]
