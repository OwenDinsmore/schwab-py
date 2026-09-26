from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

import asyncio
import copy
from schwab._http import httpx
import inspect
import json
import logging
import warnings

import websockets.asyncio.client as ws_client
import websockets.exceptions

from .utils import EnumEnforcer, LazyLog

if TYPE_CHECKING:
    import ssl
    from types import TracebackType

    from schwab.client import AsyncClient, Client


Handler = Callable[[dict[str, Any]], Any]


class StreamJsonDecoder(ABC):
    @abstractmethod
    def decode_json_string(self, raw: str) -> Any:
        '''
        Parse a JSON-formatted string into a proper object. Raises
        ``JSONDecodeError`` on parse failure.
        '''
        raise NotImplementedError()


class NaiveJsonStreamDecoder(StreamJsonDecoder):
    def decode_json_string(self, raw: str) -> Any:
        return json.loads(raw)


def get_logger() -> logging.Logger:
    return logging.getLogger(__name__)


_E = TypeVar('_E', bound='_BaseFieldEnum')


class _BaseFieldEnum(Enum):
    _key_mapping: ClassVar[dict[str, str]]

    @classmethod
    def all_fields(cls: type[_E]) -> list[_E]:
        return list(cls)

    @classmethod
    def key_mapping(cls) -> dict[str, str]:
        try:
            return cls._key_mapping
        except AttributeError:
            cls._key_mapping = dict(
                (str(enum.value), name)
                for name, enum in cls.__members__.items())
            return cls._key_mapping

    @classmethod
    def relabel_message(cls, old_msg: dict[str, Any],
                        new_msg: dict[str, Any]) -> None:
        # Make a copy of the items so we can modify the dict during iteration
        for old_key, value in list(old_msg.items()):
            if old_key in cls.key_mapping():
                new_key = cls.key_mapping()[old_key]
                new_msg[new_key] = new_msg.pop(old_key)


