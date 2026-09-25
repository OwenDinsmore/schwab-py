from __future__ import annotations

import decimal
import warnings

from typing import Any


from schwab.orders import common
from schwab.utils import EnumEnforcer

from schwab._http import httpx


def _build_object(obj: Any) -> Any:
    # Literals are passed straight through
    if isinstance(obj, str) or isinstance(obj, int) or isinstance(obj, float):
        return obj

    # Note enums are not handled because call callers convert their enums to
    # values.

    # Dicts and lists are iterated over, with keys intact
    elif isinstance(obj, dict):
        return dict((key, _build_object(value)) for key, value in obj.items())
    elif isinstance(obj, list):
        return [_build_object(i) for i in obj]

    # Objects have their variables translated into keys
    else:
        ret = {}
        for name, value in vars(obj).items():
            if value is None or name[0] != '_':
                continue

            name = name[1:]
            ret[name] = _build_object(value)
        return ret


def truncate_float(flt: float) -> str:
    warnings.warn('passing floats to set_price and set_stop_price is '+
                  'deprecated and will be removed soon. Please update your '+
                  'code to pass prices as strings instead.')
    return _truncate_price(flt)


def _truncate_price(price: str | decimal.Decimal | float) -> str:
    '''
    Truncates (not rounds) a price to four decimal places if its absolute value
    is less than one, and to two decimal places otherwise.
    '''
    # Floats are converted through their shortest round-tripping repr, so a
    # value like 8.2 is treated as exactly 8.2 rather than as its binary
    # approximation, 8.1999999999999993. Scaling the binary value and
    # truncating it, as this function used to do, knocked about 5% of prices
    # down by one tick.
    if isinstance(price, float):
        price = decimal.Decimal(repr(price))
    else:
        price = decimal.Decimal(price)

    if abs(price) < 1 and price != 0:
        quantum = decimal.Decimal('0.0001')
    else:
        quantum = decimal.Decimal('0.01')
    return str(price.quantize(quantum, rounding=decimal.ROUND_DOWN))


