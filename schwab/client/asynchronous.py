from .base import BaseClient
from ..debug import register_redactions_from_response
from ..utils import LazyLog

from .._http import httpx
import json


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

    async def _get_request(self, path, params):
        path = await self._resolve_account_path(path)
        dest = self.base_url + path

        req_num = self._req_num()
        self.logger.debug('Req %s: GET to %s, params=%s',
                req_num, dest, LazyLog(lambda: json.dumps(params, indent=4)))

        resp = await self.session.get(dest, params=params)
        self._log_response(resp, req_num, 'GET')
        register_redactions_from_response(resp)
        return resp

    async def _post_request(self, path, data):
        path = await self._resolve_account_path(path)
        dest = self.base_url + path

        req_num = self._req_num()
        self.logger.debug('Req %s: POST to %s, json=%s',
                req_num, dest, LazyLog(lambda: json.dumps(data, indent=4)))

        resp = await self.session.post(dest, json=data)
        self._log_response(resp, req_num, 'POST')
        register_redactions_from_response(resp)
        return resp

    async def _put_request(self, path, data):
        path = await self._resolve_account_path(path)
        dest = self.base_url + path

        req_num = self._req_num()
        self.logger.debug('Req %s: PUT to %s, json=%s',
                req_num, dest, LazyLog(lambda: json.dumps(data, indent=4)))

        resp = await self.session.put(dest, json=data)
        self._log_response(resp, req_num, 'PUT')
        register_redactions_from_response(resp)
        return resp

    async def _delete_request(self, path):
        path = await self._resolve_account_path(path)
        dest = self.base_url + path

        req_num = self._req_num()
        self.logger.debug('Req %s: DELETE to %s', req_num, dest)

        resp = await self.session.delete(dest)
        self._log_response(resp, req_num, 'DELETE')
        register_redactions_from_response(resp)
        return resp
