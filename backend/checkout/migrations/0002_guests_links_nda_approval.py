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

    dependencies = [
        ('checkout', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # sign-in is now an e-mailed link, and access is per guest, not per domain
        migrations.DeleteModel(name='CheckoutAuthCode'),
        migrations.RemoveField(model_name='checkoutpolicy', name='allowed_domains'),
        migrations.AddField(model_name='checkoutpolicy', name='allow_download', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='checkoutpolicy', name='require_nda', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='checkoutpolicy', name='nda_text', field=models.TextField(blank=True)),
        migrations.AddField(model_name='checkoutpolicy', name='nda_version', field=models.PositiveIntegerField(default=1)),
        migrations.AddField(model_name='checkoutfile', name='requires_approval', field=models.BooleanField(default=False)),
        migrations.CreateModel(
            name='CheckoutGuest',
            fields=_base() + [
                ('email', models.EmailField(max_length=254)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('added_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('policy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='guests', to='checkout.checkoutpolicy')),
            ],
            options={'ordering': ['created_at']},
        ),
        migrations.AddConstraint(
            model_name='checkoutguest',
            constraint=models.UniqueConstraint(fields=('policy', 'email'), name='unique_checkout_guest'),
        ),
        migrations.CreateModel(
            name='CheckoutLoginLink',
            fields=_base() + [
                ('email', models.EmailField(max_length=254)),
                ('token_hash', models.CharField(db_index=True, max_length=64)),
                ('expires_at', models.DateTimeField()),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('requested_ip', models.GenericIPAddressField(blank=True, null=True)),
            ],
            options={'abstract': False},
        ),
        migrations.CreateModel(
            name='CheckoutNdaAcceptance',
            fields=_base() + [
                ('email', models.EmailField(max_length=254)),
                ('nda_version', models.PositiveIntegerField()),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('policy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='nda_acceptances', to='checkout.checkoutpolicy')),
            ],
            options={'abstract': False},
        ),
        migrations.AddConstraint(
            model_name='checkoutndaacceptance',
            constraint=models.UniqueConstraint(fields=('policy', 'email', 'nda_version'), name='unique_checkout_nda'),
        ),
        migrations.AddField(model_name='checkout', name='status', field=models.CharField(choices=[('pending', 'Waiting for approval'), ('approved', 'Approved'), ('denied', 'Denied'), ('open', 'Open'), ('ended', 'Ended')], default='approved', max_length=10)),
        migrations.AddField(model_name='checkout', name='decided_by', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='checkout', name='decided_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='checkout', name='opened_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AlterField(model_name='checkout', name='expires_at', field=models.DateTimeField(blank=True, null=True)),
    ]
