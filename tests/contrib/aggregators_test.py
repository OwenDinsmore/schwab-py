import asyncio
import datetime
import json
import unittest

from unittest.mock import AsyncMock, MagicMock, Mock

from schwab.contrib.aggregators import BarAggregator, LevelOneQuotes
from schwab.streaming import StreamClient
from ..utils import no_duplicates


MINUTE = 60 * 1000
# 2024-07-03 13:30 UTC, which is 9:30 US/Eastern
OPEN = int(datetime.datetime(
    2024, 7, 3, 13, 30, tzinfo=datetime.timezone.utc).timestamp() * 1000)


def level_one(timestamp, *items):
    return {'service': 'LEVELONE_EQUITIES', 'timestamp': timestamp,
            'command': 'SUBS', 'content': list(items)}


def chart(*candles):
    return {'service': 'CHART_EQUITY', 'timestamp': 0, 'command': 'SUBS',
            'content': list(candles)}


def candle(symbol, minute, open_, high, low, close, volume):
    return {'key': symbol, 'SEQUENCE': 1, 'OPEN_PRICE': open_,
            'HIGH_PRICE': high, 'LOW_PRICE': low, 'CLOSE_PRICE': close,
            'VOLUME': volume, 'CHART_TIME_MILLIS': OPEN + minute * MINUTE,
            'CHART_DAY': 1}


class LevelOneQuotesTest(unittest.TestCase):

    @no_duplicates
    def test_merges_partial_updates(self):
        quotes = LevelOneQuotes()
        quotes(level_one(1, {'key': 'AAPL', 'BID_PRICE': 1, 'ASK_PRICE': 2}))
        quotes(level_one(2, {'key': 'AAPL', 'BID_PRICE': 1.5}))

        self.assertEqual(
                {'key': 'AAPL', 'BID_PRICE': 1.5, 'ASK_PRICE': 2},
                quotes['AAPL'])
        self.assertEqual(2, quotes.updated_millis('AAPL'))

    @no_duplicates
    def test_multiple_symbols(self):
        quotes = LevelOneQuotes()
        quotes(level_one(1, {'key': 'AAPL', 'BID_PRICE': 1},
                         {'key': 'MSFT', 'BID_PRICE': 2}))

        self.assertEqual(['AAPL', 'MSFT'], sorted(quotes))
        self.assertEqual(2, len(quotes))
        self.assertIn('MSFT', quotes)
        self.assertIsNone(quotes.get('GOOG'))
        with self.assertRaises(KeyError):
            quotes['GOOG']

    @no_duplicates
    def test_returns_copies(self):
        quotes = LevelOneQuotes()
        quotes(level_one(1, {'key': 'AAPL', 'BID_PRICE': 1}))
        quotes['AAPL']['BID_PRICE'] = 100
        self.assertEqual(1, quotes['AAPL']['BID_PRICE'])

    @no_duplicates
    def test_ignores_items_without_symbol(self):
        quotes = LevelOneQuotes()
        quotes(level_one(1, {'BID_PRICE': 1}))
        quotes({'service': 'LEVELONE_EQUITIES'})
        self.assertEqual(0, len(quotes))


