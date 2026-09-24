import unittest

from schwab.orders.common import *
from schwab.orders.equities import *
from .utils import has_diff, no_duplicates

from unittest.mock import patch


class BuilderTemplates(unittest.TestCase):

    def test_equity_buy_market(self):
        self.assertFalse(has_diff({
            'orderType': 'MARKET',
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [{
                'instruction': 'BUY',
                'quantity': 10,
                'instrument': {
                    'symbol': 'GOOG',
                    'assetType': 'EQUITY',
                }
            }]
        }, equity_buy_market('GOOG', 10).build()))

    def test_equity_buy_limit(self):
        self.assertFalse(has_diff({
            'orderType': 'LIMIT',
            'session': 'NORMAL',
            'duration': 'DAY',
            'price': '199.99',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [{
                'instruction': 'BUY',
                'quantity': 10,
                'instrument': {
                    'symbol': 'GOOG',
                    'assetType': 'EQUITY',
                }
            }]
        }, equity_buy_limit('GOOG', 10, 199.99).build()))

    def test_equity_sell_market(self):
        self.assertFalse(has_diff({
            'orderType': 'MARKET',
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [{
                'instruction': 'SELL',
                'quantity': 10,
                'instrument': {
                    'symbol': 'GOOG',
                    'assetType': 'EQUITY',
                }
            }]
        }, equity_sell_market('GOOG', 10).build()))

    def test_equity_sell_limit(self):
        self.assertFalse(has_diff({
            'orderType': 'LIMIT',
            'session': 'NORMAL',
            'duration': 'DAY',
            'price': '199.99',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [{
                'instruction': 'SELL',
                'quantity': 10,
                'instrument': {
                    'symbol': 'GOOG',
                    'assetType': 'EQUITY',
                }
            }]
        }, equity_sell_limit('GOOG', 10, 199.99).build()))

    def test_equity_sell_short_market(self):
        self.assertFalse(has_diff({
            'orderType': 'MARKET',
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [{
                'instruction': 'SELL_SHORT',
                'quantity': 10,
                'instrument': {
                    'symbol': 'GOOG',
                    'assetType': 'EQUITY',
                }
            }]
        }, equity_sell_short_market('GOOG', 10).build()))

    def test_equity_sell_short_limit(self):
        self.assertFalse(has_diff({
            'orderType': 'LIMIT',
            'session': 'NORMAL',
            'duration': 'DAY',
            'price': '199.99',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [{
                'instruction': 'SELL_SHORT',
                'quantity': 10,
                'instrument': {
                    'symbol': 'GOOG',
                    'assetType': 'EQUITY',
                }
            }]
        }, equity_sell_short_limit('GOOG', 10, 199.99).build()))

    def test_equity_buy_to_cover_market(self):
        self.assertFalse(has_diff({
            'orderType': 'MARKET',
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [{
                'instruction': 'BUY_TO_COVER',
                'quantity': 10,
                'instrument': {
                    'symbol': 'GOOG',
                    'assetType': 'EQUITY',
                }
            }]
        }, equity_buy_to_cover_market('GOOG', 10).build()))

    def test_equity_buy_to_cover_limit(self):
        self.assertFalse(has_diff({
            'orderType': 'LIMIT',
            'session': 'NORMAL',
            'duration': 'DAY',
            'price': '199.99',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [{
                'instruction': 'BUY_TO_COVER',
                'quantity': 10,
                'instrument': {
                    'symbol': 'GOOG',
                    'assetType': 'EQUITY',
                }
            }]
        }, equity_buy_to_cover_limit('GOOG', 10, 199.99).build()))


