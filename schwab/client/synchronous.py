from __future__ import annotations

from typing import Any

from .base import BaseClient
from ..debug import register_redactions_from_response
from authlib.integrations.base_client import OAuthError

from .._http import httpx
import time


class Client(BaseClient):
    def revoke(self) -> httpx.Response:
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

        resp = httpx.post(
                self.base_url + '/v1/oauth/revoke',
                data={'token': refresh_token,
                      'token_type_hint': 'refresh_token'},
                auth=(self.session.client_id, self.session.client_secret),
                timeout=30.0)

        if 200 <= resp.status_code < 300:
            self.token_metadata.mark_revoked()

        return resp

    def get_account_hash(self, account_number: str | int) -> str:
        '''Returns the account hash for an account number. Hashes are fetched
        with :meth:`get_account_numbers` the first time they are needed and
        cached; an unknown account number causes one refetch, in case the
        account was linked after the cache was filled.

        Every method that takes an account hash also accepts the plain account
        number and calls this method under the hood, so most users never need
        to call it directly.

        :raise ValueError: if the account number is not linked to this token.
        '''
        if str(account_number) not in self._account_hashes:
            self._cache_account_hashes(self.get_account_numbers())
        return self._cached_account_hash(account_number)

    def _resolve_account_path(self, path: str) -> str:
        account_number = self._account_number_in_path(path)
        if account_number is None:
            return path
        return self._replace_account_number(
                path, self.get_account_hash(account_number))

    def _request(self, method: str, path: str, *,
                 params: dict[str, Any] | None = None,
                 json_data: Any = None) -> httpx.Response:
        path = self._resolve_account_path(path)
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
                time.sleep(delay)
                delay = self._reserve_request_slot()

            try:
                resp = send(dest, **kwargs)
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
            time.sleep(delay)

        register_redactions_from_response(resp)
        return resp

    def _get_request(self, path: str,
                           params: dict[str, Any]) -> httpx.Response:
        return self._request('GET', path, params=params)

    def _post_request(self, path: str, data: Any) -> httpx.Response:
        return self._request('POST', path, json_data=data)

    def _put_request(self, path: str, data: Any) -> httpx.Response:
        return self._request('PUT', path, json_data=data)

    def _delete_request(self, path: str) -> httpx.Response:
        return self._request('DELETE', path)
