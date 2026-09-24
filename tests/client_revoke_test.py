import unittest

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from schwab.auth import REVOKE_URL
from schwab.client import AsyncClient, Client
from .utils import MockResponse, no_duplicates


API_KEY = 'api-key'
APP_SECRET = 'app-secret'


def make_session():
    session = MagicMock()
    session.client_id = API_KEY
    session.client_secret = APP_SECRET
    return session


def make_token_metadata(token):
    metadata = Mock()
    metadata.token = token
    return metadata


class RevokeTest(unittest.TestCase):

    @no_duplicates
    @patch('schwab.client.synchronous.httpx')
    def test_success_marks_revoked(self, httpx):
        httpx.post.return_value = MockResponse({}, 200)
        metadata = make_token_metadata({'refresh_token': 'refresh'})
        client = Client(API_KEY, make_session(), token_metadata=metadata)

        resp = client.revoke()

        self.assertEqual(200, resp.status_code)
        httpx.post.assert_called_once_with(
                REVOKE_URL,
                data={'token': 'refresh', 'token_type_hint': 'refresh_token'},
                auth=(API_KEY, APP_SECRET),
                timeout=30.0)
        metadata.mark_revoked.assert_called_once()

    @no_duplicates
    @patch('schwab.client.synchronous.httpx')
    def test_failure_does_not_mark_revoked(self, httpx):
        httpx.post.return_value = MockResponse({}, 400)
        metadata = make_token_metadata({'refresh_token': 'refresh'})
        client = Client(API_KEY, make_session(), token_metadata=metadata)

        self.assertEqual(400, client.revoke().status_code)
        metadata.mark_revoked.assert_not_called()

    @no_duplicates
    @patch('schwab.client.synchronous.httpx')
    def test_no_refresh_token(self, httpx):
        metadata = make_token_metadata({'access_token': 'access'})
        client = Client(API_KEY, make_session(), token_metadata=metadata)

        with self.assertRaisesRegex(ValueError, 'No refresh_token'):
            client.revoke()
        httpx.post.assert_not_called()


class AsyncRevokeTest(IsolatedAsyncioTestCase):

    @no_duplicates
    @patch('schwab.client.asynchronous.httpx')
    async def test_success_marks_revoked(self, httpx):
        http_client = AsyncMock()
        http_client.post.return_value = MockResponse({}, 200)
        httpx.AsyncClient.return_value.__aenter__.return_value = http_client
        metadata = make_token_metadata({'refresh_token': 'refresh'})
        client = AsyncClient(API_KEY, make_session(), token_metadata=metadata)

        resp = await client.revoke()

        self.assertEqual(200, resp.status_code)
        http_client.post.assert_awaited_once_with(
                REVOKE_URL,
                data={'token': 'refresh', 'token_type_hint': 'refresh_token'},
                auth=(API_KEY, APP_SECRET),
                timeout=30.0)
        metadata.mark_revoked.assert_called_once()

    @no_duplicates
    @patch('schwab.client.asynchronous.httpx')
    async def test_failure_does_not_mark_revoked(self, httpx):
        http_client = AsyncMock()
        http_client.post.return_value = MockResponse({}, 401)
        httpx.AsyncClient.return_value.__aenter__.return_value = http_client
        metadata = make_token_metadata({'refresh_token': 'refresh'})
        client = AsyncClient(API_KEY, make_session(), token_metadata=metadata)

        self.assertEqual(401, (await client.revoke()).status_code)
        metadata.mark_revoked.assert_not_called()
