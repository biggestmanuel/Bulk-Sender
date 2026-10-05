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

from django.core.exceptions import (
    PermissionDenied as DjangoPermissionDenied,
    ValidationError as DjangoValidationError,
)
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied as DRFPermissionDenied
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    # Translate Django's exceptions into their DRF equivalents so they get
    # the right status code.
    #
    # Http404 used to be mapped to DjangoPermissionDenied here, which DRF
    # turns into 403. That meant every unmatched URL — /api/nonexistent/,
    # /api/campaigns/abc/status/ — answered "forbidden" instead of "not
    # found". Wrong status, and actively misleading to anyone debugging a
    # client integration.
    if isinstance(exc, Http404):
        exc = NotFound(str(exc) or 'Not found.')
    elif isinstance(exc, DjangoPermissionDenied):
        exc = DRFPermissionDenied(str(exc) or 'Permission denied.')
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

    message = _extract_message(response.data)

    code = {
        400: 'bad_request',
        401: 'unauthorized',
        403: 'forbidden',
        404: 'not_found',
        405: 'method_not_allowed',
        429: 'rate_limited',
    }.get(response.status_code, 'error')

    response.data = {'error': message or 'Request failed.', 'code': code}
    return response


def _as_messages(value):
    """
    Normalises DRF's several error shapes into a list of strings.

    DRF hands back any of: a string, a list of strings, a dict of
    field -> [messages], or a list of such dicts. The previous version did
    `', '.join(str(m) for m in msgs)` unconditionally, which iterated a bare
    string character by character and produced errors like
    "error: t, h, i, s,  , f, i, e, l, d".
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _extract_message(data):
    """
    Turns a DRF error body into one readable line.

    Field-level errors matter here: the frontend renders `error` verbatim, and
    "phone: This field is required" tells the user something. Dropping it to
    a generic "Request failed." loses the only actionable information.
    """
    # {'detail': 'Not found.'} — the common single-message case.
    if isinstance(data, dict) and 'detail' in data:
        return '; '.join(_as_messages(data['detail']))

    # {'phone': ['required'], 'name': ['too long']} — field errors.
    if isinstance(data, dict):
        parts = []
        for field, messages in data.items():
            joined = ', '.join(_as_messages(messages))
            parts.append(f'{field}: {joined}' if field != 'non_field_errors' else joined)
        return '; '.join(parts)

    # [{'phone': ['required']}, ...] — the many=True shape.
    if isinstance(data, list):
        parts = []
        for entry in data:
            if isinstance(entry, dict):
                for field, messages in entry.items():
                    joined = ', '.join(_as_messages(messages))
                    parts.append(f'{field}: {joined}' if field != 'non_field_errors' else joined)
            else:
                parts.extend(_as_messages(entry))
        return '; '.join(parts)

    return _as_messages(data)[0] if data else ''
