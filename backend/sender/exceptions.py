"""
Custom exception handler.

Without this, any DRF exception produced an HTML error body, and the
frontend's `await response.json()` threw a JSON parse error instead of
showing the real message. That made every failure look like "Something went
wrong" no matter what had actually happened.

Now every error the frontend sees is JSON: `{"error": "human message"}`,
with a stable machine-readable `code` for the cases the frontend branches on
(e.g. PHONE_REQUIRED, ALREADY_SENDING).
"""

import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    # Route Django's own exceptions through DRF first so they pick up the
    # standard handling (404, 403) instead of bubbling up as a 500.
    if isinstance(exc, Http404):
        exc = DjangoPermissionDenied(str(exc))
    elif isinstance(exc, DjangoValidationError):
        detail = getattr(exc, 'message', None) or '; '.join(exc.messages)
        return Response(
            {'error': str(detail), 'code': 'validation_error'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    response = drf_exception_handler(exc, context)

    if response is None:
        # Unhandled exception. Log the real error server-side and return a
        # generic message — never leak a traceback or SQL to the client.
        view = context.get('view') if context else None
        logger.exception('Unhandled error in %s', view.__class__.__name__ if view else 'unknown view')
        return Response(
            {'error': 'Something went wrong on our end. Please try again.', 'code': 'server_error'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(response.data, dict) and 'error' in response.data:
        return response

    detail = response.data.get('detail') if isinstance(response.data, dict) else None

    if detail is not None:
        message = str(detail)
    elif isinstance(response.data, list) and response.data:
        # e.g. serializer errors: [{'phone': ['...']}]
        message = '; '.join(
            f"{field}: {', '.join(str(m) for m in msgs)}"
            for field, msgs in (response.data[0].items() if isinstance(response.data[0], dict) else [('error', response.data[0])])
        )
    else:
        message = 'Request failed.'

    code = {
        400: 'bad_request',
        401: 'unauthorized',
        403: 'forbidden',
        404: 'not_found',
        405: 'method_not_allowed',
        429: 'rate_limited',
    }.get(response.status_code, 'error')

    response.data = {'error': message, 'code': code}
    return response