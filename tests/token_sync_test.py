import asyncio
import functools
import json
import multiprocessing
import os
import tempfile
import threading
import time
import unittest

from unittest.mock import patch

from authlib.integrations.httpx_client import AsyncOAuth2Client, OAuth2Client

from schwab import auth
from schwab._http import httpx
from schwab._token_sync import FileLock
from .utils import no_duplicates


API_KEY = 'api-key'
APP_SECRET = 'app-secret'
TOKEN_URL = 'https://api.schwabapi.com/v1/oauth/token'
ACCOUNTS_URL = 'https://api.schwabapi.com/trader/v1/accounts/accountNumbers'


def make_token(access_token, expired=False):
    return {
        'access_token': access_token,
        'refresh_token': 'refresh-token',
        'token_type': 'Bearer',
        'expires_in': 1800,
        'expires_at': int(time.time()) + (-60 if expired else 1800),
    }


def write_token_file(path, token, creation_timestamp=1000):
    auth._write_token_file(path, {
        'creation_timestamp': creation_timestamp,
        'token': token,
    })


def read_token_file(path):
    with open(path, 'r') as f:
        return json.load(f)


class FakeSchwab:
    '''
    Stands in for Schwab's API. Each token refresh is appended to a log file,
    so refreshes can be counted across processes.
    '''

    def __init__(self, refresh_log_path, refresh_delay=0):
        self.refresh_log_path = refresh_log_path
        self.refresh_delay = refresh_delay
        self.authorizations = []

    def handle(self, request):
        if str(request.url) == TOKEN_URL:
            time.sleep(self.refresh_delay)
            with open(self.refresh_log_path, 'a') as f:
                f.write('{}\n'.format(os.getpid()))
            access_token = 'refreshed-{}-{}'.format(
                    os.getpid(), self.refresh_count())
            return httpx.Response(200, json=make_token(access_token))

        self.authorizations.append(request.headers.get('Authorization'))
        return httpx.Response(200, json=[])

    async def handle_async(self, request):
        return self.handle(request)

    def refresh_count(self):
        try:
            with open(self.refresh_log_path, 'r') as f:
                return len(f.readlines())
        except FileNotFoundError:
            return 0


def patch_oauth_clients(fake):
    return patch.multiple(
            'schwab.auth',
            OAuth2Client=functools.partial(
                OAuth2Client, transport=httpx.MockTransport(fake.handle)),
            AsyncOAuth2Client=functools.partial(
                AsyncOAuth2Client,
                transport=httpx.MockTransport(fake.handle_async)))


def refresh_in_subprocess(token_path, refresh_log_path, start_at):
    '''Loads a client from the token file and makes one request.'''
    fake = FakeSchwab(refresh_log_path, refresh_delay=0.2)
    with patch_oauth_clients(fake):
        client = auth.client_from_token_file(token_path, API_KEY, APP_SECRET)
        # Line up all processes so they find the token expired together
        time.sleep(max(0, start_at - time.time()))
        client.get_account_numbers()


