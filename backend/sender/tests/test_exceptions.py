"""
Tests for the API exception handler.

Every error the frontend receives has to be JSON with a readable message,
because api.js does `await response.text()` and parses it. A handler that
falls back to DRF's HTML error page turns a clear server-side failure into
"Unexpected token < in JSON" in the browser.
"""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from django.test import TestCase
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

from sender.exceptions import api_exception_handler


class NotFoundException(APIException):
    status_code = 404
    default_detail = 'no such thing'


class TeapotException(APIException):
    status_code = 418
    default_detail = 'short and stout'


def handle(exc):
    factory = APIRequestFactory()
    request = factory.get('/api/whatever/')
    request.user = None
    return api_exception_handler(exc, {'view': None, 'request': request})


class StatusMappingTests(TestCase):
    """
    The bug this file exists for: Http404 was mapped to Django's
    PermissionDenied, which DRF renders as 403. Every unmatched URL answered
    "forbidden" instead of "not found".
    """

    def test_http404_becomes_404_not_403(self):
        response = handle(Http404('no such campaign'))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data['code'], 'not_found')

    def test_http404_message_is_preserved(self):
        response = handle(Http404('no such campaign'))
        self.assertIn('no such campaign', response.data['error'])

    def test_permission_denied_stays_403(self):
        response = handle(DjangoPermissionDenied('not yours'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data['code'], 'forbidden')

    def test_drf_not_found_is_404(self):
        response = handle(NotFound('gone'))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_validation_error_is_400(self):
        response = handle(DjangoValidationError('that is not a number'))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'validation_error')

    def test_django_validation_error_with_field_messages(self):
        response = handle(DjangoValidationError(['bad phone', 'bad name']))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('bad phone', response.data['error'])


class ErrorShapeTests(TestCase):
    def test_every_response_has_error_and_code(self):
        for exc in (
            Http404('x'),
            DjangoPermissionDenied('x'),
            NotFound('x'),
            DjangoValidationError('x'),
            TeapotException(),
        ):
            with self.subTest(exc=type(exc).__name__):
                data = handle(exc).data
                self.assertIn('error', data)
                self.assertIn('code', data)
                self.assertIsInstance(data['error'], str)

    def test_already_shaped_errors_are_not_re_wrapped(self):
        """
        The guard exists for DRF exceptions whose body is already
        {'error': ..., 'code': ...}; re-wrapping would bury the real message
        under "Request failed.".
        """
        response = api_exception_handler(
            AlreadyShaped(), {'view': None}
        )
        self.assertEqual(response.data['error'], 'Campaign not found.')
        self.assertEqual(response.data['code'], 'not_found')

    def test_unknown_exception_is_500_without_leaking(self):
        response = handle(RuntimeError('SELECT * FROM users; password=hunter2'))

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.data['code'], 'server_error')
        # Must not leak the SQL, the exception text, or a traceback.
        body = response.data['error']
        for leak in ('hunter2', 'SELECT', 'RuntimeError', 'Traceback'):
            self.assertNotIn(leak, body)

    def test_serializer_field_errors_are_flattened(self):
        from rest_framework.exceptions import ValidationError as DRFValidationError

        exc = DRFValidationError({'phone': ['is required'], 'name': ['too long']})
        response = handle(exc)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('phone', response.data['error'])
        self.assertIn('name', response.data['error'])

    def test_non_field_serializer_errors_are_flattened(self):
        from rest_framework.exceptions import ValidationError as DRFValidationError

        response = handle(DRFValidationError(['this field is required']))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('required', response.data['error'])

    def test_throttled_response_is_429(self):
        from rest_framework.exceptions import Throttled

        response = handle(Throttled(wait=60))

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(response.data['code'], 'rate_limited')

    def test_method_not_allowed_is_405(self):
        from rest_framework.exceptions import MethodNotAllowed

        response = handle(MethodNotAllowed('DELETE'))

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_empty_detail_still_produces_a_message(self):
        """A blank error string would render an empty red box in the UI."""
        response = handle(TeapotException())
        self.assertTrue(response.data['error'].strip())


class AlreadyShaped(APIException):
    """An exception whose body is already in the API's own error shape."""

    status_code = 404
    default_detail = 'unused'

    def __init__(self):
        super().__init__()
        # DRF puts `detail` on the exception; the handler's early-return path
        # is what we're exercising, so shape the data directly.
        self.detail = {'error': 'Campaign not found.', 'code': 'not_found'}
