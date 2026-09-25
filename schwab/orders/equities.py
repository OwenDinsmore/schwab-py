from __future__ import annotations

import decimal

from typing import TYPE_CHECKING

from schwab.orders.common import Duration, Session, StopPriceLinkBasis

if TYPE_CHECKING:
    from schwab.orders.common import EquityInstruction, OrderType
    from schwab.orders.common import StopPriceLinkType
    from schwab.orders.generic import OrderBuilder


##########################################################################
# Buy orders


def equity_buy_market(symbol: str, quantity: int | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy market order.
    '''
    from schwab.orders.common import Duration, EquityInstruction
    from schwab.orders.common import OrderStrategyType, OrderType, Session
    from schwab.orders.generic import OrderBuilder

    return (OrderBuilder()
            .set_order_type(OrderType.MARKET)
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_equity_leg(EquityInstruction.BUY, symbol, quantity))


def equity_buy_limit(
        symbol: str, quantity: int | float,
        price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy limit order.
    '''
    from schwab.orders.common import Duration, EquityInstruction
    from schwab.orders.common import OrderStrategyType, OrderType, Session
    from schwab.orders.generic import OrderBuilder

    return (OrderBuilder()
            .set_order_type(OrderType.LIMIT)
            .set_price(price)
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_equity_leg(EquityInstruction.BUY, symbol, quantity))

##########################################################################
# Sell orders


def equity_sell_market(symbol: str, quantity: int | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    sell market order.
    '''
    from schwab.orders.common import Duration, EquityInstruction
    from schwab.orders.common import OrderStrategyType, OrderType, Session
    from schwab.orders.generic import OrderBuilder

    return (OrderBuilder()
            .set_order_type(OrderType.MARKET)
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_equity_leg(EquityInstruction.SELL, symbol, quantity))


def equity_sell_limit(
        symbol: str, quantity: int | float,
        price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    sell limit order.
    '''
    from schwab.orders.common import Duration, EquityInstruction
    from schwab.orders.common import OrderStrategyType, OrderType, Session
    from schwab.orders.generic import OrderBuilder

    return (OrderBuilder()
            .set_order_type(OrderType.LIMIT)
            .set_price(price)
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_equity_leg(EquityInstruction.SELL, symbol, quantity))

##########################################################################
# Short sell orders


def equity_sell_short_market(
        symbol: str, quantity: int | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    short sell market order.
    '''
    from schwab.orders.common import Duration, EquityInstruction
    from schwab.orders.common import OrderStrategyType, OrderType, Session
    from schwab.orders.generic import OrderBuilder

    return (OrderBuilder()
            .set_order_type(OrderType.MARKET)
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_equity_leg(EquityInstruction.SELL_SHORT, symbol, quantity))


def equity_sell_short_limit(
        symbol: str, quantity: int | float,
        price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    short sell limit order.
    '''
    from schwab.orders.common import Duration, EquityInstruction
    from schwab.orders.common import OrderStrategyType, OrderType, Session
    from schwab.orders.generic import OrderBuilder

    return (OrderBuilder()
            .set_order_type(OrderType.LIMIT)
            .set_price(price)
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_equity_leg(EquityInstruction.SELL_SHORT, symbol, quantity))

##########################################################################
# Buy to cover orders


def equity_buy_to_cover_market(
        symbol: str, quantity: int | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy-to-cover market order.
    '''
    from schwab.orders.common import Duration, EquityInstruction
    from schwab.orders.common import OrderStrategyType, OrderType, Session
    from schwab.orders.generic import OrderBuilder

    return (OrderBuilder()
            .set_order_type(OrderType.MARKET)
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_equity_leg(EquityInstruction.BUY_TO_COVER, symbol, quantity))


def equity_buy_to_cover_limit(
        symbol: str, quantity: int | float,
        price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy-to-cover limit order.
    '''
    from schwab.orders.common import Duration, EquityInstruction
    from schwab.orders.common import OrderStrategyType, OrderType, Session
    from schwab.orders.generic import OrderBuilder

    return (OrderBuilder()
            .set_order_type(OrderType.LIMIT)
            .set_price(price)
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_equity_leg(EquityInstruction.BUY_TO_COVER, symbol, quantity))


##########################################################################
# Stop, trailing stop, and on-close orders
#
# Each public template below is a thin wrapper around one of these builders.
# They're written out explicitly, rather than generated, so that they show up
# in documentation and editor autocompletion.


def __base(
        instruction: EquityInstruction, symbol: str, quantity: int | float,
        order_type: OrderType) -> OrderBuilder:
    from schwab.orders.common import OrderStrategyType
    from schwab.orders.generic import OrderBuilder

    return (OrderBuilder()
            .set_order_type(order_type)
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_equity_leg(instruction, symbol, quantity))


def __stop(
        instruction: EquityInstruction, symbol: str, quantity: int | float,
        stop_price: str | decimal.Decimal | float) -> OrderBuilder:
    from schwab.orders.common import OrderType

    return (__base(instruction, symbol, quantity, OrderType.STOP)
            .set_stop_price(stop_price))


def __stop_limit(
        instruction: EquityInstruction, symbol: str, quantity: int | float,
        stop_price: str | decimal.Decimal | float,
        limit_price: str | decimal.Decimal | float) -> OrderBuilder:
    from schwab.orders.common import OrderType

    return (__base(instruction, symbol, quantity, OrderType.STOP_LIMIT)
            .set_stop_price(stop_price)
            .set_price(limit_price))


def __trailing_stop(
        instruction: EquityInstruction, symbol: str, quantity: int | float,
        offset: int | float, offset_type: StopPriceLinkType,
        basis: StopPriceLinkBasis) -> OrderBuilder:
    from schwab.orders.common import OrderType, StopPriceLinkBasis
    from schwab.orders.common import StopPriceLinkType

    # Validate eagerly: mixing up percent and dollar offsets silently puts the
    # stop in the wrong place, and Schwab accepts either.
    if not isinstance(offset_type, StopPriceLinkType):
        raise ValueError(
                'offset_type must be a StopPriceLinkType, such as '
                'StopPriceLinkType.PERCENT or StopPriceLinkType.VALUE')
    if not isinstance(basis, StopPriceLinkBasis):
        raise ValueError('basis must be a StopPriceLinkBasis')

    return (__base(instruction, symbol, quantity, OrderType.TRAILING_STOP)
            .set_stop_price_link_basis(basis)
            .set_stop_price_link_type(offset_type)
            .set_stop_price_offset(offset))


def __market_on_close(
        instruction: EquityInstruction, symbol: str, quantity: int | float
) -> OrderBuilder:
    from schwab.orders.common import OrderType

    return __base(instruction, symbol, quantity, OrderType.MARKET_ON_CLOSE)


def __limit_on_close(
        instruction: EquityInstruction, symbol: str, quantity: int | float,
        price: str | decimal.Decimal | float) -> OrderBuilder:
    from schwab.orders.common import OrderType

    return (__base(instruction, symbol, quantity, OrderType.LIMIT_ON_CLOSE)
            .set_price(price))


def equity_buy_stop(
        symbol: str, quantity: int | float,
        stop_price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy stop order, which becomes a market order once ``stop_price`` is
    reached.
    '''
    from schwab.orders.common import EquityInstruction
    return __stop(EquityInstruction.BUY, symbol, quantity, stop_price)


def equity_buy_stop_limit(
        symbol: str, quantity: int | float,
        stop_price: str | decimal.Decimal | float,
        limit_price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy stop limit order, which becomes a limit order at ``limit_price``
    once ``stop_price`` is reached.
    '''
    from schwab.orders.common import EquityInstruction
    return __stop_limit(EquityInstruction.BUY, symbol, quantity,
                        stop_price, limit_price)


def equity_buy_trailing_stop(
        symbol: str, quantity: int | float, offset: int | float, *,
        offset_type: StopPriceLinkType,
        basis: StopPriceLinkBasis = StopPriceLinkBasis.LAST) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy trailing stop order. The stop price trails ``basis`` by
    ``offset``.

    :param offset: The distance between the stop price and ``basis``.
    :param offset_type: How ``offset`` is interpreted. Required, since mixing
                        these up silently places the stop in the wrong place.
                        Use ``StopPriceLinkType.PERCENT`` for a percentage
                        (``2.5`` means 2.5%) or ``StopPriceLinkType.VALUE`` for
                        a dollar amount (``2.5`` means $2.50).
    :param basis: The price the stop trails. See
                  :class:`~schwab.orders.common.StopPriceLinkBasis`. Defaults
                  to the last trade price.
    '''
    from schwab.orders.common import EquityInstruction
    return __trailing_stop(EquityInstruction.BUY, symbol, quantity,
                           offset, offset_type, basis)


def equity_buy_market_on_close(
        symbol: str, quantity: int | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy market-on-close order, which executes at the closing price.
    '''
    from schwab.orders.common import EquityInstruction
    return __market_on_close(EquityInstruction.BUY, symbol, quantity)


def equity_buy_limit_on_close(
        symbol: str, quantity: int | float,
        price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy limit-on-close order, which executes at the closing price if it is
    at or better than ``price``.
    '''
    from schwab.orders.common import EquityInstruction
    return __limit_on_close(EquityInstruction.BUY, symbol, quantity, price)


def equity_sell_stop(
        symbol: str, quantity: int | float,
        stop_price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    sell stop order, which becomes a market order once ``stop_price`` is
    reached.
    '''
    from schwab.orders.common import EquityInstruction
    return __stop(EquityInstruction.SELL, symbol, quantity, stop_price)


def equity_sell_stop_limit(
        symbol: str, quantity: int | float,
        stop_price: str | decimal.Decimal | float,
        limit_price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    sell stop limit order, which becomes a limit order at ``limit_price``
    once ``stop_price`` is reached.
    '''
    from schwab.orders.common import EquityInstruction
    return __stop_limit(EquityInstruction.SELL, symbol, quantity,
                        stop_price, limit_price)


def equity_sell_trailing_stop(
        symbol: str, quantity: int | float, offset: int | float, *,
        offset_type: StopPriceLinkType,
        basis: StopPriceLinkBasis = StopPriceLinkBasis.LAST) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    sell trailing stop order. The stop price trails ``basis`` by
    ``offset``.

    :param offset: The distance between the stop price and ``basis``.
    :param offset_type: How ``offset`` is interpreted. Required, since mixing
                        these up silently places the stop in the wrong place.
                        Use ``StopPriceLinkType.PERCENT`` for a percentage
                        (``2.5`` means 2.5%) or ``StopPriceLinkType.VALUE`` for
                        a dollar amount (``2.5`` means $2.50).
    :param basis: The price the stop trails. See
                  :class:`~schwab.orders.common.StopPriceLinkBasis`. Defaults
                  to the last trade price.
    '''
    from schwab.orders.common import EquityInstruction
    return __trailing_stop(EquityInstruction.SELL, symbol, quantity,
                           offset, offset_type, basis)


def equity_sell_market_on_close(
        symbol: str, quantity: int | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    sell market-on-close order, which executes at the closing price.
    '''
    from schwab.orders.common import EquityInstruction
    return __market_on_close(EquityInstruction.SELL, symbol, quantity)


def equity_sell_limit_on_close(
        symbol: str, quantity: int | float,
        price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    sell limit-on-close order, which executes at the closing price if it is
    at or better than ``price``.
    '''
    from schwab.orders.common import EquityInstruction
    return __limit_on_close(EquityInstruction.SELL, symbol, quantity, price)


def equity_sell_short_stop(
        symbol: str, quantity: int | float,
        stop_price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    short sell stop order, which becomes a market order once ``stop_price`` is
    reached.
    '''
    from schwab.orders.common import EquityInstruction
    return __stop(EquityInstruction.SELL_SHORT, symbol, quantity, stop_price)


def equity_sell_short_stop_limit(
        symbol: str, quantity: int | float,
        stop_price: str | decimal.Decimal | float,
        limit_price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    short sell stop limit order, which becomes a limit order at ``limit_price``
    once ``stop_price`` is reached.
    '''
    from schwab.orders.common import EquityInstruction
    return __stop_limit(EquityInstruction.SELL_SHORT, symbol, quantity,
                        stop_price, limit_price)


def equity_sell_short_trailing_stop(
        symbol: str, quantity: int | float, offset: int | float, *,
        offset_type: StopPriceLinkType,
        basis: StopPriceLinkBasis = StopPriceLinkBasis.LAST) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    short sell trailing stop order. The stop price trails ``basis`` by
    ``offset``.

    :param offset: The distance between the stop price and ``basis``.
    :param offset_type: How ``offset`` is interpreted. Required, since mixing
                        these up silently places the stop in the wrong place.
                        Use ``StopPriceLinkType.PERCENT`` for a percentage
                        (``2.5`` means 2.5%) or ``StopPriceLinkType.VALUE`` for
                        a dollar amount (``2.5`` means $2.50).
    :param basis: The price the stop trails. See
                  :class:`~schwab.orders.common.StopPriceLinkBasis`. Defaults
                  to the last trade price.
    '''
    from schwab.orders.common import EquityInstruction
    return __trailing_stop(EquityInstruction.SELL_SHORT, symbol, quantity,
                           offset, offset_type, basis)


def equity_sell_short_market_on_close(
        symbol: str, quantity: int | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    short sell market-on-close order, which executes at the closing price.
    '''
    from schwab.orders.common import EquityInstruction
    return __market_on_close(EquityInstruction.SELL_SHORT, symbol, quantity)


def equity_sell_short_limit_on_close(
        symbol: str, quantity: int | float,
        price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    short sell limit-on-close order, which executes at the closing price if it is
    at or better than ``price``.
    '''
    from schwab.orders.common import EquityInstruction
    return __limit_on_close(EquityInstruction.SELL_SHORT, symbol, quantity, price)


def equity_buy_to_cover_stop(
        symbol: str, quantity: int | float,
        stop_price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy-to-cover stop order, which becomes a market order once ``stop_price`` is
    reached.
    '''
    from schwab.orders.common import EquityInstruction
    return __stop(EquityInstruction.BUY_TO_COVER, symbol, quantity, stop_price)


def equity_buy_to_cover_stop_limit(
        symbol: str, quantity: int | float,
        stop_price: str | decimal.Decimal | float,
        limit_price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy-to-cover stop limit order, which becomes a limit order at ``limit_price``
    once ``stop_price`` is reached.
    '''
    from schwab.orders.common import EquityInstruction
    return __stop_limit(EquityInstruction.BUY_TO_COVER, symbol, quantity,
                        stop_price, limit_price)


def equity_buy_to_cover_trailing_stop(
        symbol: str, quantity: int | float, offset: int | float, *,
        offset_type: StopPriceLinkType,
        basis: StopPriceLinkBasis = StopPriceLinkBasis.LAST) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy-to-cover trailing stop order. The stop price trails ``basis`` by
    ``offset``.

    :param offset: The distance between the stop price and ``basis``.
    :param offset_type: How ``offset`` is interpreted. Required, since mixing
                        these up silently places the stop in the wrong place.
                        Use ``StopPriceLinkType.PERCENT`` for a percentage
                        (``2.5`` means 2.5%) or ``StopPriceLinkType.VALUE`` for
                        a dollar amount (``2.5`` means $2.50).
    :param basis: The price the stop trails. See
                  :class:`~schwab.orders.common.StopPriceLinkBasis`. Defaults
                  to the last trade price.
    '''
    from schwab.orders.common import EquityInstruction
    return __trailing_stop(EquityInstruction.BUY_TO_COVER, symbol, quantity,
                           offset, offset_type, basis)


def equity_buy_to_cover_market_on_close(
        symbol: str, quantity: int | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy-to-cover market-on-close order, which executes at the closing price.
    '''
    from schwab.orders.common import EquityInstruction
    return __market_on_close(EquityInstruction.BUY_TO_COVER, symbol, quantity)


def equity_buy_to_cover_limit_on_close(
        symbol: str, quantity: int | float,
        price: str | decimal.Decimal | float) -> OrderBuilder:
    '''
    Returns a pre-filled :class:`~schwab.orders.generic.OrderBuilder` for an equity
    buy-to-cover limit-on-close order, which executes at the closing price if it is
    at or better than ``price``.
    '''
    from schwab.orders.common import EquityInstruction
    return __limit_on_close(EquityInstruction.BUY_TO_COVER, symbol, quantity, price)
