'''Implements additional functionality beyond what's implemented in the client
module.'''

from __future__ import annotations

import datetime
import re
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, NoReturn

from authlib.integrations.base_client import OAuthError


def class_fullname(o: type) -> str:
    return o.__module__ + '.' + o.__name__


class EnumEnforcer:
    def __init__(self, enforce_enums: bool) -> None:
        self.enforce_enums = enforce_enums

    def type_error(
            self, value: Any, required_enum_type: type[Enum]) -> NoReturn:
        possible_members_message = ''

        if isinstance(value, str):
            possible_members: list[str] = []
            for member in required_enum_type.__members__:
                fullname = class_fullname(required_enum_type) + '.' + member
                if value in fullname:
                    possible_members.append(fullname)

            # Oxford comma insertion
            if possible_members:
                possible_members_message = 'Did you mean ' + ', '.join(
                    possible_members[:-2] + [' or '.join(
                        possible_members[-2:])]) + '? '

        raise ValueError(
            ('expected type "{}", got type "{}". {}(initialize with ' +
             'enforce_enums=False to disable this checking)').format(
                required_enum_type.__name__,
                type(value).__name__,
                possible_members_message))

    def convert_enum(
            self, value: Any, required_enum_type: type[Enum]) -> Any:
        if value is None:
            return None

        if isinstance(value, required_enum_type):
            return value.value
        elif self.enforce_enums:
            self.type_error(value, required_enum_type)
        else:
            return value

    def convert_enum_iterable(
            self, iterable: Any,
            required_enum_type: type[Enum]) -> Any:
        if iterable is None:
            return None

        if isinstance(iterable, required_enum_type):
            return [iterable.value]

        values: list[Any] = []
        for value in iterable:
            if isinstance(value, required_enum_type):
                values.append(value.value)
            elif self.enforce_enums:
                self.type_error(value, required_enum_type)
            else:
                values.append(value)
        return values

    def set_enforce_enums(self, enforce_enums: bool) -> None:
        self.enforce_enums = enforce_enums


class UnsuccessfulOrderException(ValueError):
    '''
    Raised by :meth:`Utils.extract_order_id` when attempting to extract an
    order ID from a :meth:`Client.place_order` response that was not successful.
    '''


class AccountHashMismatchException(ValueError):
    '''
    Raised by :meth:`Utils.extract_order_id` when attempting to extract an
    order ID from a :meth:`Client.place_order` with a different account hash
    than the one with which the :class:`Utils` was initialized.
    '''


class AccountHashLookupError(Exception):
    '''
    Raised when a plain account number is passed where an account hash is
    expected and the account hashes could not be fetched from Schwab. The
    failed response is available as ``response``.
    '''
    def __init__(
            self, response: Any, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.response = response


class RefreshTokenExpiredError(OAuthError):
    '''
    Raised when a request fails because the refresh token has expired. Schwab
    refresh tokens expire seven days after they are created, after which the
    only remedy is to delete the token file and log in again. Subclasses
    authlib's ``OAuthError``, which was raised in this case before.
    '''


def trim_candles(price_history: Mapping[str, Any],
                 start_datetime: datetime.datetime | None = None,
                 end_datetime: datetime.datetime | None = None,
                 ) -> list[dict[str, Any]]:
    '''Returns the candles in a price history response that fall between
    ``start_datetime`` and ``end_datetime``, inclusive.

    For intraday frequencies, Schwab returns whole days of candles even when
    the requested range is narrower, so this is useful after calling the
    ``get_price_history_every_*`` methods with a range within a single day:

    .. code-block:: python

      resp = client.get_price_history_every_minute(
              'AAPL', start_datetime=start, end_datetime=end)
      candles = trim_candles(resp.json(), start, end)

    :param price_history: A decoded price history response, as returned by
                          ``resp.json()``.
    :param start_datetime: Drop candles before this time. Naive datetimes are
                           treated as local time, as elsewhere in the client.
    :param end_datetime: Drop candles after this time.
    '''
    start_millis = (None if start_datetime is None
                    else int(start_datetime.timestamp() * 1000))
    end_millis = (None if end_datetime is None
                  else int(end_datetime.timestamp() * 1000))

    return [
        candle for candle in price_history.get('candles', [])
        if (start_millis is None or candle['datetime'] >= start_millis)
        and (end_millis is None or candle['datetime'] <= end_millis)
    ]


class LazyLog:
    'Helper to defer evaluation of expensive variables in log messages'
    def __init__(self, func: Callable[[], str]) -> None:
        self.func = func
    def __str__(self) -> str:
        return self.func()


class Utils(EnumEnforcer):
    '''Helper for placing orders on equities. Provides easy-to-use
    implementations for common tasks such as market and limit orders.'''

    def __init__(self, client: Any, account_hash: str) -> None:
        '''Creates a new ``Utils`` instance. For convenience, this object
        assumes the user wants to work with a single account at a time.
        ``account_hash`` may also be a plain account number.'''
        super().__init__(True)

        self.client = client
        self.account_hash = account_hash

    def set_account_hash(self, account_hash: str) -> None:
        '''Set the account hash used by this ``Utils`` instance.'''
        self.account_hash = account_hash

    def extract_order_id(self, place_order_response: Any) -> int | None:
        '''Attempts to extract the order hash from a response object returned by
        :meth:`Client.place_order() <schwab.client.Client.place_order>`. Return
        ``None`` if the order location is not contained in the response.

        :param place_order_response: Order response as returned by
                                     :meth:`Client.place_order()
                                     <schwab.client.Client.place_order>`. Note this
                                     method requires that the order was
                                     successful.

        :raise ValueError: if the order was not successful or if the order's
                           account hash is not equal to the account hash set in this
                           ``Utils`` object.
        '''
        # Check the status code directly rather than using httpx's is_error so
        # that responses from other HTTP libraries, such as requests, work too.
        if place_order_response.status_code >= 400:
            raise UnsuccessfulOrderException(
                'order not successful: status {}'.format(place_order_response.status_code))

        try:
            location = place_order_response.headers['Location']
        except KeyError:
            return None

        m = re.match(
                r'https?://[^/]+(?:/[^/]+)*?/trader/v1/accounts/(\w+)/orders/(\d+)',
                location)

        if m is None:
            return None
        account_hash, order_id = m.group(1), int(m.group(2))

        # The account may have been given as a plain account number, which the
        # client resolved to a hash when the order was placed.
        expected_hash = str(self.account_hash)
        known_hashes = getattr(self.client, '_account_hashes', None)
        if isinstance(known_hashes, dict):
            expected_hash = known_hashes.get(expected_hash, expected_hash)

        if str(account_hash) != expected_hash:
            raise AccountHashMismatchException(
                'order request account hash != Utils.account_hash')

        return order_id