class BarAggregatorTest(unittest.TestCase):

    @no_duplicates
    def test_minutes_validation(self):
        for bad in (0, -5, 1.5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                BarAggregator(bad)

    @no_duplicates
    def test_five_minute_bar(self):
        callback = Mock()
        bars = BarAggregator(5, callback=callback)
        for i, (o, h, l, c, v) in enumerate([
                (10, 11, 9, 10.5, 100),
                (10.5, 12, 10, 11, 200),
                (11, 11.5, 8, 9, 300),
                (9, 10, 9, 9.5, 400),
                (9.5, 10, 9.2, 9.8, 500)]):
            bars(chart(candle('AAPL', i, o, h, l, c, v)))

        expected = {
            'symbol': 'AAPL',
            'start_millis': OPEN,
            'end_millis': OPEN + 5 * MINUTE,
            'open': 10, 'high': 12, 'low': 8, 'close': 9.8, 'volume': 1500,
        }
        callback.assert_called_once_with(expected)
        self.assertEqual([expected], bars.bars('AAPL'))
        self.assertEqual(expected, bars.latest('AAPL'))

    @no_duplicates
    def test_bar_not_complete_before_last_minute(self):
        callback = Mock()
        bars = BarAggregator(5, callback=callback)
        for i in range(4):
            bars(chart(candle('AAPL', i, 1, 1, 1, 1, 1)))

        callback.assert_not_called()
        self.assertIsNone(bars.latest('AAPL'))

    @no_duplicates
    def test_missing_last_minute_completed_by_next_window(self):
        bars = BarAggregator(5)
        bars(chart(candle('AAPL', 0, 1, 2, 1, 2, 10)))
        bars(chart(candle('AAPL', 2, 2, 3, 2, 3, 10)))
        bars(chart(candle('AAPL', 6, 3, 3, 3, 3, 10)))

        self.assertEqual(1, len(bars.bars('AAPL')))
        self.assertEqual(3, bars.latest('AAPL')['close'])
        self.assertEqual(20, bars.latest('AAPL')['volume'])

    @no_duplicates
    def test_repeated_minute_replaces_previous(self):
        bars = BarAggregator(2)
        bars(chart(candle('AAPL', 0, 1, 1, 1, 1, 100)))
        bars(chart(candle('AAPL', 0, 1, 2, 1, 2, 150)))
        bars(chart(candle('AAPL', 1, 2, 2, 2, 2, 50)))

        self.assertEqual(200, bars.latest('AAPL')['volume'])
        self.assertEqual(2, bars.latest('AAPL')['high'])

    @no_duplicates
    def test_symbols_are_independent(self):
        bars = BarAggregator(1)
        bars(chart(candle('AAPL', 0, 1, 1, 1, 1, 1),
                   candle('MSFT', 0, 2, 2, 2, 2, 2)))

        self.assertEqual(1, bars.latest('AAPL')['close'])
        self.assertEqual(2, bars.latest('MSFT')['close'])

    @no_duplicates
    def test_out_of_order_candle_ignored(self):
        bars = BarAggregator(5)
        bars(chart(candle('AAPL', 6, 1, 1, 1, 1, 1)))
        with self.assertLogs('schwab.contrib.aggregators', level='WARNING'):
            bars(chart(candle('AAPL', 1, 9, 9, 9, 9, 9)))
        self.assertEqual([], bars.bars('AAPL'))

    @no_duplicates
    def test_max_bars(self):
        bars = BarAggregator(1, max_bars=3)
        for i in range(5):
            bars(chart(candle('AAPL', i, i, i, i, i, 1)))
        self.assertEqual([2, 3, 4], [b['close'] for b in bars.bars('AAPL')])

    @no_duplicates
    def test_callback_error_does_not_stop_aggregation(self):
        bars = BarAggregator(1, callback=Mock(side_effect=RuntimeError()))
        with self.assertLogs('schwab.contrib.aggregators', level='ERROR'):
            bars(chart(candle('AAPL', 0, 1, 1, 1, 1, 1),
                       candle('MSFT', 0, 2, 2, 2, 2, 2)))
        self.assertIsNotNone(bars.latest('MSFT'))

    @no_duplicates
    def test_async_callback(self):
        callback = AsyncMock()
        bars = BarAggregator(1, callback=callback)

        result = bars(chart(candle('AAPL', 0, 1, 1, 1, 1, 1)))
        asyncio.run(result)

        callback.assert_awaited_once()

    @no_duplicates
    def test_malformed_candle_ignored(self):
        bars = BarAggregator(1)
        with self.assertLogs('schwab.contrib.aggregators', level='WARNING'):
            bars(chart({'key': 'AAPL', 'OPEN_PRICE': 1}))
        self.assertEqual([], bars.bars('AAPL'))


class StreamIntegrationTest(unittest.IsolatedAsyncioTestCase):
    '''Feeds raw, numerically keyed stream messages through StreamClient to
    check the aggregators understand relabeled messages.'''

    async def dispatch(self, raw_message, add_handler_name, handler):
        client = StreamClient(MagicMock())
        client._socket = AsyncMock()
        client._socket.recv.side_effect = [json.dumps(raw_message)]
        getattr(client, add_handler_name)(handler)
        await client.handle_message()

    @no_duplicates
    async def test_level_one_equity(self):
        quotes = LevelOneQuotes()
        await self.dispatch({'data': [{
            'service': 'LEVELONE_EQUITIES', 'timestamp': 5, 'command': 'SUBS',
            'content': [{'key': 'AAPL', '1': 190.5, '2': 190.6}]}]},
            'add_level_one_equity_handler', quotes)

        self.assertEqual(190.5, quotes['AAPL']['BID_PRICE'])
        self.assertEqual(190.6, quotes['AAPL']['ASK_PRICE'])

    @no_duplicates
    async def test_chart_equity(self):
        bars = BarAggregator(1)
        await self.dispatch({'data': [{
            'service': 'CHART_EQUITY', 'timestamp': 5, 'command': 'SUBS',
            'content': [{'key': 'AAPL', '1': 1, '2': 10, '3': 11, '4': 9,
                         '5': 10.5, '6': 1000, '7': OPEN, '8': 1}]}]},
            'add_chart_equity_handler', bars)

        self.assertEqual(10.5, bars.latest('AAPL')['close'])

    @no_duplicates
    async def test_chart_futures(self):
        bars = BarAggregator(1)
        await self.dispatch({'data': [{
            'service': 'CHART_FUTURES', 'timestamp': 5, 'command': 'SUBS',
            'content': [{'key': '/ES', '1': OPEN, '2': 10, '3': 11, '4': 9,
                         '5': 10.5, '6': 1000}]}]},
            'add_chart_futures_handler', bars)

        self.assertEqual(10.5, bars.latest('/ES')['close'])
