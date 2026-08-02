from dataclasses import dataclass

from score.bars import build_bar_grid
from score.model import Marker, Score
from score.normalize import (
    OrderedMeterEvent,
    OrderedTempoEvent,
    derive_sections,
    finalize_score,
    normalize_events,
)


@dataclass(frozen=True)
class ParsedMidiScore:
    score: Score
    end_tick_exclusive: int
    diagnostics: tuple[str, ...]


class MidiParseError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        byte_offset: int,
        track_index: int | None = None,
        event_index: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.byte_offset = byte_offset
        self.track_index = track_index
        self.event_index = event_index
        location = f"byte {byte_offset}"
        if track_index is not None:
            location += f", track {track_index}"
        if event_index is not None:
            location += f", event {event_index}"
        super().__init__(f"{code}: {message} ({location})")


def _error(
    code: str,
    message: str,
    offset: int,
    track_index: int | None = None,
    event_index: int | None = None,
) -> MidiParseError:
    return MidiParseError(code, message, offset, track_index, event_index)


def _read_vlq(
    data: bytes,
    position: int,
    end: int,
    track_index: int,
    event_index: int,
) -> tuple[int, int]:
    start = position
    value = 0
    for _ in range(4):
        if position >= end:
            raise _error(
                "invalid_vlq",
                "truncated variable-length quantity",
                start,
                track_index,
                event_index,
            )
        byte = data[position]
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, position
    raise _error(
        "invalid_vlq",
        "variable-length quantity exceeds four bytes",
        start,
        track_index,
        event_index,
    )


def _require_payload(
    position: int,
    length: int,
    end: int,
    track_index: int,
    event_index: int,
) -> int:
    payload_end = position + length
    if payload_end > end:
        raise _error(
            "truncated_track",
            "event payload exceeds the track chunk",
            position,
            track_index,
            event_index,
        )
    return payload_end


def _parse_track(
    data: bytes,
    start: int,
    end: int,
    track_index: int,
) -> tuple[
    list[OrderedTempoEvent],
    list[OrderedMeterEvent],
    list[Marker],
    int,
    list[str],
]:
    tempos: list[OrderedTempoEvent] = []
    meters: list[OrderedMeterEvent] = []
    markers: list[Marker] = []
    diagnostics: list[str] = []
    position = start
    absolute_tick = 0
    last_event_tick = 0
    event_index = 0
    running_status: int | None = None
    end_tick: int | None = None

    while position < end:
        delta, position = _read_vlq(
            data,
            position,
            end,
            track_index,
            event_index,
        )
        absolute_tick += delta
        if position >= end:
            raise _error(
                "truncated_track",
                "track event has no status byte",
                position,
                track_index,
                event_index,
            )

        status_offset = position
        first = data[position]
        position += 1
        running_data: int | None = None
        if first < 0x80:
            if running_status is None:
                raise _error(
                    "invalid_running_status",
                    "data byte has no active channel running status",
                    status_offset,
                    track_index,
                    event_index,
                )
            status = running_status
            running_data = first
        else:
            status = first
            if 0x80 <= status <= 0xEF:
                running_status = status
            else:
                running_status = None

        if 0x80 <= status <= 0xEF:
            width = 1 if status & 0xF0 in (0xC0, 0xD0) else 2
            consumed = 1 if running_data is not None else 0
            for _ in range(width - consumed):
                if position >= end:
                    raise _error(
                        "truncated_track",
                        "channel message exceeds the track chunk",
                        position,
                        track_index,
                        event_index,
                    )
                if data[position] >= 0x80:
                    raise _error(
                        "invalid_running_status",
                        "channel message contains a status byte as data",
                        position,
                        track_index,
                        event_index,
                    )
                position += 1
        elif status in (0xF0, 0xF7):
            length, position = _read_vlq(
                data,
                position,
                end,
                track_index,
                event_index,
            )
            position = _require_payload(
                position,
                length,
                end,
                track_index,
                event_index,
            )
        elif status == 0xFF:
            if position >= end:
                raise _error(
                    "truncated_track",
                    "meta event has no type byte",
                    position,
                    track_index,
                    event_index,
                )
            meta_type = data[position]
            position += 1
            length, position = _read_vlq(
                data,
                position,
                end,
                track_index,
                event_index,
            )
            payload_start = position
            payload_end = _require_payload(
                payload_start,
                length,
                end,
                track_index,
                event_index,
            )
            payload = data[payload_start:payload_end]
            position = payload_end

            if meta_type == 0x51:
                if length != 3:
                    raise _error(
                        "invalid_meta_length",
                        "Set Tempo payload must contain exactly three bytes",
                        payload_start,
                        track_index,
                        event_index,
                    )
                value = int.from_bytes(payload, "big")
                if value <= 0:
                    raise _error(
                        "invalid_tempo",
                        "Set Tempo value must be positive",
                        payload_start,
                        track_index,
                        event_index,
                    )
                tempos.append(
                    OrderedTempoEvent(
                        absolute_tick,
                        track_index,
                        event_index,
                        value,
                    )
                )
            elif meta_type == 0x58:
                if length != 4:
                    raise _error(
                        "invalid_meta_length",
                        "Time Signature payload must contain exactly four bytes",
                        payload_start,
                        track_index,
                        event_index,
                    )
                numerator = payload[0]
                denominator_exponent = payload[1]
                if numerator == 0 or denominator_exponent > 6:
                    raise _error(
                        "invalid_meter",
                        "Time Signature requires a positive numerator and dd <= 6",
                        payload_start,
                        track_index,
                        event_index,
                    )
                meters.append(
                    OrderedMeterEvent(
                        absolute_tick,
                        track_index,
                        event_index,
                        numerator,
                        1 << denominator_exponent,
                    )
                )
            elif meta_type == 0x06:
                try:
                    name = payload.decode("utf-8")
                except UnicodeDecodeError:
                    name = payload.decode("latin-1")
                    diagnostics.append(
                        f"marker at track {track_index} event {event_index} decoded as Latin-1"
                    )
                markers.append(Marker(absolute_tick, name))
            elif meta_type == 0x2F:
                if length != 0:
                    raise _error(
                        "invalid_meta_length",
                        "End of Track payload must be empty",
                        payload_start,
                        track_index,
                        event_index,
                    )
                end_tick = absolute_tick
                if position != end:
                    raise _error(
                        "event_after_eot",
                        "track contains bytes after End of Track",
                        position,
                        track_index,
                        event_index + 1,
                    )
        else:
            raise _error(
                "unsupported_status",
                f"unsupported system status 0x{status:02X}",
                status_offset,
                track_index,
                event_index,
            )

        last_event_tick = absolute_tick
        event_index += 1

    if end_tick is None:
        end_tick = last_event_tick
        diagnostics.append(
            f"track {track_index} missing End of Track; using tick {end_tick}"
        )
    return tempos, meters, markers, end_tick, diagnostics


