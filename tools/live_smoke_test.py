'''
Smoke test for schwab-py against a real Schwab account.

Unit tests run against mocks. This script checks that the library works
against the real API: authentication, account and market data endpoints,
order previews, the async client, and streaming, including reconnecting.

By default it is READ-ONLY: it never places, replaces or cancels an order.
Order checks use Schwab's previewOrder endpoint, which validates an order
without placing it. Pass --place-test-order to also place a one-share limit
buy far below the market price and then cancel it; you'll be asked to confirm
first.

Configuration comes from environment variables:

    SCHWAB_API_KEY         App key from the Schwab developer portal
    SCHWAB_APP_SECRET      App secret
    SCHWAB_CALLBACK_URL    Callback URL, exactly as registered for the app,
                           e.g. https://127.0.0.1:8182
    SCHWAB_TOKEN_PATH      Token file. Defaults to ~/.schwab-py/token.json
    SCHWAB_ACCOUNT_NUMBER  Account to test. Defaults to the first account

Run it from the repository root:

    python tools/live_smoke_test.py
    python tools/live_smoke_test.py --skip-streaming
    python tools/live_smoke_test.py --place-test-order

The first run opens a browser to log in and creates the token file.
'''

import argparse
import asyncio
import datetime
import decimal
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import schwab  # noqa: E402
from schwab.auth import easy_client  # noqa: E402
from schwab.client import Client  # noqa: E402
from schwab.contrib.aggregators import LevelOneQuotes  # noqa: E402
from schwab.orders.common import (  # noqa: E402
        Duration, EquityInstruction, OrderStrategyType, OrderType,
        PriceLinkBasis, PriceLinkType, Session, StopPriceLinkBasis,
        StopPriceLinkType)
from schwab.orders.equities import (  # noqa: E402
        equity_buy_limit, equity_sell_stop, equity_sell_stop_limit,
        equity_sell_trailing_stop)
from schwab.orders.generic import OrderBuilder  # noqa: E402
from schwab.streaming import StreamClient  # noqa: E402
from schwab.utils import Utils, trim_candles  # noqa: E402


SYMBOL = 'AAPL'


##########################################################################
# Reporting

class Results:
    def __init__(self):
        self.passed = []
        self.failed = []
        self.notes = []

    def check(self, name, func, *args, **kwargs):
        '''Runs a check, recording a pass or failure. Returns its result, or
        None if it failed.'''
        print('  {:<58}'.format(name), end='', flush=True)
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            print('FAIL')
            print('      {}: {}'.format(type(e).__name__, e))
            if os.environ.get('SMOKE_TEST_TRACEBACKS'):
                traceback.print_exc()
            self.failed.append(name)
            return None
        print('ok')
        self.passed.append(name)
        return result

    async def check_async(self, name, coro_func, *args, **kwargs):
        print('  {:<58}'.format(name), end='', flush=True)
        try:
            result = await coro_func(*args, **kwargs)
        except Exception as e:
            print('FAIL')
            print('      {}: {}'.format(type(e).__name__, e))
            if os.environ.get('SMOKE_TEST_TRACEBACKS'):
                traceback.print_exc()
            self.failed.append(name)
            return None
        print('ok')
        self.passed.append(name)
        return result

    def note(self, message):
        print('      note: ' + message)
        self.notes.append(message)


def section(title):
    print()
    print(title)
    print('-' * len(title))


def mask(account_number):
    account_number = str(account_number)
    return '*' * max(0, len(account_number) - 4) + account_number[-4:]


def ok_json(resp, expected=(200,)):
    if resp.status_code not in expected:
        raise AssertionError('HTTP {}: {}'.format(
            resp.status_code, resp.text[:300]))
    return resp.json() if resp.content else None


##########################################################################
# Configuration

