'''
Not a test module. Type-checked by mypy in CI to verify that the public API's
annotations and stubs give users the types they expect. Never executed.
'''

import datetime

from typing_extensions import assert_type

import httpx

from schwab.auth import easy_client
from schwab.client import AsyncClient, Client
from schwab.orders.common import Duration, StopPriceLinkType
from schwab.orders.equities import equity_buy_limit, equity_sell_trailing_stop
from schwab.orders.generic import OrderBuilder
from schwab.streaming import StreamClient


def sync_client() -> None:
    client = easy_client('key', 'secret', 'https://127.0.0.1:8182', 'token')
    assert_type(client, Client)

    resp = client.get_account('12345678', fields=Client.Account.Fields.POSITIONS)
    assert_type(resp, httpx.Response)

    order = equity_buy_limit('AAPL', 1, '150.00').set_duration(Duration.DAY)
    assert_type(order, OrderBuilder)
    assert_type(client.place_order('12345678', order), httpx.Response)

    assert_type(client.get_account_hash('12345678'), str)
    assert_type(client.refresh_token_expires_in(), float | None)
    client.get_price_history_every_minute(
            'AAPL', start_datetime=datetime.datetime(2024, 1, 2))


async def async_client() -> None:
    client = easy_client('key', 'secret', 'https://127.0.0.1:8182', 'token',
                         asyncio=True)
    assert_type(client, AsyncClient)

    resp = await client.get_quotes(['AAPL', 'MSFT'])
    assert_type(resp, httpx.Response)
    assert_type(await client.get_account_hash(12345678), str)

    order = equity_sell_trailing_stop(
            'AAPL', 10, 2.5, offset_type=StopPriceLinkType.PERCENT)
    assert_type(await client.place_order('12345678', order), httpx.Response)

    stream_client = StreamClient(client, auto_reconnect=True)
    await stream_client.login()
    stream_client.add_level_one_equity_handler(lambda msg: print(msg))
    await stream_client.level_one_equity_subs(
            ['AAPL'], fields=[StreamClient.LevelOneEquityFields.BID_PRICE])
    await stream_client.handle_message()