def parse_midi(data: bytes) -> ParsedMidiScore:
    if type(data) is not bytes:
        raise _error("invalid_type", "MIDI input must be built-in bytes", 0)
    if len(data) < 8:
        raise _error("truncated_header", "MThd header is truncated", len(data))
    if data[:4] != b"MThd":
        raise _error("invalid_header", "file does not begin with MThd", 0)

    header_length = int.from_bytes(data[4:8], "big")
    if header_length != 6:
        raise _error("invalid_header", "MThd payload length must be 6", 4)
    if len(data) < 14:
        raise _error("truncated_header", "MThd payload is truncated", len(data))

    midi_format = int.from_bytes(data[8:10], "big")
    declared_tracks = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if midi_format not in (0, 1):
        raise _error("unsupported_format", f"SMF format {midi_format} is unsupported", 8)
    if declared_tracks <= 0 or (midi_format == 0 and declared_tracks != 1):
        raise _error(
            "track_count_mismatch",
            "declared track count is invalid for the SMF format",
            10,
        )
    if division & 0x8000:
        raise _error("smpte_division", "SMPTE division is unsupported", 12)
    if division == 0:
        raise _error("invalid_header", "PPQ division must be positive", 12)

    position = 14
    all_tempos: list[OrderedTempoEvent] = []
    all_meters: list[OrderedMeterEvent] = []
    all_markers: list[Marker] = []
    track_end_ticks: list[int] = []
    diagnostics: list[str] = []

    for track_index in range(declared_tracks):
        if position >= len(data):
            raise _error(
                "track_count_mismatch",
                "fewer MTrk chunks than declared",
                position,
                track_index,
            )
        if len(data) - position < 8:
            raise _error(
                "truncated_track",
                "MTrk header is truncated",
                position,
                track_index,
            )
        if data[position : position + 4] != b"MTrk":
            raise _error(
                "track_count_mismatch",
                "expected an MTrk chunk",
                position,
                track_index,
            )
        track_length = int.from_bytes(data[position + 4 : position + 8], "big")
        track_start = position + 8
        track_end = track_start + track_length
        if track_end > len(data):
            raise _error(
                "truncated_track",
                "MTrk payload is shorter than its declared length",
                track_start,
                track_index,
            )

        tempos, meters, markers, end_tick, track_diagnostics = _parse_track(
            data,
            track_start,
            track_end,
            track_index,
        )
        all_tempos.extend(tempos)
        all_meters.extend(meters)
        all_markers.extend(markers)
        track_end_ticks.append(end_tick)
        diagnostics.extend(track_diagnostics)
        position = track_end

    if position != len(data):
        if data[position : position + 4] == b"MTrk":
            raise _error(
                "track_count_mismatch",
                "more MTrk chunks than declared",
                position,
            )
        raise _error(
            "trailing_data",
            "unexpected bytes or chunks follow the declared tracks",
            position,
        )

    end_tick_exclusive = max(track_end_ticks)
    events = normalize_events(
        ticks_per_quarter=division,
        tempos=all_tempos,
        meters=all_meters,
        markers=all_markers,
    )
    through_tick = max(events.meters[-1].tick, end_tick_exclusive)
    bar_grid = build_bar_grid(
        ticks_per_quarter=division,
        meters=events.meters,
        through_tick=through_tick,
    )
    sections = derive_sections(
        markers=events.markers,
        end_tick_exclusive=end_tick_exclusive,
        bar_grid=bar_grid,
    )
    score = finalize_score(
        events=events,
        sections=sections,
        source="midi",
        meter_estimated=False,
        bar_grid=bar_grid,
    )
    return ParsedMidiScore(
        score=score,
        end_tick_exclusive=end_tick_exclusive,
        diagnostics=tuple(diagnostics) + bar_grid.diagnostics,
    )