def config():
    missing = [name for name in (
        'SCHWAB_API_KEY', 'SCHWAB_APP_SECRET', 'SCHWAB_CALLBACK_URL')
        if not os.environ.get(name)]
    if missing:
        sys.exit('Set these environment variables first: ' +
                 ', '.join(missing))

    token_path = os.path.expanduser(os.environ.get(
        'SCHWAB_TOKEN_PATH', '~/.schwab-py/token.json'))
    os.makedirs(os.path.dirname(token_path), mode=0o700, exist_ok=True)

    return {
        'api_key': os.environ['SCHWAB_API_KEY'],
        'app_secret': os.environ['SCHWAB_APP_SECRET'],
        'callback_url': os.environ['SCHWAB_CALLBACK_URL'],
        'token_path': token_path,
        'account_number': os.environ.get('SCHWAB_ACCOUNT_NUMBER'),
    }


##########################################################################
# Checks

def check_auth(results, cfg):
    section('Authentication')
    client = results.check(
        'easy_client creates or loads the token', easy_client,
        cfg['api_key'], cfg['app_secret'], cfg['callback_url'],
        cfg['token_path'])
    if client is None:
        return None

    remaining = client.refresh_token_expires_in()
    if remaining is not None:
        results.note('refresh token expires in {:.1f} days'.format(
            remaining / 86400))
    return client


def check_accounts(results, client, cfg):
    section('Accounts')
    numbers = results.check(
        'get_account_numbers', lambda: ok_json(client.get_account_numbers()))
    if not numbers:
        return None

    account_number = cfg['account_number'] or numbers[0]['accountNumber']
    results.note('{} linked account(s); testing {}'.format(
        len(numbers), mask(account_number)))

    results.check(
        'get_account with a plain account number (#6)',
        lambda: ok_json(client.get_account(
            account_number, fields=Client.Account.Fields.POSITIONS)))
    results.check(
        'get_account_hash', lambda: client.get_account_hash(account_number))
    results.check('get_accounts', lambda: ok_json(client.get_accounts()))
    results.check('get_user_preferences',
                  lambda: ok_json(client.get_user_preferences()))

    since = datetime.datetime.now(datetime.timezone.utc) - \
        datetime.timedelta(days=7)
    results.check(
        'get_orders_for_account (7 days, timezone-aware)',
        lambda: ok_json(client.get_orders_for_account(
            account_number, from_entered_datetime=since)))
    results.check(
        'get_transactions (7 days)',
        lambda: ok_json(client.get_transactions(
            account_number, start_date=since)))
    return account_number


def check_market_data(results, client):
    section('Market data')
    quotes = results.check(
        'get_quotes', lambda: ok_json(client.get_quotes([SYMBOL, 'MSFT'])))
    results.check('get_quote', lambda: ok_json(client.get_quote(SYMBOL)))

    today = datetime.date.today()
    results.check(
        'get_option_chain (narrowed)',
        lambda: ok_json(client.get_option_chain(
            SYMBOL, contract_type=Client.Options.ContractType.CALL,
            strike_count=4, from_date=today,
            to_date=today + datetime.timedelta(days=30))))
    results.check('get_option_expiration_chain',
                  lambda: ok_json(client.get_option_expiration_chain(SYMBOL)))

    def price_history():
        end = datetime.datetime.now(datetime.timezone.utc)
        start = end - datetime.timedelta(days=5)
        history = ok_json(client.get_price_history_every_thirty_minutes(
            SYMBOL, start_datetime=start, end_datetime=end))
        trimmed = trim_candles(history, start, end)
        return len(history.get('candles', [])), len(trimmed)
    counts = results.check('price history and trim_candles', price_history)
    if counts:
        results.note('{} candles, {} within range'.format(*counts))

    results.check(
        'get_market_hours',
        lambda: ok_json(client.get_market_hours(
            Client.MarketHours.Market.EQUITY)))
    results.check(
        'get_movers',
        lambda: ok_json(client.get_movers(Client.Movers.Index.SPX)))
    results.check(
        'get_instruments',
        lambda: ok_json(client.get_instruments(
            SYMBOL, Client.Instrument.Projection.FUNDAMENTAL)))

    if quotes and SYMBOL in quotes:
        return quotes[SYMBOL].get('quote', {}).get('lastPrice')
    return None


