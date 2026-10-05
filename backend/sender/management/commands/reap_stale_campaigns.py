"""
Marks abandoned campaign sends as failed.

Run this periodically (cron, a scheduler, or by hand after a deploy) to
recover campaigns whose worker thread died with its web worker. See
sender/reliability.py for why this is needed.

    python manage.py reap_stale_campaigns            # report and fix
    python manage.py reap_stale_campaigns --dry-run # report only
"""

import time

from django.core.management.base import BaseCommand

from sender.reliability import is_stale, reap_stale_campaigns, stale_after_seconds


class Command(BaseCommand):
    help = (
        'Mark campaign sends whose worker died as failed, so the affected '
        'user can start another campaign.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List what would be reaped without changing anything.',
        )
        parser.add_argument(
            '--timeout',
            type=int,
            default=None,
            help=(
                'Seconds without a heartbeat before a campaign counts as '
                f'abandoned (default: {stale_after_seconds()} from settings).'
            ),
        )
        parser.add_argument(
            '--loop',
            action='store_true',
            help='Keep running, reaping every 60 seconds. Intended for a sidecar process.',
        )

    def handle(self, *args, **options):
        if options['loop']:
            self._loop(options)
            return

        reaped = self._once(options)
        if reaped and options['dry_run']:
            self.stdout.write(
                self.style.WARNING(
                    'This was a dry run. Re-run without --dry-run to apply.'
                )
            )

    def _loop(self, options):
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f'Watching for abandoned sends (timeout {options["timeout"] or stale_after_seconds()}s). '
                'Ctrl-C to stop.'
            )
        )
        try:
            while True:
                self._once(options)
                time.sleep(60)
        except KeyboardInterrupt:
            self.stdout.write('\nStopped.')

    def _once(self, options):
        dry_run = options['dry_run']
        timeout = options['timeout']

        if dry_run:
            from sender.models import Campaign

            reaped = []
            candidates = Campaign.objects.filter(
                status__in=Campaign.IN_FLIGHT_STATUSES
            ).only('id', 'status', 'owner_id', 'last_heartbeat_at', 'send_started_at', 'created_at')

            for campaign in candidates:
                if is_stale(campaign, timeout=timeout):
                    reaped.append(campaign.pk)
                    self.stdout.write(
                        f'  campaign {campaign.pk} (user {campaign.owner_id}) '
                        f'is {campaign.status} with no heartbeat'
                    )
        else:
            reaped = reap_stale_campaigns(timeout=timeout)

        if reaped:
            verb = 'would be reaped' if dry_run else 'reaped'
            style = self.style.WARNING if dry_run else self.style.SUCCESS
            self.stdout.write(style(f'{len(reaped)} abandoned campaign(s) {verb}: {reaped}'))
        else:
            self.stdout.write('No abandoned campaigns found.')

        return reaped
