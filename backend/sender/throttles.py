"""
Throttling for the endpoints that matter most.

DRF's built-in anon/user rate limits are configured in settings.py. These
are the app-specific ones:

  * LoginRateThrottle / RegisterRateThrottle — an auth endpoint with no
    rate limit is a free password-guessing service, and register is a free
    way to fill someone's database.

  * CampaignCreateThrottle — a user could otherwise POST campaigns in a
    tight loop and fill the DB with rows plus, more importantly, upload
    media until disk runs out.

  * SendStartThrottle — starting a send spawns a real Chromium process
    against that user's persistent WhatsApp profile. It is the single most
    expensive thing this app does and was previously unlimited.

Note on ScopedRateThrottle: it reads its scope from `view.throttle_scope`,
but DRF passes the *WrappedAPIView instance* to allow_request(), not the
module-level function returned by api_view(). Setting `.throttle_scope` on
the decorated function therefore does nothing and the throttle silently
falls back to the default scope. These classes read their rate from settings
directly instead, which has no such trap.
"""

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class _RateFromSettings:
    """Mixin that resolves `rate` from settings.REST_FRAMEWORK at init time."""

    rate_setting = None

    def __init__(self):
        if self.rate_setting:
            rates = settings.REST_FRAMEWORK.get('DEFAULT_THROTTLE_RATES', {})
            self.rate = rates.get(self.rate_setting, getattr(self, 'rate', None))
        super().__init__()


class LoginRateThrottle(_RateFromSettings, AnonRateThrottle):
    """Keyed by client IP. Authenticated users bypass — this guards login."""

    rate = '10/minute'
    rate_setting = 'login'


class RegisterRateThrottle(_RateFromSettings, AnonRateThrottle):
    rate = '5/hour'
    rate_setting = 'register'


class CampaignCreateThrottle(_RateFromSettings, UserRateThrottle):
    rate = '60/hour'
    rate_setting = 'user'


class StatusPollThrottle(_RateFromSettings, UserRateThrottle):
    """
    Budget for the campaign status endpoint.

    The frontend polls this every 3 seconds while a send is running, which is
    1,200 requests per hour. Under the default 600/hour 'user' rate a send of
    about half an hour would start returning 429, freezing the progress panel
    while the send carried on in the background.
    """

    rate = '2400/hour'
    rate_setting = 'status'


class LoginStatusPollThrottle(_RateFromSettings, UserRateThrottle):
    """
    Budget for the login-status endpoint, polled every 2 seconds (1,800/hour)
    for the duration of the phone-link handshake, which can take ten minutes.
    """

    rate = '3600/hour'
    rate_setting = 'login_status'


class SendStartThrottle(_RateFromSettings, UserRateThrottle):
    rate = '30/hour'
    rate_setting = 'send_start'