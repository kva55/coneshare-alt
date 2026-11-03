from django.db import migrations


class Migration(migrations.Migration):
    """API keys were only used by the removed MCP server; delete the model."""

    dependencies = [
        ('core', '0009_alter_organization_brand_logo'),
    ]

    operations = [
        migrations.DeleteModel(
            name='APIKey',
        ),
    ]
