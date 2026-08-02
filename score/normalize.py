from collections.abc import Sequence
from dataclasses import dataclass

from score.bars import BarGrid
from score.model import (
    Marker,
    MeterEvent,
    Score,
    ScoreFormat,
    Section,
    TempoEvent,
)


@dataclass(frozen=True, order=True)
class OrderedTempoEvent:
    tick: int
    track_index: int
    event_index: int
    us_per_quarter: int


@dataclass(frozen=True, order=True)
class OrderedMeterEvent:
    tick: int
    track_index: int
    event_index: int
    numerator: int
    denominator: int


@dataclass(frozen=True)
class NormalizedEvents:
    ticks_per_quarter: int
    tempos: tuple[TempoEvent, ...]
    meters: tuple[MeterEvent, ...]
    markers: tuple[Marker, ...]


def _validate_nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative built-in int")
    return value


def _ordered_tempos(
    tempos: Sequence[OrderedTempoEvent | TempoEvent],
) -> list[OrderedTempoEvent]:
    ordered: list[OrderedTempoEvent] = []
    for input_index, tempo in enumerate(tempos):
        if type(tempo) is OrderedTempoEvent:
            item = tempo
        elif type(tempo) is TempoEvent:
            item = OrderedTempoEvent(
                tick=tempo.tick,
                track_index=0,
                event_index=input_index,
                us_per_quarter=tempo.us_per_quarter,
            )
        else:
            raise TypeError("tempos must contain ordered or canonical tempo events")
        _validate_nonnegative_int(item.tick, "tempo tick")
        _validate_nonnegative_int(item.track_index, "tempo track_index")
        _validate_nonnegative_int(item.event_index, "tempo event_index")
        if type(item.us_per_quarter) is not int or item.us_per_quarter <= 0:
            raise ValueError("us_per_quarter must be a positive built-in int")
        ordered.append(item)
    return sorted(ordered, key=lambda item: (item.tick, item.track_index, item.event_index))


def _ordered_meters(
    meters: Sequence[OrderedMeterEvent | MeterEvent],
) -> list[OrderedMeterEvent]:
    ordered: list[OrderedMeterEvent] = []
    for input_index, meter in enumerate(meters):
        if type(meter) is OrderedMeterEvent:
            item = meter
        elif type(meter) is MeterEvent:
            item = OrderedMeterEvent(
                tick=meter.tick,
                track_index=0,
                event_index=input_index,
                numerator=meter.numerator,
                denominator=meter.denominator,
            )
        else:
            raise TypeError("meters must contain ordered or canonical meter events")
        _validate_nonnegative_int(item.tick, "meter tick")
        _validate_nonnegative_int(item.track_index, "meter track_index")
        _validate_nonnegative_int(item.event_index, "meter event_index")
        if type(item.numerator) is not int or item.numerator <= 0:
            raise ValueError("meter numerator must be a positive built-in int")
        if (
            type(item.denominator) is not int
            or item.denominator <= 0
            or item.denominator > 64
            or item.denominator & (item.denominator - 1)
        ):
            raise ValueError("meter denominator must be a power of two through 64")
        ordered.append(item)
    return sorted(ordered, key=lambda item: (item.tick, item.track_index, item.event_index))


def _canonical_tempos(ordered: Sequence[OrderedTempoEvent]) -> tuple[TempoEvent, ...]:
    same_tick: list[TempoEvent] = []
    for item in ordered:
        value = TempoEvent(item.tick, item.us_per_quarter)
        if same_tick and same_tick[-1].tick == item.tick:
            same_tick[-1] = value
        else:
            same_tick.append(value)
    if not same_tick or same_tick[0].tick > 0:
        same_tick.insert(0, TempoEvent(0, 500_000))

    effective: list[TempoEvent] = []
    for item in same_tick:
        if not effective or item.us_per_quarter != effective[-1].us_per_quarter:
            effective.append(item)
    return tuple(effective)


def _canonical_meters(ordered: Sequence[OrderedMeterEvent]) -> tuple[MeterEvent, ...]:
    same_tick: list[MeterEvent] = []
    for item in ordered:
        value = MeterEvent(item.tick, item.numerator, item.denominator)
        if same_tick and same_tick[-1].tick == item.tick:
            same_tick[-1] = value
        else:
            same_tick.append(value)
    if not same_tick or same_tick[0].tick > 0:
        same_tick.insert(0, MeterEvent(0, 4, 4))

    effective: list[MeterEvent] = []
    for item in same_tick:
        signature = (item.numerator, item.denominator)
        if not effective or signature != (
            effective[-1].numerator,
            effective[-1].denominator,
        ):
            effective.append(item)
    return tuple(effective)


