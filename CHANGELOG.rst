=========
Changelog
=========

Issue and pull request numbers refer to the upstream repository,
`alexgolec/schwab-py <https://github.com/alexgolec/schwab-py>`__.


1.6.0 (unreleased)
==================

Behavior changes
----------------

These fix real bugs but may be visible to existing code:

* **Stream handler errors no longer escape** ``handle_message()``. A handler
  that raises, whether a plain function or a coroutine, is logged and reported
  to the new ``handler_error_callback``, and the other handlers still receive
  the message. Previously a raising sync handler stopped dispatch to all other
  handlers, and a raising async handler failed silently (#233).
* **Stream requests time out** after 30 seconds by default, raising
  ``StreamResponseTimeout``, instead of waiting forever. Pass
  ``response_timeout=None`` to ``StreamClient`` for the old behavior (#237).
* ``StreamClient.logout()`` now also closes the connection (#235).
* ``websockets`` 13.0 or newer is required. The ``extra_headers`` connect
  argument is still accepted, with a ``DeprecationWarning``; use
  ``additional_headers`` (#225).
* Timezone-aware datetimes passed to ``get_orders_for_account``,
  ``get_orders_for_all_linked_accounts`` and ``get_transactions`` are converted
  to UTC. Previously their wall-clock time was sent as if it were UTC (#195).
* ``client_from_received_url``, and so the manual and login flows, raise
  ``InvalidRedirectURLError`` for a redirect URL without an authorization code,
  instead of silently fetching a token that can't be refreshed (#224).
* Clients created from a token file create a lock file next to it
  (``token.json.lock``) the first time they refresh the token (#94).
* Token files record whether they have been revoked. Older versions of
  schwab-py ignore the extra field.

Fixes
-----

* Installing schwab-py no longer installs a stray top-level ``tests`` package.
* Float prices already at the target precision, like ``8.2``, are no longer
  lowered by one tick. This affected about 5% of prices (#239).
* Response values such as account numbers are redacted from bug report logs
  again. Redaction was disabled by a leftover stub (#246).
* Token files are written atomically and readable only by their owner (#231).
* Clients created by the login and manual flows can refresh their tokens (#222,
  PR #223).
* ``extract_order_id`` works with responses from HTTP libraries other than
  httpx (#214).
* schwab-py uses the same HTTP library as authlib, which prefers ``httpx2``
  when it is installed (#268).
* ``easy_client`` honors ``asyncio=True`` when run in a notebook.
* DELETE requests are logged correctly, and responses are logged with their
  actual HTTP method.
* Removed the unused ``python-dateutil`` dependency and dead links to the
  defunct TD Ameritrade developer site.
* Fixed several documentation errors, including the missing
  ``client_from_access_functions`` example (#183, #216), the async client
  example (#188, #199) and the nonexistent ``statuses`` parameter (#195).

New features
------------

* ``StreamClient.reconnect()`` logs in again and restores all subscriptions
  after a dropped connection, and ``auto_reconnect=True`` makes
  ``handle_message()`` do so automatically, with exponential backoff.
* A warning explains how to narrow requests that Schwab rejects with
  ``Body buffer overflow`` because the response would be too large, as happens
  with full option chains for symbols like ``$SPX``.
* Optional client-side rate limiting with ``set_rate_limit()``, and automatic
  retries of requests rejected with HTTP 429 with ``set_rate_limit_retries()``.
* Clients warn as the seven-day refresh token expiry approaches, and can call
  a function you provide so you can log in again in time. Requests made with an
  expired refresh token raise ``RefreshTokenExpiredError``, a subclass of the
  ``OAuthError`` raised before.
* ``client_from_login_flow`` works on machines without a browser, printing the
  login URL instead of crashing (#166).
* Leading and trailing whitespace is stripped from API keys and secrets, with a
  warning (#152).
* Every method that takes an account hash also accepts the plain account
  number. Hashes are looked up and cached automatically; see
  ``Client.get_account_hash()`` (#6).
* Several scripts on one machine can share a token file. Clients adopt tokens
  refreshed or created by other processes, and refreshes are coordinated with a
  lock file so each happens once. On by default for token-file clients; pass
  ``share_token=False`` to disable (#94).
* Equity order templates for stop, stop limit, trailing stop,
  market-on-close and limit-on-close orders (#227).
* Straddle option templates (PR #179).
* ``OrderBuilder.set_price_offset`` for trailing stop limit orders (PR #173).
* ``set_price`` and ``set_stop_price`` accept ``decimal.Decimal``.
* ``Client.revoke()`` revokes the refresh token with Schwab (PR #267).
* ``base_url`` parameter on all client creation functions, for mock servers and
  proxies (#218, PR #219).
* ``StreamClient.close()`` and ``async with StreamClient(...)`` support (#235).
* ``client_from_access_functions_async`` for async token storage (PR #205).
* ``Session.EXTO`` for the extended overnight session (PR #213).
* ``LevelOneOptionFields.STRIKE_PRICE``, an accurately named alias for
  ``STRIKE_TYPE`` (#197).
* ``schwab-py[httpx2]`` extra.

Packaging
---------

* Packaging moved from ``setup.py`` to ``pyproject.toml``. Build with
  ``python -m build``.
* Removed the unused ``python-dateutil`` dependency.