class StopAndOnCloseTemplates(unittest.TestCase):

    INSTRUCTIONS = [
        ('buy', 'BUY'),
        ('sell', 'SELL'),
        ('sell_short', 'SELL_SHORT'),
        ('buy_to_cover', 'BUY_TO_COVER'),
    ]

    def template(self, prefix, suffix):
        import schwab.orders.equities
        return getattr(schwab.orders.equities,
                       'equity_{}_{}'.format(prefix, suffix))

    def expected(self, instruction, order_type, **fields):
        expected = {
            'orderType': order_type,
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [{
                'instruction': instruction,
                'quantity': 10,
                'instrument': {
                    'symbol': 'GOOG',
                    'assetType': 'EQUITY',
                }
            }]
        }
        expected.update(fields)
        return expected

    def test_stop(self):
        for prefix, instruction in self.INSTRUCTIONS:
            with self.subTest(prefix=prefix):
                self.assertFalse(has_diff(
                    self.expected(instruction, 'STOP', stopPrice='199.99'),
                    self.template(prefix, 'stop')(
                        'GOOG', 10, '199.99').build()))

    def test_stop_limit(self):
        for prefix, instruction in self.INSTRUCTIONS:
            with self.subTest(prefix=prefix):
                self.assertFalse(has_diff(
                    self.expected(instruction, 'STOP_LIMIT',
                                  stopPrice='199.99', price='198.50'),
                    self.template(prefix, 'stop_limit')(
                        'GOOG', 10, '199.99', '198.50').build()))

    def test_trailing_stop_percent(self):
        for prefix, instruction in self.INSTRUCTIONS:
            with self.subTest(prefix=prefix):
                self.assertFalse(has_diff(
                    self.expected(instruction, 'TRAILING_STOP',
                                  stopPriceLinkBasis='LAST',
                                  stopPriceLinkType='PERCENT',
                                  stopPriceOffset=2.5),
                    self.template(prefix, 'trailing_stop')(
                        'GOOG', 10, 2.5,
                        offset_type=StopPriceLinkType.PERCENT).build()))

    def test_trailing_stop_value_and_basis(self):
        for prefix, instruction in self.INSTRUCTIONS:
            with self.subTest(prefix=prefix):
                self.assertFalse(has_diff(
                    self.expected(instruction, 'TRAILING_STOP',
                                  stopPriceLinkBasis='BID',
                                  stopPriceLinkType='VALUE',
                                  stopPriceOffset=1),
                    self.template(prefix, 'trailing_stop')(
                        'GOOG', 10, 1,
                        offset_type=StopPriceLinkType.VALUE,
                        basis=StopPriceLinkBasis.BID).build()))

    def test_trailing_stop_offset_type_required(self):
        for prefix, _ in self.INSTRUCTIONS:
            with self.subTest(prefix=prefix):
                with self.assertRaises(TypeError):
                    self.template(prefix, 'trailing_stop')('GOOG', 10, 2.5)

    def test_trailing_stop_offset_type_must_be_enum(self):
        with self.assertRaisesRegex(ValueError, 'StopPriceLinkType'):
            equity_sell_trailing_stop('GOOG', 10, 2.5, offset_type='PERCENT')

    def test_trailing_stop_basis_must_be_enum(self):
        with self.assertRaisesRegex(ValueError, 'StopPriceLinkBasis'):
            equity_sell_trailing_stop(
                    'GOOG', 10, 2.5, offset_type=StopPriceLinkType.PERCENT,
                    basis='LAST')

    def test_market_on_close(self):
        for prefix, instruction in self.INSTRUCTIONS:
            with self.subTest(prefix=prefix):
                self.assertFalse(has_diff(
                    self.expected(instruction, 'MARKET_ON_CLOSE'),
                    self.template(prefix, 'market_on_close')(
                        'GOOG', 10).build()))

    def test_limit_on_close(self):
        for prefix, instruction in self.INSTRUCTIONS:
            with self.subTest(prefix=prefix):
                self.assertFalse(has_diff(
                    self.expected(instruction, 'LIMIT_ON_CLOSE',
                                  price='199.99'),
                    self.template(prefix, 'limit_on_close')(
                        'GOOG', 10, '199.99').build()))

    def test_duration_can_be_overridden(self):
        order = (equity_sell_stop('GOOG', 10, '199.99')
                 .set_duration(Duration.GOOD_TILL_CANCEL)
                 .build())
        self.assertEqual('GOOD_TILL_CANCEL', order['duration'])