def check_order_previews(results, client, account_number, last_price):
    '''Validates order shapes with previewOrder, which never places orders.'''
    section('Order previews (nothing is placed)')
    if not last_price:
        results.note('no last price for {}; skipping'.format(SYMBOL))
        return

    low = str(decimal.Decimal(str(last_price * 0.5)).quantize(
        decimal.Decimal('0.01')))

    previews = [
        ('limit buy', equity_buy_limit(SYMBOL, 1, low)),
        ('stop sell', equity_sell_stop(SYMBOL, 1, low)),
        ('stop limit sell', equity_sell_stop_limit(SYMBOL, 1, low, low)),
        ('trailing stop sell (percent)', equity_sell_trailing_stop(
            SYMBOL, 1, 5, offset_type=StopPriceLinkType.PERCENT)),
        ('GTC limit buy', equity_buy_limit(SYMBOL, 1, low)
            .set_duration(Duration.GOOD_TILL_CANCEL)),
    ]
    for name, order in previews:
        results.check('preview: ' + name, lambda o=order: ok_json(
            client.preview_order(account_number, o), expected=(200, 201)))

    # Trailing stop limit: two candidate shapes have been reported. Report
    # which ones Schwab accepts so the template can be written.
    section('Trailing stop limit shapes (informational)')

    def trailing_base():
        return (OrderBuilder()
                .set_order_type(OrderType.TRAILING_STOP_LIMIT)
                .set_session(Session.NORMAL)
                .set_duration(Duration.DAY)
                .set_order_strategy_type(OrderStrategyType.SINGLE)
                .add_equity_leg(EquityInstruction.SELL, SYMBOL, 1)
                .set_stop_price_link_basis(StopPriceLinkBasis.LAST)
                .set_stop_price_link_type(StopPriceLinkType.PERCENT)
                .set_stop_price_offset(5))

    shapes = {
        'trailing stop + fixed limit price':
            trailing_base().set_price(low),
        'trailing stop + limit offset (priceOffset)':
            trailing_base()
            .set_price_link_basis(PriceLinkBasis.LAST)
            .set_price_link_type(PriceLinkType.VALUE)
            .set_price_offset(0.5),
    }
    for name, order in shapes.items():
        try:
            resp = client.preview_order(account_number, order)
        except Exception as e:
            print('  {:<58}error: {}'.format(name, e))
            continue
        accepted = resp.status_code in (200, 201)
        print('  {:<58}{}'.format(
            name, 'accepted' if accepted else
            'rejected (HTTP {})'.format(resp.status_code)))
        results.notes.append('trailing stop limit, {}: {}'.format(
            name, 'accepted' if accepted else resp.text[:200]))


def check_async_client(results, cfg):
    section('Async client')

    async def run():
        client = easy_client(cfg['api_key'], cfg['app_secret'],
                             cfg['callback_url'], cfg['token_path'],
                             asyncio=True)
        try:
            ok_json(await client.get_account_numbers())
            ok_json(await client.get_quotes([SYMBOL]))
        finally:
            await client.close_async_session()
    results.check('async client requests', lambda: asyncio.run(run()))