class UnexpectedResponse(Exception):
    def __init__(self, response: dict[str, Any], *args: Any,
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.response = response


class UnexpectedResponseCode(Exception):
    def __init__(self, response: dict[str, Any], *args: Any,
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.response = response


class StreamResponseTimeout(Exception):
    '''
    Raised when the stream server accepts a request but does not answer it
    within the client's ``response_timeout``. This is distinct from the
    ``websockets`` exceptions raised when the connection itself fails: the
    socket is still open, so callers can choose to retry the operation or
    reconnect.
    '''
    def __init__(self, request_id: int, service: str, command: str,
                 *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.request_id = request_id
        self.service = service
        self.command = command


class UnparsableMessage(Exception):
    def __init__(self, raw_msg: str,
                 json_parse_exception: json.JSONDecodeError, *args: Any,
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.raw_msg = raw_msg
        self.json_parse_exception = json_parse_exception


class _Handler:
    def __init__(self, func: Handler,
                 field_enum_type: type[_BaseFieldEnum]) -> None:
        self._func = func
        self._field_enum_type = field_enum_type

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._func(*args, **kwargs)

    def label_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        if 'content' in msg:
            new_msg = copy.deepcopy(msg)
            for idx in range(len(msg['content'])):
                self._field_enum_type.relabel_message(msg['content'][idx],
                                                      new_msg['content'][idx])
            return new_msg
        else:
            return msg


class StreamClient(EnumEnforcer):

    def __init__(self, client: Client | AsyncClient, *,
                 account_id: str | None = None,
                 enforce_enums: bool = True,
                 ssl_context: ssl.SSLContext | None = None,
                 response_timeout: float | None = 30.0,
                 handler_error_callback: Callable[
                     [BaseException, dict[str, Any]], Any] | None = None,
                 auto_reconnect: bool = False,
                 max_reconnect_attempts: int | None = None) -> None:
        '''
        :param response_timeout: Maximum number of seconds to wait for the
                                 server to answer a request such as a login or
                                 subscription. Raises
                                 :class:`StreamResponseTimeout` when exceeded.
                                 Set to ``None`` to wait forever.
        :param handler_error_callback: Called as ``callback(exception,
                                       message)`` whenever a message handler
                                       raises, whether the handler is a plain
                                       function or a coroutine. Handler errors
                                       are always logged; the remaining
                                       handlers still run.
        :param auto_reconnect: If the connection is lost while
                               :meth:`handle_message` is waiting for a message,
                               reconnect and restore all subscriptions
                               instead of raising. See :meth:`reconnect`.
        :param max_reconnect_attempts: With ``auto_reconnect``, the number of
                                       consecutive failed reconnection attempts
                                       after which to give up and raise.
                                       ``None``, the default, retries forever,
                                       backing off up to a minute between
                                       attempts.
        '''
        super().__init__(enforce_enums)

        self._auto_reconnect = auto_reconnect
        self._max_reconnect_attempts = max_reconnect_attempts

        # Arguments of the most recent login(), reused when reconnecting
        self._websocket_connect_args: dict[str, Any] | None = None

        # Maps service names to the keys and fields currently subscribed, so
        # subscriptions can be restored after reconnecting
        self._subscriptions: dict[str, dict[str, Any]] = {}

        self._response_timeout = response_timeout
        self._handler_error_callback = handler_error_callback

        self._ssl_context = ssl_context
        self._client = client

        # Set by the login() function
        self._account = None
        self._stream_correl_id: Any = None
        self._stream_customer_id = None
        self._stream_channel = None
        self._stream_function_id = None
        self._socket: ws_client.ClientConnection | None = None

        # Internal fields
        self._request_id = 0
        self._handlers: defaultdict[str, list[_Handler]] = defaultdict(list)

        # Requests whose responses we stopped waiting for after a timeout. If
        # their responses arrive late, they are discarded instead of being
        # mistaken for the response to a later request.
        self._abandoned_request_ids: set[int] = set()

        # The event loop holds only weak references to tasks, so keep strong
        # references to async handler tasks until they finish.
        self._handler_tasks: set[asyncio.Future[Any]] = set()

        # When listening for responses, we sometimes encounter non-response
        # messages. Since this happens outside the context of the handler
        # dispatcher, we cannot handle these messages. However, we still need to
        # deliver these messages. This list records the messages that were read
        # from the stream but not handled yet. Messages should be read from this
        # list before they are read from the stream.
        self._overflow_items: deque[Any] = deque()

        # Logging-related fields
        self.logger = get_logger()
        self.request_number = 0

        # Initialize the JSON parser to be the naive parser which directly calls
        # ``json.loads``
        self.json_decoder: StreamJsonDecoder = NaiveJsonStreamDecoder()
        self._lock = asyncio.Lock()

    def set_json_decoder(self, json_decoder: StreamJsonDecoder) -> None:
        '''
        Sets a custom JSON decoder.

        :param json_decoder: Custom JSON decoder to use for to decode all
                             incoming JSON strings. See
                             :class:`StreamJsonDecoder` for details.
        '''
        if not isinstance(json_decoder, StreamJsonDecoder):
            raise ValueError('Custom JSON parser must be a subclass of ' +
                             'schwab.contrib.util.StreamJsonDecoder')
        self.json_decoder = json_decoder

    def req_num(self) -> int:
        self.request_number += 1
        return self.request_number

    async def _send(self, obj: dict[str, Any]) -> None:
        if self._socket is None:
            raise ValueError(
                'Socket not open. Did you forget to call login()?')

        self.logger.debug('Send %s: Sending %s',
                self.req_num(), LazyLog(lambda: json.dumps(obj, indent=4)))

        await self._socket.send(json.dumps(obj))

    async def _receive(self) -> Any:
        if self._socket is None:
            raise ValueError(
                'Socket not open. Did you forget to call login()?')

        if len(self._overflow_items) > 0:
            ret = self._overflow_items.pop()

            self.logger.debug(
                'Receive %s: Returning message from overflow: %s',
                self.req_num(), LazyLog(lambda: json.dumps(ret, indent=4)))
        else:
            raw: Any = await self._socket.recv()
            try:
                ret = self.json_decoder.decode_json_string(raw)
            except json.decoder.JSONDecodeError as e:
                msg = ('Failed to parse message. This often happens with ' +
                       'unknown symbols or other error conditions. Full ' +
                       'message text: ' + raw)
                raise UnparsableMessage(raw, e, msg)

            self.logger.debug(
                'Receive %s: Returning message from stream: %s',
                self.req_num(), LazyLog(lambda: json.dumps(ret, indent=4)))

        return ret

    async def _init_from_preferences(
            self, prefs: dict[str, Any],
            websocket_connect_args: Mapping[str, Any]) -> None:
        # Record streamer subscription keys
        stream_info = prefs['streamerInfo'][0]

        self._stream_correl_id = stream_info['schwabClientCorrelId']
        self._stream_customer_id = stream_info['schwabClientCustomerId']
        self._stream_channel = stream_info['schwabClientChannel']
        self._stream_function_id = stream_info['schwabClientFunctionId']

        # Initialize socket
        wss_url = stream_info['streamerSocketUrl']

        websocket_connect_args = dict(websocket_connect_args)
        if self._ssl_context:
            websocket_connect_args['ssl'] = self._ssl_context

        # websockets 14 renamed extra_headers. Accept the old name so existing
        # callers keep working.
        if 'extra_headers' in websocket_connect_args:
            warnings.warn(
                    'the extra_headers websocket connect argument is '
                    'deprecated, use additional_headers instead',
                    DeprecationWarning, stacklevel=3)
            websocket_connect_args['additional_headers'] = \
                    websocket_connect_args.pop('extra_headers')

        self._socket = await ws_client.connect(
                wss_url, **websocket_connect_args)


    def _make_request(
            self, *, service: str, command: str,
            parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
        request_id = self._request_id
        self._request_id += 1

        request = {
            'service': service,
            'requestid': str(request_id),
            'command': command,
            'SchwabClientCustomerId': self._stream_customer_id,
            'SchwabClientCorrelId': self._stream_correl_id,
            'parameters': parameters,
        }

        return request, request_id

    async def _send_and_await_response(self, request: dict[str, Any],
                                       request_id: int, service: str,
                                       command: str) -> None:
        '''
        Sends a request and waits for its response. Must be called with
        ``self._lock`` held.
        '''
        await self._send({'requests': [request]})
        try:
            await asyncio.wait_for(
                    self._await_response(request_id, service, command),
                    self._response_timeout)
        except asyncio.TimeoutError:
            self._abandoned_request_ids.add(request_id)
            raise StreamResponseTimeout(
                    request_id, service, command,
                    'no response to {} {} request {} after {} seconds'.format(
                        service, command, request_id,
                        self._response_timeout)) from None

    async def _await_response(self, request_id: int, service: str,
                              command: str) -> None:
        deferred_messages: list[Any] = []

        # Context handler to ensure we always append the deferred messages,
        # regardless of how we exit the await loop below
        class WriteDeferredMessages:
            def __init__(self, this_client: StreamClient) -> None:
                self.this_client = this_client

            def __enter__(self) -> WriteDeferredMessages:
                return self

            def __exit__(self, exc_type: type[BaseException] | None,
                         exc_val: BaseException | None,
                         exc_tb: TracebackType | None) -> None:
                self.this_client._overflow_items.extendleft(deferred_messages)

        with WriteDeferredMessages(self):
            while True:
                resp = await self._receive()

                if 'response' not in resp:
                    deferred_messages.append(resp)
                    continue

                # Validate request ID
                resp_request_id = int(resp['response'][0]['requestid'])
                if resp_request_id in self._abandoned_request_ids:
                    self._abandoned_request_ids.discard(resp_request_id)
                    self.logger.warning(
                            'Discarding late response to request %s',
                            resp_request_id)
                    continue
                if resp_request_id != request_id:
                    raise UnexpectedResponse(
                        resp, 'unexpected requestid: {}'.format(
                            resp_request_id))

                # Validate service
                resp_service = resp['response'][0]['service']
                if resp_service != service:
                    raise UnexpectedResponse(
                        resp, 'unexpected service: {}'.format(
                            resp_service))

                # Validate command
                resp_command = resp['response'][0]['command']
                if resp_command != command:
                    raise UnexpectedResponse(
                        resp, 'unexpected command: {}'.format(
                            resp_command))

                # Validate response code
                resp_code = resp['response'][0]['content']['code']
                if resp_code != 0:
                    raise UnexpectedResponseCode(
                        resp,
                        'unexpected response code: {}, msg is \'{}\''.format(
                            resp_code,
                            resp['response'][0]['content']['msg']))

                break

    async def _service_op(self, symbols: Iterable[str], service: str,
                          command: str,
                          field_type: type[_BaseFieldEnum] | None = None,
                          *,
                          fields: Iterable[_BaseFieldEnum] | None = None
                          ) -> None:
        parameters = {
            'keys': ','.join(symbols)
        }

        if field_type is not None:
            if fields is None:
                fields = field_type.all_fields()

            fields = sorted(self.convert_enum_iterable(fields, field_type))
            parameters['fields'] = ','.join(str(f) for f in fields)

        request, request_id = self._make_request(
            service=service, command=command,
            parameters=parameters)

        async with self._lock:
            await self._send_and_await_response(
                    request, request_id, service, command)

        self._record_subscription(service, command, parameters)

    def _record_subscription(self, service: str, command: str,
                             parameters: dict[str, Any]) -> None:
        keys = [k for k in parameters['keys'].split(',') if k]
        fields = parameters.get('fields')

        sub: dict[str, Any] | None
        if command == 'SUBS':
            self._subscriptions[service] = {'keys': keys, 'fields': fields}
        elif command == 'ADD':
            sub = self._subscriptions.setdefault(
                    service, {'keys': [], 'fields': fields})
            sub['keys'] += [k for k in keys if k not in sub['keys']]
            if fields is not None:
                sub['fields'] = fields
        elif command == 'VIEW':
            sub = self._subscriptions.get(service)
            if sub is not None and fields is not None:
                sub['fields'] = fields
        elif command == 'UNSUBS':
            sub = self._subscriptions.get(service)
            if sub is not None:
                sub['keys'] = [k for k in sub['keys'] if k not in keys]
                if not sub['keys']:
                    del self._subscriptions[service]

    ##########################################################################
    # RECONNECTING

    async def reconnect(self) -> None:
        '''
        Replaces the connection with a new one: closes the current connection,
        if any, logs in again using the arguments of the last :meth:`login`,
        and restores every subscription made since then. Handlers are kept.

        Use this to recover when the connection drops, for instance when
        :meth:`handle_message` raises
        ``websockets.exceptions.ConnectionClosed``. Pass
        ``auto_reconnect=True`` to the constructor to have
        :meth:`handle_message` do this for you.
        '''
        subscriptions = copy.deepcopy(self._subscriptions)

        await self._close_socket()
        await self.login(self._websocket_connect_args)

        for service, sub in subscriptions.items():
            keys = sub['keys']
            # Account activity is keyed by the stream correlation ID, which
            # changes with every login
            if service == 'ACCT_ACTIVITY':
                keys = [self._stream_correl_id]

            parameters = {'keys': ','.join(keys)}
            if sub['fields'] is not None:
                parameters['fields'] = sub['fields']

            request, request_id = self._make_request(
                    service=service, command='SUBS', parameters=parameters)
            async with self._lock:
                await self._send_and_await_response(
                        request, request_id, service, 'SUBS')
            self._record_subscription(service, 'SUBS', parameters)

        self.logger.info('Reconnected and restored %s subscription(s)',
                         len(subscriptions))

    async def _reconnect_with_backoff(self, cause: BaseException) -> None:
        attempt = 0
        while True:
            attempt += 1
            delay = min(2 ** (attempt - 1), 60)
            self.logger.warning(
                    'Stream connection lost (%s). Reconnecting in %s seconds, '
                    'attempt %s', cause, delay, attempt)
            await asyncio.sleep(delay)
            try:
                await self.reconnect()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if (self._max_reconnect_attempts is not None
                        and attempt >= self._max_reconnect_attempts):
                    raise
                cause = e

    async def handle_message(self) -> None:
        try:
            async with self._lock:
                msg = await self._receive()
        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            if not self._auto_reconnect or self._websocket_connect_args is None:
                raise
            await self._reconnect_with_backoff(e)
            return

        # response
        if 'response' in msg:
            raise UnexpectedResponse(msg,
                                     'unexpected response code during message handling: {}, msg is \'{}\''.format(
                                         msg['response'][0]['content']['code'],
                                         msg['response'][0]['content']['msg']))

        # data
        if 'data' in msg:
            for d in msg['data']:
                for handler in self._handlers.get(d.get('service'), ()):
                    self._dispatch(handler, d, label=True)

        # notify
        if 'notify' in msg:
            for d in msg['notify']:
                if 'heartbeat' in d:
                    continue
                for handler in self._handlers.get(d.get('service'), ()):
                    self._dispatch(handler, d, label=False)

    def _dispatch(self, handler: _Handler, msg: dict[str, Any], *,
                  label: bool) -> None:
        '''
        Calls a single handler. Exceptions from both sync and async handlers
        are reported through :meth:`_report_handler_error` and never prevent
        other handlers from running.
        '''
        try:
            h = handler(handler.label_message(msg) if label else msg)
        except Exception as e:
            self._report_handler_error(e, msg)
            return

        # Check if h is an awaitable, if so schedule it
        # This allows for both sync and async handlers
        if inspect.isawaitable(h):
            task = asyncio.ensure_future(h)
            self._handler_tasks.add(task)

            def on_done(task):
                self._handler_tasks.discard(task)
                if not task.cancelled() and task.exception() is not None:
                    self._report_handler_error(task.exception(), msg)
            task.add_done_callback(on_done)

    def _report_handler_error(self, exception: BaseException,
                              msg: dict[str, Any]) -> None:
        self.logger.error('Stream message handler raised an exception',
                          exc_info=exception)
        if self._handler_error_callback is not None:
            try:
                self._handler_error_callback(exception, msg)
            except Exception:
                self.logger.exception('Handler error callback raised')

    ##########################################################################
    # LOGIN

    async def login(
            self,
            websocket_connect_args: Mapping[str, Any] | None = None) -> None:
        '''
        Performs initial stream setup:
         * Fetches streaming information from the HTTP client's
           :meth:`~schwab.client.Client.get_user_preferences` method
         * Initializes the socket
         * Builds and sends and authentication request
         * Waits for response indicating login success

        All stream operations are available after this method completes.

        :param websocket_connect_args: ``dict`` of additional arguments to pass
                                       to the websocket ``connect`` call. Useful 
                                       for setting timeouts and other connection 
                                       parameters. See `the official 
                                       documentation <https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html#websockets.asyncio.client.connect>`__
                                       for details.
        '''

        self._websocket_connect_args = (
                dict(websocket_connect_args) if websocket_connect_args else {})
        self._subscriptions = {}

        # Fetch required data and initialize the client
        r: Any = self._client.get_user_preferences()

        # We don't actually know whether the client is synchronous or
        # asynchronous, so work around by awaiting the response if necessary
        if inspect.iscoroutine(r):
            r = await r
        assert r.status_code == httpx.codes.OK, r.raise_for_status()
        r = r.json()

        await self._init_from_preferences(
                r, websocket_connect_args if websocket_connect_args else {})

        # Build and send the request object
        request_parameters = {
                'Authorization': self._client.token_metadata.token['access_token'],
                'SchwabClientChannel': self._stream_channel,
                'SchwabClientFunctionId': self._stream_function_id,
        }

        request, request_id = self._make_request(
            service='ADMIN', command='LOGIN',
            parameters=request_parameters)
        async with self._lock:
            await self._send_and_await_response(
                    request, request_id, 'ADMIN', 'LOGIN')

    ##########################################################################
    # LOGOUT

    async def logout(self) -> None:
        '''
        Performs a logout operation on the stream and closes the connection.
        After this method is called, no further stream operations are possible.
        The client must be re-initialized with :meth:`login` to perform further
        operations.
        '''
        request, request_id = self._make_request(
            service='ADMIN', command='LOGOUT',
            parameters={})
        try:
            async with self._lock:
                await self._send_and_await_response(
                        request, request_id, 'ADMIN', 'LOGOUT')
        finally:
            await self.close()

    ##########################################################################
    # CLOSE

    async def close(self) -> None:
        '''
        Closes the underlying websocket connection without logging out. Safe to
        call more than once, and safe to call on a client that never logged
        in. Also called automatically when the client is used as an async
        context manager:

        .. code-block:: python

          async with StreamClient(client) as stream_client:
              await stream_client.login()
              ...
        '''
        self._subscriptions = {}
        await self._close_socket()

    async def _close_socket(self) -> None:
        socket, self._socket = self._socket, None
        self._overflow_items.clear()
        self._abandoned_request_ids.clear()
        if socket is not None:
            try:
                await socket.close()
            except Exception as e:
                # The connection may already be broken, which is often why
                # it's being closed
                self.logger.debug('Error closing stream connection: %s', e)

    async def __aenter__(self) -> StreamClient:
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None,
                        exc_val: BaseException | None,
                        exc_tb: TracebackType | None) -> None:
        await self.close()

    ##########################################################################
    # ACCT_ACTIVITY

    class AccountActivityFields(_BaseFieldEnum):
        '''
        Data fields for equity account activity. Primarily an implementation detail
        and not used in client code. Provided here as documentation for key
        values stored returned in the stream messages.
        '''

        #: Passed back to the client from the request to identify a subscription this response belongs to.
        SUBSCRIPTION_KEY = 0

        #: Account Number that the activity occurred on.
        ACCOUNT = 1

        #: Message Type that dictates the format of the Message Data field.
        MESSAGE_TYPE = 2

        #: The core data for the message. Either JSON-formatted data describing the update, NULL in some cases,
        #: or plain text in case of ERROR.
        MESSAGE_DATA = 3

    async def account_activity_sub(self) -> None:
        '''
        Subscribe to account activity for the account id associated with this
        streaming client. See :class:`AccountActivityFields` for more info.
        '''
        await self._service_op(
            [self._stream_correl_id], 'ACCT_ACTIVITY', 'SUBS',
            self.AccountActivityFields)

    async def account_activity_unsubs(self) -> None:
        '''
        Un-Subscribe to account activity for the account id associated with this
        streaming client. See :class:`AccountActivityFields` for more info.
        '''
        await self._service_op([self._stream_correl_id], 'ACCT_ACTIVITY', 'UNSUBS')

    def add_account_activity_handler(self, handler: Handler) -> None:
        '''
        Adds a handler to the account activity subscription. See
        :ref:`registering_handlers` for details.
        '''
        self._handlers['ACCT_ACTIVITY'].append(_Handler(handler,
                                                        self.AccountActivityFields))

    ##########################################################################
    # CHART_EQUITY

    class ChartEquityFields(_BaseFieldEnum):
        '''
        Data fields for equity OHLCV data. Primarily an implementation detail
        and not used in client code. Provided here as documentation for key
        values stored returned in the stream messages.
        '''

        #: Ticker symbol
        SYMBOL = 0

        #: Sequence number
        SEQUENCE = 1

        #: Today's open price
        OPEN_PRICE = 2

        #: Today's high price
        HIGH_PRICE = 3

        #: Today's low price
        LOW_PRICE = 4

        #: Previous day's close price
        CLOSE_PRICE = 5

        #: Today's trading volume
        VOLUME = 6

        #: Chart timestamp
        CHART_TIME_MILLIS = 7

        #: Chart day
        CHART_DAY = 8

    async def chart_equity_subs(self, symbols: Iterable[str]) -> None:
        '''
        Subscribe to equity charts. Behavior is undefined if called multiple
        times.

        :param symbols: Equity symbols to subscribe to.'''
        await self._service_op(
            symbols, 'CHART_EQUITY', 'SUBS', self.ChartEquityFields,
            fields=self.ChartEquityFields.all_fields())

    async def chart_equity_unsubs(self, symbols: Iterable[str]) -> None:
        '''
        Un-Subscribe to equity charts. Behavior is undefined if called multiple
        times.

        :param symbols: Equity symbols to subscribe to.'''
        await self._service_op(symbols, 'CHART_EQUITY', 'UNSUBS')

    async def chart_equity_add(self, symbols: Iterable[str]) -> None:
        '''
        Add a symbol to the equity charts subscription. Behavior is undefined
        if called before :meth:`chart_equity_subs`.

        :param symbols: Equity symbols to add to the subscription.
        '''
        await self._service_op(
            symbols, 'CHART_EQUITY', 'ADD', self.ChartEquityFields,
            fields=self.ChartEquityFields.all_fields())

    def add_chart_equity_handler(self, handler: Handler) -> None:
        '''
        Adds a handler to the equity chart subscription. See
        :ref:`registering_handlers` for details.
        '''
        self._handlers['CHART_EQUITY'].append(_Handler(handler,
                                                       self.ChartEquityFields))

    ##########################################################################
    # CHART_FUTURES

    class ChartFuturesFields(_BaseFieldEnum):
        '''
        Data fields for equity OHLCV data. Primarily an implementation detail
        and not used in client code. Provided here as documentation for key
        values stored returned in the stream messages.
        '''

        #: Ticker symbol in upper case.
        SYMBOL = 0

        #: Milliseconds since Epoch
        CHART_TIME_MILLIS = 1

        #: Opening price for the minute
        OPEN_PRICE = 2

        #: Highest price for the minute
        HIGH_PRICE = 3

        #: Chart's lowest price for the minute
        LOW_PRICE = 4

        #: Closing price for the minute
        CLOSE_PRICE = 5

        #: Total volume for the minute
        VOLUME = 6

    async def chart_futures_subs(self, symbols: Iterable[str]) -> None:
        '''
        Subscribe to futures charts. Behavior is undefined if called multiple
        times.

        :param symbols: Futures symbols to subscribe to.
        '''
        await self._service_op(
            symbols, 'CHART_FUTURES', 'SUBS', self.ChartFuturesFields,
            fields=self.ChartFuturesFields.all_fields())

    async def chart_futures_unsubs(self, symbols: Iterable[str]) -> None:
        '''
        Un-Subscribe to futures charts. Behavior is undefined if called multiple
        times.

        :param symbols: Futures symbols to subscribe to.
        '''
        await self._service_op(symbols, 'CHART_FUTURES', 'UNSUBS')

    async def chart_futures_add(self, symbols: Iterable[str]) -> None:
        '''
        Add a symbol to the futures chart subscription. Behavior is undefined
        if called before :meth:`chart_futures_subs`.

        :param symbols: Futures symbols to add to the subscription.
        '''
        await self._service_op(
            symbols, 'CHART_FUTURES', 'ADD', self.ChartFuturesFields,
            fields=self.ChartFuturesFields.all_fields())

    def add_chart_futures_handler(self, handler: Handler) -> None:
        '''
        Adds a handler to the futures chart subscription. See
        :ref:`registering_handlers` for details.
        '''
        self._handlers['CHART_FUTURES'].append(_Handler(handler,
                                                        self.ChartFuturesFields))

    ##########################################################################
    # LEVELONE_EQUITIES

    class LevelOneEquityFields(_BaseFieldEnum):
        '''
        Fields for equity quotes.
        '''

        #: Ticker symbol
        SYMBOL = 0

        #: Bid price
        BID_PRICE = 1

        #: Ask price
        ASK_PRICE = 2

        #: Last trade price
        LAST_PRICE = 3

        #: Size of the highest bid
        BID_SIZE = 4

        #: Size of the lowest ask
        ASK_SIZE = 5

        #: Exchange ID of the lowest ask
        ASK_ID = 6

        #: Exchange ID of the highest bid
        BID_ID = 7

        #: Total volume trade to date
        TOTAL_VOLUME = 8

        #: Size of the last trade
        LAST_SIZE = 9

        #: Daily high price
        HIGH_PRICE = 10

        #: Daily low price
        LOW_PRICE = 11

        #: Previous close price
        CLOSE_PRICE = 12

        #: Exchange ID
        EXCHANGE_ID = 13

        #: Is this equity marginable?
        MARGINABLE = 14

        #: Description
        DESCRIPTION = 15

        #: Exchange ID of the last trade
        LAST_ID = 16

        #: Today's open price
        OPEN_PRICE = 17

        #: Net change
        NET_CHANGE = 18

        #: 52 week high price
        HIGH_PRICE_52_WEEK = 19

        #: 52 week low price
        LOW_PRICE_52_WEEK = 20

        #: P/E ratio
        PE_RATIO = 21

        #: Dividend amount
        DIVIDEND_AMOUNT = 22

        #: Dividend yield
        DIVIDEND_YIELD = 23

        #: ETF net asset value
        NAV = 24

        #: Exchange name
        EXCHANGE_NAME = 25

        #: Dividend date
        DIVIDEND_DATE = 26

        #: Is this a regular market quote?
        REGULAR_MARKET_QUOTE = 27

        #: Is this a regular market trade?
        REGULAR_MARKET_TRADE = 28

        #: Regular market last price
        REGULAR_MARKET_LAST_PRICE = 29

        #: Regular market last size
        REGULAR_MARKET_LAST_SIZE = 30

        #: Regular market net change
        REGULAR_MARKET_NET_CHANGE = 31

        #: Security status
        SECURITY_STATUS = 32

        #: Mark
        MARK = 33

        #: Quote time in milliseconds
        QUOTE_TIME_MILLIS = 34

        #: Last trade time in milliseconds
        TRADE_TIME_MILLIS = 35

        #: Regular market trade time in milliseconds
        REGULAR_MARKET_TRADE_MILLIS = 36

        #: Bid time in millis
        BID_TIME_MILLIS = 37

        #: Ask time in millis
        ASK_TIME_MILLIS = 38

        #: Ask MIC ID
        ASK_MIC_ID = 39

        #: Bid MIC ID
        BID_MIC_ID = 40

        #: Last trade MIC ID
        LAST_MIC_ID = 41

        #: Net change in percent
        NET_CHANGE_PERCENT = 42

        #: Regular market change in percent
        REGULAR_MARKET_CHANGE_PERCENT = 43

        #: Mark change
        MARK_CHANGE = 44

        #: Mark change in percent
        MARK_CHANGE_PERCENT = 45

        #: HTB quantity
        HTB_QUANTITY = 46

        #: HTB rate
        HTB_RATE = 47

        #: Is this equity hard to borrow?
        HARD_TO_BORROW = 48

        #: Is this equity shortable
        IS_SHORTABLE = 49

        #: Post market net change
        POST_MARKET_NET_CHANGE = 50

        #: Post market net change percent
        POST_MARKET_NET_CHANGE_PERCENT = 51

    async def level_one_equity_subs(
            self, symbols: Iterable[str], *,
            fields: Iterable[StreamClient.LevelOneEquityFields] | None = None
    ) -> None:
        '''
        Subscribe to level one equity quote data.

        :param symbols: Equity symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneEquityFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields:
            # Copy, so the caller's list isn't modified
            fields = list(fields)
            if self.LevelOneEquityFields.SYMBOL not in fields:
                fields.append(self.LevelOneEquityFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_EQUITIES', 'SUBS', self.LevelOneEquityFields,
            fields=fields)

    async def level_one_equity_unsubs(self, symbols: Iterable[str]) -> None:
        '''
        Un-Subscribe to level one equity quote data.

        :param symbols: Equity symbols to receive quotes for
        '''

        await self._service_op(symbols, 'LEVELONE_EQUITIES', 'UNSUBS')

    async def level_one_equity_add(
            self, symbols: Iterable[str], *,
            fields: Iterable[StreamClient.LevelOneEquityFields] | None = None
    ) -> None:
        '''
        Add symbols to the list to receive quotes for.

        :param symbols: Equity symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneEquityFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields:
            # Copy, so the caller's list isn't modified
            fields = list(fields)
            if self.LevelOneEquityFields.SYMBOL not in fields:
                fields.append(self.LevelOneEquityFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_EQUITIES', 'ADD',
            self.LevelOneEquityFields, fields=fields)

    async def level_one_equity_view(
            self, symbols: Iterable[str],
            fields: Iterable[StreamClient.LevelOneEquityFields]) -> None:
        '''
        Change the fields received for equity symbols that are already
        subscribed, without resubscribing. The symbol field is always
        included.

        :param symbols: Subscribed symbols whose fields to change.
        :param fields: Iterable of :class:`LevelOneEquityFields` representing the fields to
                       return in streaming entries from now on.
        '''
        # Copy, so the caller's list isn't modified
        fields = list(fields)
        if self.LevelOneEquityFields.SYMBOL not in fields:
            fields.append(self.LevelOneEquityFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_EQUITIES', 'VIEW',
            self.LevelOneEquityFields, fields=fields)

    def add_level_one_equity_handler(self, handler: Handler) -> None:
        '''
        Register a function to handle level one equity quotes as they are sent.
        See :ref:`registering_handlers` for details.
        '''
        self._handlers['LEVELONE_EQUITIES'].append(
                _Handler(handler, self.LevelOneEquityFields))

    ##########################################################################
    # LEVELONE_OPTIONS

    class LevelOneOptionFields(_BaseFieldEnum):
        '''
        '''

        #: Option symbol
        SYMBOL = 0

        #: Description
        DESCRIPTION = 1

        #: Highest bid price
        BID_PRICE = 2

        #: Lowest ask price
        ASK_PRICE = 3

        #: Last trade price
        LAST_PRICE = 4

        #: Today's high price
        HIGH_PRICE = 5

        #: Today's low price
        LOW_PRICE = 6

        #: Last close price
        CLOSE_PRICE = 7

        #: Today's total volume
        TOTAL_VOLUME = 8

        #: Open interest
        OPEN_INTEREST = 9

        #: Volatility
        VOLATILITY = 10

        #: Money intrinsic value
        MONEY_INTRINSIC_VALUE = 11

        #: Expiration year
        EXPIRATION_YEAR = 12

        #: Multiplier
        MULTIPLIER = 13

        #: Digits
        DIGITS = 14

        #: Open price
        OPEN_PRICE = 15

        #: Highest bid size
        BID_SIZE = 16

        #: Lowest ask size
        ASK_SIZE = 17

        #: Last trade size
        LAST_SIZE = 18

        #: Net change
        NET_CHANGE = 19

        #: Strike price
        STRIKE_PRICE = 20

        #: Deprecated alias of ``STRIKE_PRICE``, kept so existing code keeps
        #: working. Relabeled messages still use this name as the key until the
        #: next major version. This field has always held the strike price.
        STRIKE_TYPE = 20

        #: Contract type
        CONTRACT_TYPE = 21

        #: Underlying symbol
        UNDERLYING = 22

        #: Expiration month
        EXPIRATION_MONTH = 23

        #: Deliverables
        DELIVERABLES = 24

        #: Time value
        TIME_VALUE = 25

        #: Expiration day
        EXPIRATION_DAY = 26

        #: Days to expiration
        DAYS_TO_EXPIRATION = 27

        #: Delta
        DELTA = 28

        #: Gamma
        GAMMA = 29

        #: Theta
        THETA = 30

        #: Vega
        VEGA = 31

        #: Rho
        RHO = 32

        #: Security status
        SECURITY_STATUS = 33

        #: Theoretical option value
        THEORETICAL_OPTION_VALUE = 34

        #: Underlying price
        UNDERLYING_PRICE = 35

        #: UV expiration type
        UV_EXPIRATION_TYPE = 36

        #: Mark
        MARK = 37

        #: Quote time in millis
        QUOTE_TIME_MILLIS = 38

        #: Last trade time in millis
        TRADE_TIME_MILLIS = 39

        #: Exchange ID
        EXCHANGE_ID = 40

        #: Exchange name
        EXCHANGE_NAME = 41

        #: Last trading day
        LAST_TRADING_DAY = 42

        #: Settlement type
        SETTLEMENT_TYPE = 43

        #: Net percent change
        NET_PERCENT_CHANGE = 44

        #: Mark change
        MARK_CHANGE = 45

        #: Mark change in percent
        MARK_CHANGE_PERCENT = 46

        #: Implied yield
        IMPLIED_YIELD = 47

        #: Is penny stock?
        IS_PENNY = 48

        #: Option root
        OPTION_ROOT = 49

        #: 52 week high price
        HIGH_PRICE_52_WEEK = 50

        #: 52 week low price
        LOW_PRICE_52_WEEK = 51

        #: Indicative asking price
        INDICATIVE_ASKING_PRICE = 52

        #: Indicative bid price
        INDICATIVE_BID_PRICE = 53

        #: Indicative quote time
        INDICATIVE_QUOTE_TIME = 54

        #: Exercise type
        EXERCISE_TYPE = 55

    async def level_one_option_subs(
            self, symbols: Iterable[str], *,
            fields: Iterable[StreamClient.LevelOneOptionFields] | None = None
    ) -> None:
        '''
        Subscribe to level one option quote data.

        :param symbols: Option symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneOptionFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields:
            # Copy, so the caller's list isn't modified
            fields = list(fields)
            if self.LevelOneOptionFields.SYMBOL not in fields:
                fields.append(self.LevelOneOptionFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_OPTIONS', 'SUBS', self.LevelOneOptionFields,
            fields=fields)

    async def level_one_option_unsubs(self, symbols: Iterable[str]) -> None:
        '''
        Un-Subscribe to level one option quote data.

        :param symbols: Option symbols to receive quotes for
        '''
        await self._service_op(symbols, 'LEVELONE_OPTIONS', 'UNSUBS')

    async def level_one_option_add(
            self, symbols: Iterable[str], *,
            fields: Iterable[StreamClient.LevelOneOptionFields] | None = None
    ) -> None:
        '''
        Add symbols to the list to receive quotes for.

        :param symbols: Option symbols to add to list to receive quotes for
        :param fields: Iterable of :class:`LevelOneOptionFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields:
            # Copy, so the caller's list isn't modified
            fields = list(fields)
            if self.LevelOneOptionFields.SYMBOL not in fields:
                fields.append(self.LevelOneOptionFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_OPTIONS', 'ADD',
            self.LevelOneOptionFields, fields=fields)

    async def level_one_option_view(
            self, symbols: Iterable[str],
            fields: Iterable[StreamClient.LevelOneOptionFields]) -> None:
        '''
        Change the fields received for option symbols that are already
        subscribed, without resubscribing. The symbol field is always
        included.

        :param symbols: Subscribed symbols whose fields to change.
        :param fields: Iterable of :class:`LevelOneOptionFields` representing the fields to
                       return in streaming entries from now on.
        '''
        # Copy, so the caller's list isn't modified
        fields = list(fields)
        if self.LevelOneOptionFields.SYMBOL not in fields:
            fields.append(self.LevelOneOptionFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_OPTIONS', 'VIEW',
            self.LevelOneOptionFields, fields=fields)

    def add_level_one_option_handler(self, handler: Handler) -> None:
        '''
        Register a function to handle level one options quotes as they are sent.
        See :ref:`registering_handlers` for details.
        '''
        self._handlers['LEVELONE_OPTIONS'].append(
                _Handler(handler, self.LevelOneOptionFields))

    ##########################################################################
    # LEVELONE_FUTURES

    class LevelOneFuturesFields(_BaseFieldEnum):
        '''
        '''

        #: Ticker symbol in upper case.
        SYMBOL = 0

        #: Current Best Bid Price
        BID_PRICE = 1

        #: Current Best Ask Price
        ASK_PRICE = 2

        #: Price at which the last trade was matched
        LAST_PRICE = 3

        #: Number of contracts for bid
        BID_SIZE = 4

        #: Number of contracts for ask
        ASK_SIZE = 5

        #: Exchange with the best bid
        BID_ID = 6

        #: Exchange with the best ask
        ASK_ID = 7

        #: Aggregated contracts traded throughout the day, including pre/post market hours.
        TOTAL_VOLUME = 8

        #: Number of contracts traded with last trade
        LAST_SIZE = 9

        #: Time of the last quote in milliseconds since epoch
        QUOTE_TIME_MILLIS = 10

        #: Time of the last trade in milliseconds since epoch
        TRADE_TIME_MILLIS = 11

        #: Day's high trade price
        HIGH_PRICE = 12

        #: Day's low trade price
        LOW_PRICE = 13

        #: Previous day's closing price
        CLOSE_PRICE = 14

        #: Primary "listing" Exchange
        EXCHANGE_ID = 15

        #: Description of the product
        DESCRIPTION = 16

        #: Exchange where last trade was executed
        LAST_ID = 17

        #: Day's Open Price
        OPEN_PRICE = 18

        #: Current Last-Prev Close
        NET_CHANGE = 19

        #: Current percent change
        FUTURE_CHANGE_PERCENT = 20

        #: Name of exchange
        EXCHANGE_NAME = 21

        #: Trading status of the symbol
        SECURITY_STATUS = 22

        #: The total number of futures contracts that are not closed or delivered on a particular day
        OPEN_INTEREST = 23

        #: Mark-to-Market value is calculated daily using current prices to determine profit/loss
        MARK = 24

        #: Minimum price movement
        TICK = 25

        #: Minimum amount that the price of the market can change
        TICK_AMOUNT = 26

        #: Futures product
        PRODUCT = 27

        #: Display in fraction or decimal format.
        FUTURE_PRICE_FORMAT = 28

        #: Trading hours
        FUTURE_TRADING_HOURS = 29

        #: Flag to indicate if this future contract is tradable
        FUTURE_IS_TRADABLE = 30

        #: Point value
        FUTURE_MULTIPLIER = 31

        #: Indicates if this contract is active
        FUTURE_IS_ACTIVE = 32

        #: Closing price
        FUTURE_SETTLEMENT_PRICE = 33

        #: Symbol of the active contract
        FUTURE_ACTIVE_SYMBOL = 34

        #: Expiration date of this contract
        FUTURE_EXPIRATION_DATE = 35

        #: Expiration Style
        EXPIRATION_STYLE = 36

        #: Time of the last ask-side quote in milliseconds since epoch
        ASK_TIME_MILLIS = 37

        #: Time of the last bid-side quote in milliseconds since epoch
        BID_TIME_MILLIS = 38

        #: Indicates if this contract has quoted during the active session
        QUOTED_IN_SESSION = 39

        #: Expiration date of this contract
        SETTLEMENT_DATE = 40

    async def level_one_futures_subs(
            self, symbols: Iterable[str], *,
            fields: Iterable[StreamClient.LevelOneFuturesFields] | None = None
    ) -> None:
        '''
        Subscribe to level one futures quote data.

        :param symbols: Futures symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneFuturesFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields:
            # Copy, so the caller's list isn't modified
            fields = list(fields)
            if self.LevelOneFuturesFields.SYMBOL not in fields:
                fields.append(self.LevelOneFuturesFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES', 'SUBS', self.LevelOneFuturesFields,
            fields=fields)

    async def level_one_futures_unsubs(self, symbols: Iterable[str]) -> None:
        '''
        Un-Subscribe to level one futures quote data.

        :param symbols: Futures symbols to receive quotes for
        '''

        await self._service_op(symbols, 'LEVELONE_FUTURES', 'UNSUBS')

    async def level_one_futures_add(
            self, symbols: Iterable[str], *,
            fields: Iterable[StreamClient.LevelOneFuturesFields] | None = None
    ) -> None:
        '''
        Add symbols to the list to receive quotes for.

        :param symbols: Futures symbols to add to the list to receive quotes for
        :param fields: Iterable of :class:`LevelOneFuturesFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields:
            # Copy, so the caller's list isn't modified
            fields = list(fields)
            if self.LevelOneFuturesFields.SYMBOL not in fields:
                fields.append(self.LevelOneFuturesFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES', 'ADD',
            self.LevelOneFuturesFields, fields=fields)

    async def level_one_futures_view(
            self, symbols: Iterable[str],
            fields: Iterable[StreamClient.LevelOneFuturesFields]) -> None:
        '''
        Change the fields received for futures symbols that are already
        subscribed, without resubscribing. The symbol field is always
        included.

        :param symbols: Subscribed symbols whose fields to change.
        :param fields: Iterable of :class:`LevelOneFuturesFields` representing the fields to
                       return in streaming entries from now on.
        '''
        # Copy, so the caller's list isn't modified
        fields = list(fields)
        if self.LevelOneFuturesFields.SYMBOL not in fields:
            fields.append(self.LevelOneFuturesFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES', 'VIEW',
            self.LevelOneFuturesFields, fields=fields)

    def add_level_one_futures_handler(self, handler: Handler) -> None:
        '''
        Register a function to handle level one futures quotes as they are sent.
        See :ref:`registering_handlers` for details.
        '''
        self._handlers['LEVELONE_FUTURES'].append(
            _Handler(handler, self.LevelOneFuturesFields))

    ##########################################################################
    # LEVELONE_FOREX

    class LevelOneForexFields(_BaseFieldEnum):
        '''
        '''

        #: Ticker symbol in upper case.
        SYMBOL = 0

        #: Current Bid Price
        BID_PRICE = 1

        #: Current Ask Price
        ASK_PRICE = 2

        #: Price at which the last trade was matched
        LAST_PRICE = 3

        #: Number of currency pairs for bid
        BID_SIZE = 4

        #: Number of currency pairs for ask
        ASK_SIZE = 5

        #: Aggregated currency pairs traded throughout the day, including pre/post market hours.
        TOTAL_VOLUME = 6

        #: Number of currency pairs traded with last trade
        LAST_SIZE = 7

        #: Trade time of the last quote in milliseconds since epoch
        QUOTE_TIME_MILLIS = 8

        #: Trade time of the last trade in milliseconds since epoch
        TRADE_TIME_MILLIS = 9

        #: Day's high trade price
        HIGH_PRICE = 10

        #: Day's low trade price
        LOW_PRICE = 11

        #: Previous day's closing price
        CLOSE_PRICE = 12

        #: Exchange Id
        EXCHANGE_ID = 13

        #: Description of the product
        DESCRIPTION = 14

        #: Day's Open Price
        OPEN_PRICE = 15

        #: Current Last-Prev Close
        NET_CHANGE = 16

        #: Current percent change
        CHANGE_PERCENT = 17

        #: Name of exchange
        EXCHANGE_NAME = 18

        #: Valid decimal points
        DIGITS = 19

        #: Trading status of the symbol
        SECURITY_STATUS = 20

        #: Minimum price movement
        TICK = 21

        #: Minimum amount that the price of the market can change
        TICK_AMOUNT = 22

        #: Product name
        PRODUCT = 23

        #: Trading hours
        TRADING_HOURS = 24

        #: Flag to indicate if this forex is tradable
        IS_TRADABLE = 25

        #: Market Maker
        MARKET_MAKER = 26

        #: Highest price traded in the past 12 months, or 52 weeks
        HIGH_PRICE_52_WEEK = 27

        #: Lowest price traded in the past 12 months, or 52 weeks
        LOW_PRICE_52_WEEK = 28

        #: Mark-to-Market value is calculated daily using current prices to determine profit/loss
        MARK = 29

    async def level_one_forex_subs(
            self, symbols: Iterable[str], *,
            fields: Iterable[StreamClient.LevelOneForexFields] | None = None
    ) -> None:
        '''
        Subscribe to level one forex quote data.

        :param symbols: Forex symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneForexFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields:
            # Copy, so the caller's list isn't modified
            fields = list(fields)
            if self.LevelOneForexFields.SYMBOL not in fields:
                fields.append(self.LevelOneForexFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FOREX', 'SUBS', self.LevelOneForexFields,
            fields=fields)

    async def level_one_forex_unsubs(self, symbols: Iterable[str]) -> None:
        '''
        Un-Subscribe to level one forex quote data.

        :param symbols: Forex symbols to receive quotes for
        '''

        await self._service_op(symbols, 'LEVELONE_FOREX', 'UNSUBS')

    async def level_one_forex_add(
            self, symbols: Iterable[str], *,
            fields: Iterable[StreamClient.LevelOneForexFields] | None = None
    ) -> None:
        '''
        Add symbols to the list to receive quotes for.

        :param symbols: Forex symbols to add to list to receive quotes for
        :param fields: Iterable of :class:`LevelOneForexFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.

        '''
        if fields:
            # Copy, so the caller's list isn't modified
            fields = list(fields)
            if self.LevelOneForexFields.SYMBOL not in fields:
                fields.append(self.LevelOneForexFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FOREX', 'ADD',
            self.LevelOneForexFields, fields=fields)

    async def level_one_forex_view(
            self, symbols: Iterable[str],
            fields: Iterable[StreamClient.LevelOneForexFields]) -> None:
        '''
        Change the fields received for forex symbols that are already
        subscribed, without resubscribing. The symbol field is always
        included.

        :param symbols: Subscribed symbols whose fields to change.
        :param fields: Iterable of :class:`LevelOneForexFields` representing the fields to
                       return in streaming entries from now on.
        '''
        # Copy, so the caller's list isn't modified
        fields = list(fields)
        if self.LevelOneForexFields.SYMBOL not in fields:
            fields.append(self.LevelOneForexFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FOREX', 'VIEW',
            self.LevelOneForexFields, fields=fields)

    def add_level_one_forex_handler(self, handler: Handler) -> None:
        '''
        Register a function to handle level one forex quotes as they are sent.
        See :ref:`registering_handlers` for details.
        '''
        self._handlers['LEVELONE_FOREX'].append(_Handler(handler,
                                                         self.LevelOneForexFields))

    ##########################################################################
    # LEVELONE_FUTURES_OPTIONS

    class LevelOneFuturesOptionsFields(_BaseFieldEnum):
        '''
        '''

        #: Ticker symbol in upper case.
        SYMBOL = 0

        #: Current Bid Price
        BID_PRICE = 1

        #: Current Ask Price
        ASK_PRICE = 2

        #: Price at which the last trade was matched
        LAST_PRICE = 3

        #: Number of contracts for bid
        BID_SIZE = 4

        #: Number of contracts for ask
        ASK_SIZE = 5

        #: Exchange with the bid
        BID_ID = 6

        #: Exchange with the ask
        ASK_ID = 7

        #: Aggregated contracts traded throughout the day, including pre/post market hours.
        TOTAL_VOLUME = 8

        #: Number of contracts traded with last trade
        LAST_SIZE = 9

        #: Trade time of the last quote in milliseconds since epoch
        QUOTE_TIME_MILLIS = 10

        #: Trade time of the last trade in milliseconds since epoch
        TRADE_TIME_MILLIS = 11

        #: Day's high trade price
        HIGH_PRICE = 12

        #: Day's low trade price
        LOW_PRICE = 13

        #: Previous day's closing price
        CLOSE_PRICE = 14

        #: Exchange where last trade was executed
        LAST_ID = 15

        #: Description of the product
        DESCRIPTION = 16

        #: Day's Open Price
        OPEN_PRICE = 17

        #: Open Interest
        OPEN_INTEREST = 18

        #: Mark-to-Market value is calculated daily using current prices to determine profit/loss
        MARK = 19

        #: Minimum price movement
        TICK = 20

        #: Minimum amount that the price of the market can change
        TICK_AMOUNT = 21

        #: Point value
        FUTURE_MULTIPLIER = 22

        #: Closing price
        FUTURE_SETTLEMENT_PRICE = 23

        #: Underlying symbol
        UNDERLYING_SYMBOL = 24

        #: Strike Price
        STRIKE_PRICE = 25

        #: Expiration date of this contract
        FUTURE_EXPIRATION_DATE = 26

        #: Expiration Style
        EXPIRATION_STYLE = 27

        #: Contract Type
        CONTRACT_TYPE = 28

        #: Security Status
        SECURITY_STATUS = 29

        #: Exchange character
        EXCHANGE_ID = 30

        #: Display name of exchange
        EXCHANGE_NAME = 31

    async def level_one_futures_options_subs(
            self, symbols: Iterable[str], *,
            fields: Iterable[StreamClient.LevelOneFuturesOptionsFields]
            | None = None) -> None:
        '''
        Subscribe to level one futures options quote data.

        :param symbols: Futures options symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneFuturesOptionsFields`
                       representing the fields to return in streaming entries.
                       If unset, all fields will be requested.
        '''
        if fields:
            # Copy, so the caller's list isn't modified
            fields = list(fields)
            if self.LevelOneFuturesOptionsFields.SYMBOL not in fields:
                fields.append(self.LevelOneFuturesOptionsFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES_OPTIONS', 'SUBS',
            self.LevelOneFuturesOptionsFields, fields=fields)

    async def level_one_futures_options_unsubs(
            self, symbols: Iterable[str]) -> None:
        '''
        Un-Subscribe to level one futures options quote data.

        :param symbols: Futures options symbols to receive quotes for
        '''

        await self._service_op(symbols, 'LEVELONE_FUTURES_OPTIONS', 'UNSUBS')

    async def level_one_futures_options_add(
            self, symbols: Iterable[str], *,
            fields: Iterable[StreamClient.LevelOneFuturesOptionsFields]
            | None = None) -> None:
        '''
        Add symbols to the list to receive quotes for.

        :param symbols: Futures options symbols add to list to receive quotes for
        :param fields: Iterable of :class:`LevelOneFuturesOptionsFields`
                       representing the fields to return in streaming entries.
                       If unset, all fields will be requested.
        '''
        if fields:
            # Copy, so the caller's list isn't modified
            fields = list(fields)
            if self.LevelOneFuturesOptionsFields.SYMBOL not in fields:
                fields.append(self.LevelOneFuturesOptionsFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES_OPTIONS', 'ADD',
            self.LevelOneFuturesOptionsFields, fields=fields)

    async def level_one_futures_options_view(
            self, symbols: Iterable[str],
            fields: Iterable[StreamClient.LevelOneFuturesOptionsFields]
            ) -> None:
        '''
        Change the fields received for futures options symbols that are
        already subscribed, without resubscribing. The symbol field is always
        included.

        :param symbols: Subscribed symbols whose fields to change.
        :param fields: Iterable of :class:`LevelOneFuturesOptionsFields`
                       representing the fields to return in streaming entries
                       from now on.
        '''
        # Copy, so the caller's list isn't modified
        fields = list(fields)
        if self.LevelOneFuturesOptionsFields.SYMBOL not in fields:
            fields.append(self.LevelOneFuturesOptionsFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES_OPTIONS', 'VIEW',
            self.LevelOneFuturesOptionsFields, fields=fields)

    def add_level_one_futures_options_handler(self, handler: Handler) -> None:
        '''
        Register a function to handle level one futures options quotes as they
        are sent. See :ref:`registering_handlers` for details.
        '''
        self._handlers['LEVELONE_FUTURES_OPTIONS'].append(
            _Handler(handler, self.LevelOneFuturesOptionsFields))

    ##########################################################################
    # Common book utilities

    class BookFields(_BaseFieldEnum):
        SYMBOL = 0
        BOOK_TIME = 1
        BIDS = 2
        ASKS = 3

    class BidFields(_BaseFieldEnum):
        BID_PRICE = 0
        TOTAL_VOLUME = 1
        NUM_BIDS = 2
        BIDS = 3

    class PerExchangeBidFields(_BaseFieldEnum):
        EXCHANGE = 0
        BID_VOLUME = 1
        SEQUENCE = 2

    class AskFields(_BaseFieldEnum):
        ASK_PRICE = 0
        TOTAL_VOLUME = 1
        NUM_ASKS = 2
        ASKS = 3

    class PerExchangeAskFields(_BaseFieldEnum):
        EXCHANGE = 0
        ASK_VOLUME = 1
        SEQUENCE = 2

    class _BookHandler(_Handler):
        def label_message(self, msg: dict[str, Any]) -> dict[str, Any]:
            # Relabel top-level fields
            new_msg = super().label_message(msg)

            # Relabel bids
            for content in new_msg['content']:
                if 'BIDS' in content:
                    for bid in content['BIDS']:
                        # Relabel top-level bids
                        StreamClient.BidFields.relabel_message(bid, bid)

                        # Relabel per-exchange bids
                        for e_bid in bid['BIDS']:
                            StreamClient.PerExchangeBidFields.relabel_message(
                                e_bid, e_bid)

            # Relabel asks
            for content in new_msg['content']:
                if 'ASKS' in content:
                    for ask in content['ASKS']:
                        # Relabel top-level asks
                        StreamClient.AskFields.relabel_message(ask, ask)

                        # Relabel per-exchange bids
                        for e_ask in ask['ASKS']:
                            StreamClient.PerExchangeAskFields.relabel_message(
                                e_ask, e_ask)

            return new_msg

    ##########################################################################
    # NYSE_BOOK

    async def nyse_book_subs(self, symbols: Iterable[str]) -> None:
        '''
        Subscribe to the NYSE level two order book.

        :param symbols: NYSE symbols to subscribe to.
        '''
        await self._service_op(
            symbols, 'NYSE_BOOK', 'SUBS',
            self.BookFields, fields=self.BookFields.all_fields())

    async def nyse_book_unsubs(self, symbols: Iterable[str]) -> None:
        '''
        Un-Subscribe to the NYSE level two order book.

        :param symbols: NYSE symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'NYSE_BOOK', 'UNSUBS')

    async def nyse_book_add(self, symbols: Iterable[str]) -> None:
        '''
        Add to the NYSE level two order book.

        :param symbols: NYSE symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'NYSE_BOOK', 'ADD', self.BookFields)

    def add_nyse_book_handler(self, handler: Handler) -> None:
        '''
        Register a function to handle level two NYSE book data as it is updated
        See :ref:`registering_handlers` for details.
        '''
        self._handlers['NYSE_BOOK'].append(
            self._BookHandler(handler, self.BookFields))

    ##########################################################################
    # NASDAQ_BOOK

    async def nasdaq_book_subs(self, symbols: Iterable[str]) -> None:
        '''
        Subscribe to the NASDAQ level two order book.

        :param symbols: NASDAQ symbols to subscribe to.
        '''
        await self._service_op(symbols, 'NASDAQ_BOOK', 'SUBS',
                               self.BookFields,
                               fields=self.BookFields.all_fields())

    async def nasdaq_book_unsubs(self, symbols: Iterable[str]) -> None:
        '''
        Un-Subscribe to the NASDAQ level two order book.

        :param symbols: NASDAQ symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'NASDAQ_BOOK', 'UNSUBS')

    async def nasdaq_book_add(self, symbols: Iterable[str]) -> None:
        '''
        Add to the NASDAQ level two order book.

        :param symbols: NASDAQ symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'NASDAQ_BOOK', 'ADD', self.BookFields)

    def add_nasdaq_book_handler(self, handler: Handler) -> None:
        '''
        Register a function to handle level two NASDAQ book data as it is
        updated See :ref:`registering_handlers` for details.
        '''
        self._handlers['NASDAQ_BOOK'].append(
            self._BookHandler(handler, self.BookFields))

    ##########################################################################
    # OPTIONS_BOOK

    async def options_book_subs(self, symbols: Iterable[str]) -> None:
        '''
        Subscribe to the level two order book for options.

        :param symbols: Option symbols to subscribe to.
        '''
        await self._service_op(symbols, 'OPTIONS_BOOK', 'SUBS',
                               self.BookFields,
                               fields=self.BookFields.all_fields())

    async def options_book_unsubs(self, symbols: Iterable[str]) -> None:
        '''
        Un-Subscribe to the level two order book for options.

        :param symbols: Option symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'OPTIONS_BOOK', 'UNSUBS')

    async def options_book_add(self, symbols: Iterable[str]) -> None:
        '''
        Add to the level two order book for options.

        :param symbols: Option symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'OPTIONS_BOOK', 'ADD', self.BookFields)

    def add_options_book_handler(self, handler: Handler) -> None:
        '''
        Register a function to handle level two options book data as it is
        updated See :ref:`registering_handlers` for details.
        '''
        self._handlers['OPTIONS_BOOK'].append(
            self._BookHandler(handler, self.BookFields))

    ##########################################################################
    # SCREENER_EQUITY/SCREENER_OPTION

    class ScreenerFields(_BaseFieldEnum):
        #: The symbol used to look up either actives, gainers or losers
        SYMBOL = 0

        #: Market snapshot timestamp in milliseconds since Epoch
        TIMESTAMP = 1

        #: Field to sort on
        SORT_FIELD = 2

        #: Frequency of data to sort
        FREQUENCY = 3

        #: Array of fields
        ITEMS = 4

    async def screener_equity_subs(self, symbols: Iterable[str]) -> None:
        '''
        Subscribe to Screener Equity.

        :param symbols: Equity symbols to subscribe to.
        '''
        await self._service_op(symbols, 'SCREENER_EQUITY', 'SUBS', self.ScreenerFields)

    async def screener_equity_unsubs(self, symbols: Iterable[str]) -> None:
        '''
        Un-Subscribe to Screener Equity.

        :param symbols: Equity symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'SCREENER_EQUITY', 'UNSUBS')

    async def screener_equity_add(self, symbols: Iterable[str]) -> None:
        '''
        Add symbols to the Screener Equity list.

        :param symbols: Equity symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'SCREENER_EQUITY', 'ADD', self.ScreenerFields)

    def add_screener_equity_handler(self, handler: Handler) -> None:
        '''
        Register a function to handle Screener Equity data as it is
        updated See :ref:`registering_handlers` for details.
        '''
        self._handlers['SCREENER_EQUITY'].append(
            _Handler(handler, self.ScreenerFields))

    async def screener_option_subs(self, symbols: Iterable[str]) -> None:
        '''
        Subscribe to Screener Option.

        :param symbols: Option symbols to subscribe to.
        '''
        await self._service_op(symbols, 'SCREENER_OPTION', 'SUBS', self.ScreenerFields)

    async def screener_option_unsubs(self, symbols: Iterable[str]) -> None:
        '''
        Un-Subscribe to Screener Option.

        :param symbols: Option symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'SCREENER_OPTION', 'UNSUBS')

    async def screener_option_add(self, symbols: Iterable[str]) -> None:
        '''
        Add symbols to the Screener Option list.

        :param symbols: Option symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'SCREENER_OPTION', 'ADD', self.ScreenerFields)

    def add_screener_option_handler(self, handler: Handler) -> None:
        '''
        Register a function to handle Screener Option data as it is
        updated See :ref:`registering_handlers` for details.
        '''
        self._handlers['SCREENER_OPTION'].append(
            _Handler(handler, self.ScreenerFields))
