'''
Lets several processes on the same machine share one token file.

Schwab allows one active token per app and user: creating a new token
invalidates the old one. So scripts that run side by side must share a single
token, which in turn means they must coordinate refreshes. Without
coordination, each process refreshes on its own schedule and overwrites the
file with its own token, and processes holding an older token can start
failing.

:class:`TokenFileSync` coordinates through the token file itself:

* Before every request, the client checks whether the token file has changed
  since it was last read or written by this client. If so, it adopts the token
  in the file, picking up refreshes and new logins from other processes.
* Refreshes happen under an exclusive lock on a lock file next to the token
  file. The lock holder re-reads the token file first, so if another process
  refreshed while this one was waiting for the lock, the refreshed token is
  used instead of refreshing again.
'''

import asyncio
import json
import logging
import os


def get_logger():
    return logging.getLogger(__name__)


if os.name == 'nt':  # pragma: no cover
    import msvcrt

    def _lock_fd(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                # LK_LOCK retries for about ten seconds before giving up, so
                # keep trying until the lock is acquired.
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError:
                continue

    def _unlock_fd(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_fd(fd):
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock_fd(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)


class FileLock:
    '''
    An exclusive lock held on a lock file, which blocks other processes and
    other ``FileLock`` instances in this process. Each instance can be acquired
    once.
    '''

    def __init__(self, path):
        self.path = path
        self._fd = None

    def acquire(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock_fd(fd)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def release(self):
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock_fd(fd)
        finally:
            os.close(fd)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    async def __aenter__(self):
        # Acquire in a worker thread so waiting for another process doesn't
        # block the event loop.
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, self.acquire)
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            # The thread may still acquire the lock after we stop waiting.
            # Release it as soon as it does.
            future.add_done_callback(lambda f: self.release())
            raise
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.release()


class TokenFileSync:
    '''
    Keeps a client's token in sync with a token file shared with other
    processes. See the module documentation for details.
    '''

    def __init__(self, token_path, write_func):
        '''
        :param token_path: Path of the shared token file.
        :param write_func: Function that writes a metadata-wrapped token to
                           ``token_path``.
        '''
        self.token_path = token_path
        self.lock_path = token_path + '.lock'
        self._write_func = write_func

        self._file_state = None
        self._session = None
        self._metadata = None

    def _current_file_state(self):
        try:
            st = os.stat(self.token_path)
        except OSError:
            return None
        # Tokens are written by replacing the file, so the inode changes on
        # every write even if the timestamp granularity is coarse.
        return (st.st_ino, st.st_mtime_ns, st.st_size)

    def read_token(self):
        '''Reads the token file. Usable as a ``token_read_func``.'''
        get_logger().info('Loading token from file %s', self.token_path)
        state = self._current_file_state()
        with open(self.token_path, 'rb') as f:
            token = json.load(f)
        self._file_state = state
        return token

    def write_token(self, token, *args, **kwargs):
        '''Writes the token file. Usable as a ``token_write_func``.'''
        self._write_func(token, *args, **kwargs)
        self._file_state = self._current_file_state()

    def attach(self, session, metadata, asyncio):
        '''
        Hooks into the OAuth session's token refresh so that it adopts tokens
        written by other processes and refreshes under the lock.
        '''
        self._session = session
        self._metadata = metadata

        original = session.ensure_active_token

        if asyncio:
            async def ensure_active_token(token=None):
                self.reload_if_changed()
                if not self._expired():
                    return True
                async with FileLock(self.lock_path):
                    self.reload_if_changed()
                    if not self._expired():
                        return True
                    return await original(self._session.token)
        else:
            def ensure_active_token(token=None):
                self.reload_if_changed()
                if not self._expired():
                    return True
                with FileLock(self.lock_path):
                    self.reload_if_changed()
                    if not self._expired():
                        return True
                    return original(self._session.token)

        session.ensure_active_token = ensure_active_token

    def _expired(self):
        return self._session.token.is_expired(leeway=self._session.leeway)

    def reload_if_changed(self):
        '''
        Adopts the token in the token file if the file has changed since this
        client last read or wrote it. Returns whether a new token was adopted.
        '''
        state = self._current_file_state()
        if state is None or state == self._file_state:
            return False

        try:
            with open(self.token_path, 'rb') as f:
                wrapped = json.load(f)
            token = wrapped['token']
            creation_timestamp = wrapped['creation_timestamp']
        except (OSError, ValueError, KeyError, TypeError) as e:
            # Tokens are replaced atomically, so this shouldn't happen, but
            # never let a bad file take down a working client.
            get_logger().warning(
                    'Ignoring unreadable token file %s: %s',
                    self.token_path, e)
            return False

        self._file_state = state

        if wrapped.get('revoked', False):
            get_logger().warning(
                    'Token file %s was marked as revoked by another process',
                    self.token_path)
            return False

        from schwab.debug import register_redactions
        register_redactions(token)

        get_logger().info(
                'Token file %s was updated by another process, reloading',
                self.token_path)
        self._session.token = token
        self._metadata.token = token
        self._metadata.creation_timestamp = creation_timestamp
        return True
