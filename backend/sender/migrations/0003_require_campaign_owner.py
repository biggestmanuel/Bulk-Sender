# Deletes any campaigns left over from before user accounts existed
# (owner=None), then makes owner required going forward. These orphaned
# rows can't be attributed to any real user, so there's nothing safe to
# backfill them to — deleting is the only option that doesn't risk
# assigning someone else's old test data to a real account.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def delete_orphaned_campaigns(apps, schema_editor):
    Campaign = apps.get_model('sender', 'Campaign')
    Campaign.objects.filter(owner__isnull=True).delete()


def noop_reverse(apps, schema_editor):
    # Nothing to reverse — deleted orphaned rows can't be recovered,
    # and reversing the field back to nullable doesn't need any data change.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sender', '0002_campaign_owner_alter_campaign_message_text_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(delete_orphaned_campaigns, noop_reverse),
        migrations.AlterField(
            model_name='campaign',
            name='owner',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='campaigns',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]