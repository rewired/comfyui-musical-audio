def encode_vlq(value: int) -> bytes:
    if type(value) is not int or not 0 <= value <= 0x0FFFFFFF:
        raise ValueError("VLQ value must be an integer from 0 through 0x0FFFFFFF")
    encoded = [value & 0x7F]
    value >>= 7
    while value:
        encoded.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(encoded))


def event(delta: int, payload: bytes) -> bytes:
    return encode_vlq(delta) + payload


def meta_event(delta: int, meta_type: int, payload: bytes = b"") -> bytes:
    if not 0 <= meta_type <= 0x7F:
        raise ValueError("meta_type must be a data byte")
    return event(delta, b"\xFF" + bytes((meta_type,)) + encode_vlq(len(payload)) + payload)


def channel_event(
    delta: int,
    status: int,
    data: bytes,
    *,
    running: bool = False,
) -> bytes:
    if not 0x80 <= status <= 0xEF:
        raise ValueError("status must be a channel status")
    width = 1 if status & 0xF0 in (0xC0, 0xD0) else 2
    if len(data) != width or any(byte >= 0x80 for byte in data):
        raise ValueError("channel data has the wrong width or contains a status byte")
    return event(delta, data if running else bytes((status,)) + data)


def sysex_event(delta: int, payload: bytes, *, escaped: bool = False) -> bytes:
    status = b"\xF7" if escaped else b"\xF0"
    return event(delta, status + encode_vlq(len(payload)) + payload)


def track_chunk(
    events: list[bytes] | tuple[bytes, ...],
    *,
    include_eot: bool = True,
    eot_delta: int = 0,
) -> bytes:
    payload = b"".join(events)
    if include_eot:
        payload += meta_event(eot_delta, 0x2F)
    return b"MTrk" + len(payload).to_bytes(4, "big") + payload


def smf(
    tracks: list[bytes] | tuple[bytes, ...],
    *,
    midi_format: int | None = None,
    ticks_per_quarter: int = 480,
) -> bytes:
    if not tracks:
        raise ValueError("at least one track is required")
    if midi_format is None:
        midi_format = 0 if len(tracks) == 1 else 1
    if midi_format not in (0, 1):
        raise ValueError("the valid-case writer supports only formats 0 and 1")
    if midi_format == 0 and len(tracks) != 1:
        raise ValueError("format 0 requires exactly one track")
    if type(ticks_per_quarter) is not int or not 1 <= ticks_per_quarter <= 0x7FFF:
        raise ValueError("ticks_per_quarter must be a positive PPQ division")
    header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + midi_format.to_bytes(2, "big")
        + len(tracks).to_bytes(2, "big")
        + ticks_per_quarter.to_bytes(2, "big")
    )
    return header + b"".join(tracks)
