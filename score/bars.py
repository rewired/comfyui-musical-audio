from collections.abc import Sequence
from dataclasses import dataclass

from score.model import MeterEvent


@dataclass(frozen=True)
class BarGrid:
    boundaries: tuple[int, ...]
    meters_by_bar: tuple[MeterEvent, ...]
    midbar_change_ticks: tuple[int, ...]
    diagnostics: tuple[str, ...]
    has_variable_meter: bool
    has_midbar_meter_change: bool


def _round_half_away_from_zero_ratio(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return sign * quotient


def _validate_inputs(
    ticks_per_quarter: int,
    meters: Sequence[MeterEvent],
    through_tick: int,
) -> tuple[MeterEvent, ...]:
    if type(ticks_per_quarter) is not int or ticks_per_quarter <= 0:
        raise ValueError("ticks_per_quarter must be a positive built-in int")
    if type(through_tick) is not int or through_tick < 0:
        raise ValueError("through_tick must be a nonnegative built-in int")
    if isinstance(meters, (str, bytes)):
        raise TypeError("meters must be a sequence of MeterEvent values")

    canonical = tuple(meters)
    if not canonical:
        raise ValueError("meters must not be empty")
    if not all(type(meter) is MeterEvent for meter in canonical):
        raise TypeError("meters must contain only MeterEvent values")
    if canonical[0].tick != 0:
        raise ValueError("the first effective meter must begin at tick 0")

    previous_tick = -1
    previous_signature: tuple[int, int] | None = None
    for meter in canonical:
        if type(meter.tick) is not int or meter.tick < 0:
            raise ValueError("meter ticks must be nonnegative built-in ints")
        if type(meter.numerator) is not int or meter.numerator <= 0:
            raise ValueError("meter numerators must be positive built-in ints")
        if (
            type(meter.denominator) is not int
            or meter.denominator <= 0
            or meter.denominator > 64
            or meter.denominator & (meter.denominator - 1)
        ):
            raise ValueError("meter denominators must be powers of two through 64")
        if meter.tick <= previous_tick:
            raise ValueError("meters must be strictly sorted by tick")
        signature = (meter.numerator, meter.denominator)
        if signature == previous_signature:
            raise ValueError("meters must not contain consecutive equivalent events")
        previous_tick = meter.tick
        previous_signature = signature

    if through_tick < canonical[-1].tick:
        raise ValueError("through_tick must reach the final effective meter")
    return canonical


def _absolute_boundary(
    anchor_tick: int,
    bar_index: int,
    meter: MeterEvent,
    ticks_per_quarter: int,
) -> int:
    numerator = bar_index * meter.numerator * 4 * ticks_per_quarter
    return anchor_tick + _round_half_away_from_zero_ratio(
        numerator,
        meter.denominator,
    )


def build_bar_grid(
    *,
    ticks_per_quarter: int,
    meters: Sequence[MeterEvent],
    through_tick: int,
) -> BarGrid:
    canonical = _validate_inputs(ticks_per_quarter, meters, through_tick)
    boundaries = [0]
    meters_by_bar: list[MeterEvent] = []
    midbar_change_ticks: list[int] = []
    diagnostics: list[str] = []

    meter_index = 0
    current_meter = canonical[0]
    anchor_tick = current_meter.tick
    bar_index = 1

    while boundaries[-1] <= through_tick:
        next_boundary = _absolute_boundary(
            anchor_tick,
            bar_index,
            current_meter,
            ticks_per_quarter,
        )
        if next_boundary <= boundaries[-1]:
            raise ValueError("meter resolution does not produce increasing boundaries")

        next_meter = (
            canonical[meter_index + 1]
            if meter_index + 1 < len(canonical)
            else None
        )
        if next_meter is not None and next_meter.tick <= next_boundary:
            meters_by_bar.append(current_meter)
            boundaries.append(next_meter.tick)
            if next_meter.tick < next_boundary:
                midbar_change_ticks.append(next_meter.tick)
                diagnostics.append(
                    f"meter change at tick {next_meter.tick} shortened a bar"
                )
            meter_index += 1
            current_meter = next_meter
            anchor_tick = next_meter.tick
            bar_index = 1
            continue

        meters_by_bar.append(current_meter)
        boundaries.append(next_boundary)
        bar_index += 1

    signatures = {(meter.numerator, meter.denominator) for meter in canonical}
    return BarGrid(
        boundaries=tuple(boundaries),
        meters_by_bar=tuple(meters_by_bar),
        midbar_change_ticks=tuple(midbar_change_ticks),
        diagnostics=tuple(diagnostics),
        has_variable_meter=len(signatures) > 1,
        has_midbar_meter_change=bool(midbar_change_ticks),
    )