def check_streaming(results, client, seconds):
    section('Streaming ({} seconds per phase)'.format(seconds))
    results.note('outside market hours, expect only the initial snapshot')

    async def run():
        stream_client = StreamClient(client, auto_reconnect=True)
        quotes = LevelOneQuotes()
        stream_client.add_level_one_equity_handler(quotes)

        async def receive_for(duration):
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                try:
                    await asyncio.wait_for(
                        stream_client.handle_message(),
                        max(0.1, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    pass

        async with stream_client:
            await results.check_async('login', stream_client.login)
            await results.check_async(
                'level_one_equity_subs', stream_client.level_one_equity_subs,
                [SYMBOL, 'MSFT'])

            await receive_for(seconds)
            if SYMBOL not in quotes:
                raise AssertionError('no level one data received')
            print('  {:<58}ok'.format('received level one data'))
            results.note('{} bid/ask: {} / {}'.format(
                SYMBOL, quotes[SYMBOL].get('BID_PRICE'),
                quotes[SYMBOL].get('ASK_PRICE')))

            await results.check_async(
                'level_one_equity_view (VIEW command)',
                stream_client.level_one_equity_view, [SYMBOL, 'MSFT'],
                [StreamClient.LevelOneEquityFields.BID_PRICE,
                 StreamClient.LevelOneEquityFields.ASK_PRICE,
                 StreamClient.LevelOneEquityFields.LAST_PRICE])

            await results.check_async('reconnect', stream_client.reconnect)
            before = quotes.updated_millis(SYMBOL)
            await receive_for(seconds)
            after = quotes.updated_millis(SYMBOL)
            if after is None or after == before:
                results.note('no new data after reconnecting; expected '
                             'outside market hours')
            else:
                print('  {:<58}ok'.format('data received after reconnect'))

            await results.check_async('logout', stream_client.logout)

    try:
        asyncio.run(run())
    except Exception as e:
        print('  {:<58}FAIL'.format('streaming session'))
        print('      {}: {}'.format(type(e).__name__, e))
        results.failed.append('streaming session')


def place_and_cancel_test_order(results, client, account_number, last_price):
    section('Placing and cancelling a test order')
    if not last_price:
        results.note('no last price; skipping')
        return

    price = str(decimal.Decimal(str(last_price * 0.5)).quantize(
        decimal.Decimal('0.01')))
    print()
    print('  This places a REAL order on account {}:'.format(
        mask(account_number)))
    print('    BUY 1 {} LIMIT ${} (about half the last price), DAY'.format(
        SYMBOL, price))
    print('  It is cancelled immediately afterwards. At half the market')
    print('  price it should not fill, but that is not guaranteed.')
    print()
    if input('  Type PLACE to continue, anything else to skip: ') != 'PLACE':
        results.note('test order skipped')
        return

    def place():
        resp = client.place_order(
            account_number, equity_buy_limit(SYMBOL, 1, price))
        ok_json(resp, (200, 201))
        order_id = Utils(client, account_number).extract_order_id(resp)
        if order_id is None:
            raise AssertionError('order placed but no order ID in the '
                                 'response; CHECK YOUR ACCOUNT')
        return order_id
    order_id = results.check('place_order and extract_order_id', place)
    if order_id is None:
        return

    try:
        results.check('get_order', lambda: ok_json(
            client.get_order(order_id, account_number)))
    finally:
        results.check('cancel_order', lambda: ok_json(
            client.cancel_order(order_id, account_number), (200, 201)))

    def cancelled():
        status = {}
        for _ in range(10):
            status = ok_json(client.get_order(order_id, account_number))
            if status.get('status') in ('CANCELED', 'PENDING_CANCEL'):
                return
            time.sleep(1)
        raise AssertionError('order status is {}; CHECK YOUR ACCOUNT'.format(
            status.get('status')))
    results.check('order is cancelled', cancelled)


##########################################################################

def main():
    parser = argparse.ArgumentParser(
        description='Smoke test schwab-py against a real Schwab account. '
                    'Read-only unless --place-test-order is given.')
    parser.add_argument('--skip-streaming', action='store_true')
    parser.add_argument('--stream-seconds', type=int, default=15)
    parser.add_argument('--place-test-order', action='store_true',
                        help='place and cancel a one-share limit order far '
                             'below the market (asks for confirmation)')
    args = parser.parse_args()

    cfg = config()
    results = Results()
    print('schwab-py {} smoke test'.format(schwab.__version__))

    client = check_auth(results, cfg)
    if client is None:
        sys.exit(1)

    account_number = check_accounts(results, client, cfg)
    last_price = check_market_data(results, client)
    if account_number:
        check_order_previews(results, client, account_number, last_price)
    check_async_client(results, cfg)
    if not args.skip_streaming:
        check_streaming(results, client, args.stream_seconds)
    if args.place_test_order and account_number:
        place_and_cancel_test_order(
            results, client, account_number, last_price)

    section('Summary')
    print('  {} passed, {} failed'.format(
        len(results.passed), len(results.failed)))
    for name in results.failed:
        print('  FAILED: ' + name)
    if results.notes:
        print()
        for note in results.notes:
            print('  - ' + note)
    sys.exit(1 if results.failed else 0)


if __name__ == '__main__':
    main()