class OrderBuilder(EnumEnforcer):
    '''
    Helper class to create arbitrarily complex orders. Note this class simply
    implements the order schema defined in Schwab's `Trader API documentation
    <https://developer.schwab.com/>`__, with no attempts to validate the result.
    Orders created using this class may be rejected or may never fill. Use at
    your own risk.
    '''

    def __init__(self, *, enforce_enums: bool = True) -> None:
        super().__init__(enforce_enums)

        self._session: Any = None
        self._duration: Any = None
        self._orderType: Any = None
        self._complexOrderStrategyType: Any = None
        self._quantity: int | float | None = None
        self._destinationLinkName: Any = None
        self._stopPrice: str | float | None = None
        self._stopPriceLinkBasis: Any = None
        self._stopPriceLinkType: Any = None
        self._stopPriceOffset: int | float | None = None
        self._stopType: Any = None
        self._priceLinkBasis: Any = None
        self._priceLinkType: Any = None
        self._priceOffset: int | float | None = None
        self._price: str | float | None = None
        self._orderLegCollection: list[dict[str, Any]] | None = None
        self._activationPrice: int | float | None = None
        self._specialInstruction: Any = None
        self._orderStrategyType: Any = None
        self._childOrderStrategies: (
            list[OrderBuilder | dict[str, Any]] | None) = None

    # Session
    def set_session(self, session: common.Session) -> OrderBuilder:
        '''
        Set the order session. See :class:`~schwab.orders.common.Session` for
        details.
        '''
        session = self.convert_enum(session, common.Session)
        self._session = session
        return self

    def clear_session(self) -> OrderBuilder:
        '''
        Clear the order session.
        '''
        self._session = None
        return self

    # Duration
    def set_duration(self, duration: common.Duration) -> OrderBuilder:
        '''
        Set the order duration. See :class:`~schwab.orders.common.Duration` for
        details.
        '''
        duration = self.convert_enum(duration, common.Duration)
        self._duration = duration
        return self

    def clear_duration(self) -> OrderBuilder:
        '''
        Clear the order duration.
        '''
        self._duration = None
        return self

    # OrderType
    def set_order_type(self, order_type: common.OrderType) -> OrderBuilder:
        '''
        Set the order type. See :class:`~schwab.orders.common.OrderType` for
        details.
        '''
        order_type = self.convert_enum(order_type, common.OrderType)
        self._orderType = order_type
        return self

    def clear_order_type(self) -> OrderBuilder:
        '''
        Clear the order type.
        '''
        self._orderType = None
        return self

    # ComplexOrderStrategyType
    def set_complex_order_strategy_type(
            self, complex_order_strategy_type: common.ComplexOrderStrategyType
    ) -> OrderBuilder:
        '''
        Set the complex order strategy type. See
        :class:`~schwab.orders.common.ComplexOrderStrategyType` for details.
        '''
        complex_order_strategy_type = self.convert_enum(
            complex_order_strategy_type, common.ComplexOrderStrategyType)
        self._complexOrderStrategyType = complex_order_strategy_type
        return self

    def clear_complex_order_strategy_type(self) -> OrderBuilder:
        '''
        Clear the complex order strategy type.
        '''
        self._complexOrderStrategyType = None
        return self

    # Quantity
    def set_quantity(self, quantity: int | float) -> OrderBuilder:
        '''
        Exact semantics unknown. See :ref:`undocumented_quantity` for a
        discussion.
        '''
        if quantity <= 0:
            raise ValueError('quantity must be positive')
        self._quantity = quantity
        return self

    def clear_quantity(self) -> OrderBuilder:
        '''
        Clear the order-level quantity. Note this does not affect order legs.
        '''
        self._quantity = None
        return self

    # DestinationLinkName
    def set_destination_link_name(
            self, destination_link_name: common.Destination) -> OrderBuilder:
        '''
        Set the destination link name. See
        :class:`~schwab.orders.common.Destination` for details.
        '''
        destination_link_name = self.convert_enum(
            destination_link_name, common.Destination)
        self._destinationLinkName = destination_link_name
        return self

    def clear_destination_link_name(self) -> OrderBuilder:
        '''
        Clear the destination link name
        '''
        self._destinationLinkName = None
        return self

    # StopPrice
    def set_stop_price(
            self, stop_price: str | decimal.Decimal | float) -> OrderBuilder:
        '''
        Set the stop price. Note price can be passed as a `str`, a
        `decimal.Decimal`, or a `float`. See :ref:`number_truncation`.
        '''
        if isinstance(stop_price, str):
            self._stopPrice = stop_price
        elif isinstance(stop_price, decimal.Decimal):
            self._stopPrice = _truncate_price(stop_price)
        else:
            self._stopPrice = truncate_float(stop_price)
        return self

    def copy_stop_price(self, stop_price: str | float) -> OrderBuilder:
        '''
        Directly set the stop price, avoiding all the validation and truncation
        logic from :func:`set_stop_price`.
        '''
        self._stopPrice = stop_price
        return self

    def clear_stop_price(self) -> OrderBuilder:
        '''
        Clear the stop price.
        '''
        self._stopPrice = None
        return self

    # StopPriceLinkBasis
    def set_stop_price_link_basis(
            self, stop_price_link_basis: common.StopPriceLinkBasis
    ) -> OrderBuilder:
        '''
        Set the stop price link basis. See
        :class:`~schwab.orders.common.StopPriceLinkBasis` for details.
        '''
        stop_price_link_basis = self.convert_enum(
            stop_price_link_basis, common.StopPriceLinkBasis)
        self._stopPriceLinkBasis = stop_price_link_basis
        return self

    def clear_stop_price_link_basis(self) -> OrderBuilder:
        '''
        Clear the stop price link basis.
        '''
        self._stopPriceLinkBasis = None
        return self

    # StopPriceLinkType
    def set_stop_price_link_type(
            self, stop_price_link_type: common.StopPriceLinkType
    ) -> OrderBuilder:
        '''
        Set the stop price link type. See
        :class:`~schwab.orders.common.StopPriceLinkType` for details.
        '''
        stop_price_link_type = self.convert_enum(
            stop_price_link_type, common.StopPriceLinkType)
        self._stopPriceLinkType = stop_price_link_type
        return self

    def clear_stop_price_link_type(self) -> OrderBuilder:
        '''
        Clear the stop price link type.
        '''
        self._stopPriceLinkType = None
        return self

    # StopPriceOffset
    def set_stop_price_offset(
            self, stop_price_offset: int | float) -> OrderBuilder:
        '''
        Set the stop price offset.
        '''
        self._stopPriceOffset = stop_price_offset
        return self

    def clear_stop_price_offset(self) -> OrderBuilder:
        '''
        Clear the stop price offset.
        '''
        self._stopPriceOffset = None
        return self

    # StopType
    def set_stop_type(self, stop_type: common.StopType) -> OrderBuilder:
        '''
        Set the stop type. See
        :class:`~schwab.orders.common.StopType` for more details.
        '''
        stop_type = self.convert_enum(stop_type, common.StopType)
        self._stopType = stop_type
        return self

    def clear_stop_type(self) -> OrderBuilder:
        '''
        Clear the stop type.
        '''
        self._stopType = None
        return self

    # PriceLinkBasis
    def set_price_link_basis(
            self, price_link_basis: common.PriceLinkBasis) -> OrderBuilder:
        '''
        Set the price link basis. See
        :class:`~schwab.orders.common.PriceLinkBasis` for details.
        '''
        price_link_basis = self.convert_enum(
            price_link_basis, common.PriceLinkBasis)
        self._priceLinkBasis = price_link_basis
        return self

    def clear_price_link_basis(self) -> OrderBuilder:
        '''
        Clear the price link basis.
        '''
        self._priceLinkBasis = None
        return self

    # PriceLinkType
    def set_price_link_type(
            self, price_link_type: common.PriceLinkType) -> OrderBuilder:
        '''
        Set the price link type. See
        :class:`~schwab.orders.common.PriceLinkType` for more details.
        '''
        price_link_type = self.convert_enum(
            price_link_type, common.PriceLinkType)
        self._priceLinkType = price_link_type
        return self

    def clear_price_link_type(self) -> OrderBuilder:
        '''
        Clear the price link basis.
        '''
        self._priceLinkType = None
        return self

    def set_price_offset(self, price_offset: int | float) -> OrderBuilder:
        '''
        Set the price offset. Used with ``TRAILING_STOP_LIMIT`` orders to
        determine the limit price relative to the trailing stop price.
        '''
        self._priceOffset = price_offset
        return self

    def clear_price_offset(self) -> OrderBuilder:
        '''
        Clear the price offset.
        '''
        self._priceOffset = None
        return self

    # Price
    def set_price(self, price: str | decimal.Decimal | float) -> OrderBuilder:
        '''
        Set the order price. Note price can be passed as a `str`, a
        `decimal.Decimal`, or a `float`. See :ref:`number_truncation`.
        '''
        if isinstance(price, str):
            self._price = price
        elif isinstance(price, decimal.Decimal):
            self._price = _truncate_price(price)
        else:
            self._price = truncate_float(price)
        return self

    def copy_price(self, price: str | float) -> OrderBuilder:
        '''
        Directly set the stop price, avoiding all the validation and truncation
        logic from :func:`set_price`.
        '''
        self._price = price
        return self

    def clear_price(self) -> OrderBuilder:
        '''
        Clear the order price
        '''
        self._price = None
        return self

    # ActivationPrice
    def set_activation_price(
            self, activation_price: int | float) -> OrderBuilder:
        '''
        Set the activation price.
        '''
        if activation_price <= 0.0:
            raise ValueError('activation price must be positive')
        self._activationPrice = activation_price
        return self

    def clear_activation_price(self) -> OrderBuilder:
        '''
        Clear the activation price.
        '''
        self._activationPrice = None
        return self

    # SpecialInstruction
    def set_special_instruction(
            self, special_instruction: common.SpecialInstruction
    ) -> OrderBuilder:
        '''
        Set the special instruction. See
        :class:`~schwab.orders.common.SpecialInstruction` for details.
        '''
        special_instruction = self.convert_enum(
            special_instruction, common.SpecialInstruction)
        self._specialInstruction = special_instruction
        return self

    def clear_special_instruction(self) -> OrderBuilder:
        '''
        Clear the special instruction.
        '''
        self._specialInstruction = None
        return self

    # OrderStrategyType
    def set_order_strategy_type(
            self, order_strategy_type: common.OrderStrategyType
    ) -> OrderBuilder:
        '''
        Set the order strategy type. See
        :class:`~schwab.orders.common.OrderStrategyType` for more details.
        '''
        order_strategy_type = self.convert_enum(
            order_strategy_type, common.OrderStrategyType)
        self._orderStrategyType = order_strategy_type
        return self

    def clear_order_strategy_type(self) -> OrderBuilder:
        '''
        Clear the order strategy type.
        '''
        self._orderStrategyType = None
        return self

    # ChildOrderStrategies
    def add_child_order_strategy(
            self, child_order_strategy: OrderBuilder | dict[str, Any]
    ) -> OrderBuilder:
        if isinstance(child_order_strategy, httpx.Response):
            raise ValueError(
                    'Child order cannot be a response. See here for ' +
                    'details: https://schwab-api.readthedocs.io/en/latest/' +
                    'order-templates.html#utility-methods')

        if (not isinstance(child_order_strategy, OrderBuilder)
                and not isinstance(child_order_strategy, dict)):
            raise ValueError('child order must be OrderBuilder or dict')

        if self._childOrderStrategies is None:
            self._childOrderStrategies = []

        self._childOrderStrategies.append(child_order_strategy)
        return self

    def clear_child_order_strategies(self) -> OrderBuilder:
        self._childOrderStrategies = None
        return self

    # OrderLegCollection
    def __add_order_leg(
            self, instruction: Any,
            instrument: common.EquityInstrument | common.OptionInstrument,
            quantity: int | float) -> OrderBuilder:
        # instruction is assumed to have been verified

        if quantity <= 0:
            raise ValueError('quantity must be positive')

        if self._orderLegCollection is None:
            self._orderLegCollection = []

        self._orderLegCollection.append({
            'instruction': instruction,
            'instrument': instrument,
            'quantity': quantity,
        })

        return self

    def add_equity_leg(
            self, instruction: common.EquityInstruction, symbol: str,
            quantity: int | float) -> OrderBuilder:
        '''
        Add an equity order leg.

        :param instruction: Instruction for the leg. See
                            :class:`~schwab.orders.common.EquityInstruction` for
                            valid options.
        :param symbol: Equity symbol
        :param quantity: Number of shares for the order
        '''
        instruction = self.convert_enum(instruction, common.EquityInstruction)
        return self.__add_order_leg(
            instruction, common.EquityInstrument(symbol), quantity)

    def add_option_leg(
            self, instruction: common.OptionInstruction, symbol: str,
            quantity: int | float) -> OrderBuilder:
        '''
        Add an option order leg.

        :param instruction: Instruction for the leg. See
                            :class:`~schwab.orders.common.OptionInstruction` for
                            valid options.
        :param symbol: Option symbol
        :param quantity: Number of contracts for the order
        '''
        instruction = self.convert_enum(instruction, common.OptionInstruction)
        return self.__add_order_leg(
            instruction, common.OptionInstrument(symbol), quantity)

    def clear_order_legs(self) -> OrderBuilder:
        '''
        Clear all order legs.
        '''
        self._orderLegCollection = None
        return self

    # Build

    def build(self) -> dict[str, Any]:
        return _build_object(self)