class TokenFileSyncTest(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.token_path = os.path.join(self.tmp_dir.name, 'token.json')
        self.refresh_log_path = os.path.join(self.tmp_dir.name, 'refreshes')
        self.fake = FakeSchwab(self.refresh_log_path)

        patcher = patch_oauth_clients(self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self, **kwargs):
        return auth.client_from_token_file(
                self.token_path, API_KEY, APP_SECRET, **kwargs)

    @no_duplicates
    def test_second_client_adopts_refresh_instead_of_refreshing(self):
        write_token_file(self.token_path, make_token('old', expired=True))
        client_a = self.client()
        client_b = self.client()

        client_a.get_account_numbers()
        client_b.get_account_numbers()

        self.assertEqual(1, self.fake.refresh_count())
        refreshed = read_token_file(self.token_path)['token']['access_token']
        self.assertEqual(['Bearer ' + refreshed] * 2, self.fake.authorizations)

    @no_duplicates
    def test_adopts_token_written_by_another_process(self):
        write_token_file(self.token_path, make_token('old'))
        client = self.client()

        write_token_file(self.token_path, make_token('new'),
                         creation_timestamp=2000)
        client.get_account_numbers()

        self.assertEqual(['Bearer new'], self.fake.authorizations)
        self.assertEqual(2000, client.token_metadata.creation_timestamp)
        self.assertEqual('new', client.token_metadata.token['access_token'])
        self.assertEqual(0, self.fake.refresh_count())

    @no_duplicates
    def test_own_refresh_is_not_reloaded(self):
        write_token_file(self.token_path, make_token('old', expired=True))
        client = self.client()

        with self.assertNoLogs('schwab._token_sync', level='INFO'):
            client.get_account_numbers()
            client.get_account_numbers()

        self.assertEqual(1, self.fake.refresh_count())

    @no_duplicates
    def test_refreshed_token_written_to_file(self):
        write_token_file(self.token_path, make_token('old', expired=True))
        client = self.client()

        client.get_account_numbers()

        wrapped = read_token_file(self.token_path)
        self.assertEqual(1000, wrapped['creation_timestamp'])
        self.assertTrue(wrapped['token']['access_token'].startswith(
            'refreshed-'))

    @no_duplicates
    def test_share_token_disabled(self):
        write_token_file(self.token_path, make_token('old'))
        client = self.client(share_token=False)

        write_token_file(self.token_path, make_token('new'))
        client.get_account_numbers()

        self.assertEqual(['Bearer old'], self.fake.authorizations)
        self.assertFalse(os.path.exists(self.token_path + '.lock'))

    @no_duplicates
    def test_unreadable_token_file_is_ignored(self):
        write_token_file(self.token_path, make_token('old'))
        client = self.client()

        with open(self.token_path, 'w') as f:
            f.write('not json')
        with self.assertLogs('schwab._token_sync', level='WARNING'):
            client.get_account_numbers()

        self.assertEqual(['Bearer old'], self.fake.authorizations)

    @no_duplicates
    def test_deleted_token_file_is_ignored(self):
        write_token_file(self.token_path, make_token('old'))
        client = self.client()

        os.remove(self.token_path)
        client.get_account_numbers()

        self.assertEqual(['Bearer old'], self.fake.authorizations)

    @no_duplicates
    def test_revoked_token_file_is_not_adopted(self):
        write_token_file(self.token_path, make_token('old'))
        client = self.client()

        auth._write_token_file(self.token_path, {
            'creation_timestamp': 1000,
            'token': make_token('revoked'),
            'revoked': True,
        })
        with self.assertLogs('schwab._token_sync', level='WARNING'):
            client.get_account_numbers()

        self.assertEqual(['Bearer old'], self.fake.authorizations)

    @no_duplicates
    def test_async_second_client_adopts_refresh(self):
        write_token_file(self.token_path, make_token('old', expired=True))

        async def run():
            client_a = self.client(asyncio=True)
            client_b = self.client(asyncio=True)
            await client_a.get_account_numbers()
            await client_b.get_account_numbers()
            await client_a.close_async_session()
            await client_b.close_async_session()
        asyncio.run(run())

        self.assertEqual(1, self.fake.refresh_count())
        refreshed = read_token_file(self.token_path)['token']['access_token']
        self.assertEqual(['Bearer ' + refreshed] * 2, self.fake.authorizations)

    @no_duplicates
    def test_async_concurrent_requests_refresh_once(self):
        write_token_file(self.token_path, make_token('old', expired=True))
        self.fake.refresh_delay = 0.1

        async def run():
            clients = [self.client(asyncio=True) for _ in range(3)]
            await asyncio.gather(*(c.get_account_numbers() for c in clients))
            for c in clients:
                await c.close_async_session()
        asyncio.run(run())

        self.assertEqual(1, self.fake.refresh_count())

    @no_duplicates
    def test_threads_refresh_once(self):
        write_token_file(self.token_path, make_token('old', expired=True))
        self.fake.refresh_delay = 0.1
        clients = [self.client() for _ in range(4)]

        threads = [threading.Thread(target=c.get_account_numbers)
                   for c in clients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(1, self.fake.refresh_count())
        self.assertEqual(4, len(self.fake.authorizations))

    @no_duplicates
    def test_processes_refresh_once(self):
        write_token_file(self.token_path, make_token('old', expired=True))

        ctx = multiprocessing.get_context('spawn')
        start_at = time.time() + 3
        processes = [
            ctx.Process(target=refresh_in_subprocess,
                        args=(self.token_path, self.refresh_log_path,
                              start_at))
            for _ in range(4)]
        for p in processes:
            p.start()
        for p in processes:
            p.join(timeout=60)
            self.assertEqual(0, p.exitcode)

        self.assertEqual(1, self.fake.refresh_count())


class FileLockTest(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.lock_path = os.path.join(self.tmp_dir.name, 'lock')

    @no_duplicates
    def test_excludes_other_holders(self):
        events = []

        def hold():
            with FileLock(self.lock_path):
                events.append('second acquired')

        with FileLock(self.lock_path):
            thread = threading.Thread(target=hold)
            thread.start()
            time.sleep(0.2)
            events.append('first releasing')
        thread.join()

        self.assertEqual(['first releasing', 'second acquired'], events)

    @no_duplicates
    def test_async_does_not_block_event_loop(self):
        async def run():
            ticks = 0

            async def tick():
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.01)
                    ticks += 1

            with FileLock(self.lock_path):
                ticker = asyncio.ensure_future(tick())
                waiter = asyncio.ensure_future(
                        FileLock(self.lock_path).__aenter__())
                await asyncio.sleep(0.2)
                self.assertFalse(waiter.done())
            lock = await waiter
            lock.release()
            ticker.cancel()
            return ticks

        self.assertGreater(asyncio.run(run()), 5)

    @no_duplicates
    def test_release_is_idempotent(self):
        lock = FileLock(self.lock_path)
        lock.acquire()
        lock.release()
        lock.release()
