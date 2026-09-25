from .base import BaseClient
from ..debug import register_redactions_from_response
from authlib.integrations.base_client import OAuthError

from .._http import httpx
import asyncio


class AsyncClient(BaseClient):

    async def close_async_session(self):
        await self.session.aclose()

    async def revoke(self):
        '''Revoke this client's refresh token server-side via Schwab's OAuth
        revocation endpoint (RFC 7009). Revoking the refresh token invalidates
        any access tokens issued from it, killing the credential everywhere
        rather than merely forgetting it locally.

        On a successful (2xx) response, the token metadata is marked as revoked
        and persisted via the same write function used for token refreshes.
        Subsequent attempts to load the token via :func:`easy_client` or
        :func:`client_from_token_file` will surface the revoked state.

        Returns the raw ``httpx`` response.
        '''
        refresh_token = self.token_metadata.token.get('refresh_token')
        if not refresh_token:
            raise ValueError(
                    'No refresh_token present on this client; nothing to '
                    'revoke.')

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                    self.base_url + '/v1/oauth/revoke',
                    data={'token': refresh_token,
                          'token_type_hint': 'refresh_token'},
                    auth=(self.session.client_id, self.session.client_secret),
                    timeout=30.0)

        if 200 <= resp.status_code < 300:
            self.token_metadata.mark_revoked()

        return resp

    async def get_account_hash(self, account_number):
        '''Returns the account hash for an account number. See
        :meth:`Client.get_account_hash <schwab.client.Client.get_account_hash>`.
        '''
        if str(account_number) not in self._account_hashes:
            self._cache_account_hashes(await self.get_account_numbers())
        return self._cached_account_hash(account_number)

    async def _resolve_account_path(self, path):
        account_number = self._account_number_in_path(path)
        if account_number is None:
            return path
        return self._replace_account_number(
                path, await self.get_account_hash(account_number))

    async def _request(self, method, path, *, params=None, json_data=None):
        path = await self._resolve_account_path(path)
        dest = self.base_url + path

        req_num = self._req_num()
        self._log_request(req_num, method, dest, params, json_data)
        self._check_refresh_token_expiry()

        send = getattr(self.session, method.lower())
        kwargs = self._send_kwargs(method, params, json_data)

        attempt = 0
        while True:
            delay = self._reserve_request_slot()
            while delay > 0:
                self.logger.debug(
                        'Req %s: waiting %.1f seconds for the rate limit',
                        req_num, delay)
                await asyncio.sleep(delay)
                delay = self._reserve_request_slot()

            try:
                resp = await send(dest, **kwargs)
            except OAuthError as e:
                translated = self._translate_oauth_error(e)
                if translated is e:
                    raise
                raise translated from e
            self._log_response(resp, req_num, method)
            self._warn_on_known_errors(resp, req_num)

            if not self._should_retry(resp, attempt):
                break
            delay = self._retry_delay(resp, attempt)
            attempt += 1
            self.logger.warning(
                    'Req %s: rate limited by Schwab, retry %s of %s in %.1f '
                    'seconds', req_num, attempt, self._max_rate_limit_retries,
                    delay)
            await asyncio.sleep(delay)

        register_redactions_from_response(resp)
        return resp

    async def _get_request(self, path, params):
        return await self._request('GET', path, params=params)

    async def _post_request(self, path, data):
        return await self._request('POST', path, json_data=data)

    async def _put_request(self, path, data):
        return await self._request('PUT', path, json_data=data)

    async def _delete_request(self, path):
        return await self._request('DELETE', path)
