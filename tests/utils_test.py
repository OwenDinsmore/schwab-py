import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from schwab.utils import trim_candles, AccountHashMismatchException, Utils
from schwab.utils import UnsuccessfulOrderException
from schwab.utils import EnumEnforcer
from .utils import no_duplicates, MockResponse

import enum
import unittest


class EnumEnforcerTest(unittest.TestCase):

    class TestClass(EnumEnforcer):
        def test_enforcement(self, value):
            self.convert_enum(value, EnumEnforcerTest.TestEnum)


    class TestEnum(enum.Enum):
        VALUE_1 = 1
        VALUE_2 = 2


    def test_valid_enum(self):
        t = self.TestClass(enforce_enums=True)
        t.test_enforcement(self.TestEnum.VALUE_1)

    def test_invalid_enum_passed_as_string(self):
        t = self.TestClass(enforce_enums=True)
        with self.assertRaisesRegex(
                ValueError, 'tests.utils_test.TestEnum.VALUE_1'):
            t.test_enforcement('VALUE_1')

    def test_invalid_enum_passed_as_not_string(self):
        t = self.TestClass(enforce_enums=True)
        with self.assertRaises(ValueError):
            t.test_enforcement(123)


class UtilsTest(unittest.TestCase):

    def setUp(self):
        self.mock_client = MagicMock()
        self.account_hash = '0xacc0unth45h'
        self.utils = Utils(self.mock_client, self.account_hash)

        self.order_id = 1

        self.maxDiff = None

    ##########################################################################
    # extract_order_id tests

    @no_duplicates
    def test_extract_order_id_order_not_ok(self):
        response = MockResponse({}, 403)
        with self.assertRaises(
                UnsuccessfulOrderException, msg='order not successful'):
            self.utils.extract_order_id(response)

    @no_duplicates
    def test_extract_order_id_no_location(self):
        response = MockResponse({}, 200, headers={})
        self.assertIsNone(self.utils.extract_order_id(response))

    @no_duplicates
    def test_extract_order_id_no_pattern_match(self):
        response = MockResponse({}, 200, headers={
            'Location': 'not-a-match'})
        self.assertIsNone(self.utils.extract_order_id(response))

    @no_duplicates
    def test_get_order_nonmatching_account_hash(self):
        response = MockResponse({}, 200, headers={
            'Location':
            'https://api.schwabapi.com/trader/v1/accounts/badhash/orders/123'})
        with self.assertRaisesRegex(
                AccountHashMismatchException,
                'order request account hash != Utils.account_hash') as cm:
            self.utils.extract_order_id(response)

    @no_duplicates
    def test_get_order_success_200(self):
        order_id = 123456
        response = MockResponse({}, 200, headers={
            'Location':
            'https://api.schwabapi.com/trader/v1/accounts/{}/orders/{}'.format(
                self.account_hash, order_id)})
        self.assertEqual(order_id, self.utils.extract_order_id(response))

    @no_duplicates
    def test_extract_order_id_custom_base_url(self):
        response = MockResponse({}, 201, headers={
            'Location':
            'http://localhost:8080/proxy/trader/v1/accounts/{}/orders/{}'.format(
                self.account_hash, 123456)})
        self.assertEqual(123456, self.utils.extract_order_id(response))

    @no_duplicates
    def test_extract_order_id_with_account_number(self):
        self.mock_client._account_hashes = {'12345678': 'RESOLVEDHASH'}
        utils = Utils(self.mock_client, '12345678')
        response = MockResponse({}, 201, headers={
            'Location': 'https://api.schwabapi.com/trader/v1/accounts/' +
                        'RESOLVEDHASH/orders/123456'})
        self.assertEqual(123456, utils.extract_order_id(response))

    @no_duplicates
    def test_extract_order_id_non_httpx_response(self):
        # requests.Response has no is_error attribute. See upstream issue #214.
        response = SimpleNamespace(status_code=201, headers={
            'Location':
            'https://api.schwabapi.com/trader/v1/accounts/{}/orders/{}'.format(
                self.account_hash, 123456)})
        self.assertEqual(123456, self.utils.extract_order_id(response))

    @no_duplicates
    def test_extract_order_id_non_httpx_response_not_ok(self):
        response = SimpleNamespace(status_code=400, headers={})
        with self.assertRaises(UnsuccessfulOrderException):
            self.utils.extract_order_id(response)

    @no_duplicates
    def test_get_order_success_201(self):
        order_id = 123456
        response = MockResponse({}, 201, headers={
            'Location':
            'https://api.schwabapi.com/trader/v1/accounts/{}/orders/{}'.format(
                self.account_hash, order_id)})
        self.assertEqual(order_id, self.utils.extract_order_id(response))


class TrimCandlesTest(unittest.TestCase):

    def candle(self, dt):
        return {'open': 1, 'close': 1, 'datetime': int(dt.timestamp() * 1000)}

    def setUp(self):
        eastern = datetime.timezone(datetime.timedelta(hours=-4))
        self.times = [datetime.datetime(2024, 7, 3, hour, minute, tzinfo=eastern)
                      for hour, minute in ((9, 30), (10, 0), (10, 30), (11, 0),
                                           (11, 30))]
        self.history = {'symbol': 'SPY',
                        'candles': [self.candle(t) for t in self.times]}

    @no_duplicates
    def test_trim_inclusive_bounds(self):
        trimmed = trim_candles(self.history, self.times[1], self.times[3])
        self.assertEqual([self.candle(t) for t in self.times[1:4]], trimmed)

    @no_duplicates
    def test_open_ended(self):
        self.assertEqual(4, len(trim_candles(self.history, self.times[1])))
        self.assertEqual(
                2, len(trim_candles(self.history, end_datetime=self.times[1])))

    @no_duplicates
    def test_other_timezone(self):
        utc_start = self.times[2].astimezone(datetime.timezone.utc)
        self.assertEqual(3, len(trim_candles(self.history, utc_start)))

    @no_duplicates
    def test_no_candles(self):
        self.assertEqual([], trim_candles({'empty': True}, self.times[0]))
