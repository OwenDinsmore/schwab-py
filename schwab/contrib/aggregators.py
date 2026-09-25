'''
Stream handlers that aggregate streaming data into state you can read at any
time, so you don't have to write your own message handling for common tasks.
Register them like any other handler:

.. code-block:: python

  quotes = LevelOneQuotes()
  stream_client.add_level_one_equity_handler(quotes)
  ...
  print(quotes['AAPL']['BID_PRICE'])
'''

from __future__ import annotations

import asyncio
import collections
import inspect
import logging

from collections.abc import Awaitable, Callable, Iterator
from typing import Any


def get_logger() -> logging.Logger:
    return logging.getLogger(__name__)


def _symbol(item: dict[str, Any]) -> str | None:
    symbol = item.get('key', item.get('SYMBOL'))
    return None if symbol is None else str(symbol)


class LevelOneQuotes:
    '''
    Keeps the latest quote for every symbol on a level one stream.

    Level one streams only send the fields that changed since the previous
    message for a symbol. This handler merges those updates, so each quote
    holds the most recent value of every field received so far. Works with any
    level one stream: equities, options, futures, futures options, and forex.

    .. code-block:: python

      quotes = LevelOneQuotes()
      stream_client.add_level_one_equity_handler(quotes)
      await stream_client.level_one_equity_subs(['AAPL', 'MSFT'])

      while True:
          await stream_client.handle_message()
          if 'AAPL' in quotes:
              print(quotes['AAPL']['LAST_PRICE'])
    '''

    def __init__(self) -> None:
        self._quotes: dict[str, dict[str, Any]] = {}
        self._updated: dict[str, int] = {}

    def __call__(self, msg: dict[str, Any]) -> None:
        for item in msg.get('content', []):
            symbol = _symbol(item)
            if symbol is None:
                continue
            self._quotes.setdefault(symbol, {}).update(item)
            if 'timestamp' in msg:
                self._updated[symbol] = msg['timestamp']

    def __getitem__(self, symbol: str) -> dict[str, Any]:
        '''Returns a copy of the latest quote for ``symbol``, with field
        names as keys. Raises ``KeyError`` if no quote has been received.'''
        return dict(self._quotes[symbol])

    def __contains__(self, symbol: object) -> bool:
        return symbol in self._quotes

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._quotes))

    def __len__(self) -> int:
        return len(self._quotes)

    def get(self, symbol: str) -> dict[str, Any] | None:
        '''Returns a copy of the latest quote for ``symbol``, or ``None`` if no
        quote has been received.'''
        quote = self._quotes.get(symbol)
        return None if quote is None else dict(quote)

    def updated_millis(self, symbol: str) -> int | None:
        '''Returns the stream timestamp, in milliseconds since the epoch, of
        the last update for ``symbol``, or ``None``.'''
        return self._updated.get(symbol)


class BarAggregator:
    '''
    Combines the one-minute candles from the equity or futures chart streams
    into bars of ``minutes`` minutes.

    Bars cover consecutive ``minutes``-long windows counted from midnight UTC,
    so for values that divide an hour, such as 5, 15 or 30, bars start on the
    clock boundaries you'd expect: 9:30, 9:35, and so on. A bar is complete
    when its last minute arrives, or when a candle for a later window arrives.

    Completed bars are passed to ``callback``, if given, and kept in memory.
    Each bar is a ``dict`` with the keys ``symbol``, ``start_millis`` and
    ``end_millis`` (the bar covers ``start_millis`` up to, but not including,
    ``end_millis``), ``open``, ``high``, ``low``, ``close`` and ``volume``.

    .. code-block:: python

      def on_bar(bar):
          print(bar['symbol'], bar['close'])

      bars = BarAggregator(5, callback=on_bar)
      stream_client.add_chart_equity_handler(bars)
      await stream_client.chart_equity_subs(['AAPL'])

    :param minutes: Length of each bar, in minutes.
    :param callback: Called with each completed bar. May be a plain function or
                     a coroutine function. Exceptions it raises are logged, and
                     aggregation continues.
    :param max_bars: Number of completed bars to keep per symbol. ``None``
                     keeps all of them.
    '''

    def __init__(self, minutes: int,
                 callback: Callable[[dict[str, Any]], Any] | None = None,
                 max_bars: int | None = 1000) -> None:
        if not isinstance(minutes, int) or minutes < 1:
            raise ValueError('minutes must be a positive int')

        self.minutes = minutes
        self._width = minutes * 60 * 1000
        self._callback = callback
        self._max_bars = max_bars

        # Completed bars, per symbol
        self._bars: dict[str, collections.deque[dict[str, Any]]] = {}

        # One-minute candles of the bar in progress, per symbol, keyed by
        # their start time so a repeated or corrected minute replaces the old
        # one instead of being counted twice
        self._pending: dict[str, dict[int, dict[str, Any]]] = {}
        self._pending_start: dict[str, int] = {}

    def __call__(self, msg: dict[str, Any]) -> Awaitable[None] | None:
        awaitables = []
        for item in msg.get('content', []):
            for result in self._add_candle(item):
                if inspect.isawaitable(result):
                    awaitables.append(result)

        if not awaitables:
            return None

        async def wait_for_callbacks() -> None:
            await asyncio.gather(*awaitables)
        return wait_for_callbacks()

    def _add_candle(self, item: dict[str, Any]) -> list[Any]:
        symbol = _symbol(item)
        try:
            minute = int(item['CHART_TIME_MILLIS'])
            candle = {
                'open': item['OPEN_PRICE'],
                'high': item['HIGH_PRICE'],
                'low': item['LOW_PRICE'],
                'close': item['CLOSE_PRICE'],
                'volume': item['VOLUME'],
            }
        except KeyError as e:
            get_logger().warning('Ignoring candle without %s: %s', e, item)
            return []
        if symbol is None:
            return []

        start = minute - minute % self._width
        results = []

        pending_start = self._pending_start.get(symbol)
        if pending_start is not None and start != pending_start:
            if start < pending_start:
                get_logger().warning(
                        'Ignoring out-of-order candle for %s at %s',
                        symbol, minute)
                return []
            results.append(self._complete(symbol))

        if self._pending_start.get(symbol) is None:
            self._pending_start[symbol] = start
            self._pending[symbol] = {}
        self._pending[symbol][minute] = candle

        if minute + 60 * 1000 >= start + self._width:
            results.append(self._complete(symbol))

        return results

    def _complete(self, symbol: str) -> Any:
        start = self._pending_start.pop(symbol)
        candles = [c for _, c in sorted(self._pending.pop(symbol).items())]

        bar = {
            'symbol': symbol,
            'start_millis': start,
            'end_millis': start + self._width,
            'open': candles[0]['open'],
            'high': max(c['high'] for c in candles),
            'low': min(c['low'] for c in candles),
            'close': candles[-1]['close'],
            'volume': sum(c['volume'] for c in candles),
        }

        self._bars.setdefault(
                symbol, collections.deque(maxlen=self._max_bars)).append(bar)

        if self._callback is not None:
            # Keep aggregating the rest of the message even if the callback
            # fails
            try:
                return self._callback(bar)
            except Exception:
                get_logger().exception('Bar callback raised')
        return None

    def bars(self, symbol: str) -> list[dict[str, Any]]:
        '''Returns the completed bars for ``symbol``, oldest first.'''
        return list(self._bars.get(symbol, ()))

    def latest(self, symbol: str) -> dict[str, Any] | None:
        '''Returns the most recently completed bar for ``symbol``, or
        ``None``.'''
        bars = self._bars.get(symbol)
        return bars[-1] if bars else None