def normalize_events(
    *,
    ticks_per_quarter: int,
    tempos: Sequence[OrderedTempoEvent | TempoEvent],
    meters: Sequence[OrderedMeterEvent | MeterEvent],
    markers: Sequence[Marker],
) -> NormalizedEvents:
    if type(ticks_per_quarter) is not int or ticks_per_quarter <= 0:
        raise ValueError("ticks_per_quarter must be a positive built-in int")
    if isinstance(tempos, (str, bytes)) or isinstance(meters, (str, bytes)):
        raise TypeError("tempo and meter inputs must be event sequences")
    if isinstance(markers, (str, bytes)):
        raise TypeError("markers must be a sequence of Marker values")

    canonical_markers: list[Marker] = []
    for marker in markers:
        if type(marker) is not Marker:
            raise TypeError("markers must contain only Marker values")
        _validate_nonnegative_int(marker.tick, "marker tick")
        if type(marker.name) is not str:
            raise TypeError("marker names must be built-in strings")
        canonical_markers.append(marker)

    return NormalizedEvents(
        ticks_per_quarter=ticks_per_quarter,
        tempos=_canonical_tempos(_ordered_tempos(tempos)),
        meters=_canonical_meters(_ordered_meters(meters)),
        markers=tuple(sorted(canonical_markers, key=lambda item: (item.tick, item.name))),
    )


def derive_sections(
    *,
    markers: Sequence[Marker],
    end_tick_exclusive: int,
    bar_grid: BarGrid,
) -> tuple[Section, ...]:
    if type(end_tick_exclusive) is not int or end_tick_exclusive < 0:
        raise ValueError("end_tick_exclusive must be a nonnegative built-in int")
    if type(bar_grid) is not BarGrid:
        raise TypeError("bar_grid must be a BarGrid")
    marker_tuple = tuple(markers)
    if not all(type(marker) is Marker for marker in marker_tuple):
        raise TypeError("markers must contain only Marker values")
    if marker_tuple != tuple(sorted(marker_tuple, key=lambda item: (item.tick, item.name))):
        raise ValueError("markers must already be canonically sorted")

    boundaries = set(bar_grid.boundaries)
    sections = []
    for index, marker in enumerate(marker_tuple):
        end_tick = (
            marker_tuple[index + 1].tick
            if index + 1 < len(marker_tuple)
            else end_tick_exclusive
        )
        sections.append(
            Section(
                name=marker.name,
                start_tick=marker.tick,
                end_tick_exclusive=end_tick,
                bar_aligned=marker.tick in boundaries,
                confidence=None,
            )
        )
    return tuple(
        sorted(
            sections,
            key=lambda item: (item.start_tick, item.end_tick_exclusive, item.name),
        )
    )


def finalize_score(
    *,
    events: NormalizedEvents,
    sections: Sequence[Section],
    source: ScoreFormat,
    meter_estimated: bool,
    bar_grid: BarGrid,
) -> Score:
    if type(events) is not NormalizedEvents:
        raise TypeError("events must be NormalizedEvents")
    if type(bar_grid) is not BarGrid:
        raise TypeError("bar_grid must be a BarGrid")
    if source not in ("json", "midi", "analyzed", "constant"):
        raise ValueError("source is not a supported ScoreFormat")
    if type(meter_estimated) is not bool:
        raise TypeError("meter_estimated must be a built-in bool")
    canonical_sections = tuple(sections)
    if not all(type(section) is Section for section in canonical_sections):
        raise TypeError("sections must contain only Section values")

    return Score(
        ticks_per_quarter=events.ticks_per_quarter,
        tempos=events.tempos,
        meters=events.meters,
        markers=events.markers,
        sections=tuple(
            sorted(
                canonical_sections,
                key=lambda item: (
                    item.start_tick,
                    item.end_tick_exclusive,
                    item.name,
                ),
            )
        ),
        source=source,
        meter_estimated=meter_estimated,
        has_variable_meter=bar_grid.has_variable_meter,
        has_midbar_meter_change=bar_grid.has_midbar_meter_change,
    )
