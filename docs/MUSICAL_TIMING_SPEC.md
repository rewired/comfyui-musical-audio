# Musical Timing Specification

This document defines the musical timing semantics used by
`MusicalLoadAudioUI`. Specification version 0.2 defines two timing profiles:
`ConstantTempoMap` and `ScoreTempoMap`. `ConstantTempoMap` preserves the
implemented constant-tempo behavior. `ScoreTempoMap` defines both the
delivered resolver surface and the complete TempoMap planning profile.

The [Score Subsystem: Tempo Map and Markers](SCORE_SUBSYSTEM.md) is the frozen
architecture contract for Score data and resolution. Audio decoding, trimming,
clamping, sample boundaries, returned waveform ranges, and output compatibility
remain governed by the
[Audio Integration Contract](AUDIO_INTEGRATION_SPEC.md).

## Status

Specification version: 0.2

The Score model, provider resolution, `ScoreResolver`, and the complete
`ScoreTempoMap` query surface are implemented. The delivered node can activate
only the smaller uniform-timing bridge and keeps unsupported Score shapes
display-only. The complete TempoMap planning profile specified below is not
yet implemented in the node.

This implementation-status paragraph is descriptive. It does not narrow the
normative complete-planning rules or make the delivered uniform bridge the
definition of complete Score timing.

Audio trimming remains sample-based and is governed by the
[Audio Integration Contract](AUDIO_INTEGRATION_SPEC.md). Video-frame values are
metadata and never determine audio sample boundaries.

## Scope and time domains

The timing model uses exactly four time concepts:

```text
tick          musical time
seconds       real time
video_frame   FPS-based image position
sample_index  position in an audio track
```

`tick` is the canonical internal unit for Score musical time. `seconds` is the
conversion boundary between a tempo map and real time. `video_frame` is derived
metadata. `sample_index` belongs to a decoded audio track and is resolved from
seconds using that track's sample rate.

The timing profiles own musical-position, tick, seconds, and video-frame
metadata calculations. They do not decode audio, inspect waveform length, or
choose the final sample range. Those operations belong to the audio integration
layer.

## Shared invariants

### Tick canon

Ticks are the canonical musical-time unit.

Score events, bar boundaries, grid boundaries, markers, and Section boundaries
use integer ticks. Conversion to seconds occurs at the timing profile's outer
boundary. An audio-derived provider must convert detected seconds to ticks
before producing a `Score`; downstream consumers do not receive a second-based
special case.

Synthetic Scores use `ticks_per_quarter = 960`.
MIDI Scores use the file's PPQ value. SMPTE division is not a PPQ tick axis and
is invalid for this contract.

### Deterministic rounding

Rounding is absolute, never cumulative.

Whenever a fractional grid position must become an integer tick, both Python
and JavaScript use round-half-away-from-zero. Every result is calculated from
the canonical beat or meter-event anchor.

For example:

```text
tick = beat_start_tick + round_half_away_from_zero(
    subdivision * ticks_per_beat / subdivisions_per_beat
)
```

Repeatedly adding a rounded subdivision width is invalid because it can drift
away from the canonical beat boundary. The same rule applies to calculated bar
boundaries.

A grid with `subdivisions_per_beat > ticks_per_beat` is invalid because two
subdivisions can resolve to the same integer tick. It must be rejected or
limited to `ticks_per_beat`; it must not silently create duplicate boundaries.

### Interval convention

All intervals are half-open.

The discrete interval forms are:

```text
[start_tick, end_tick_exclusive)
[start_sample, end_sample_exclusive)
[start_frame, end_frame_exclusive)
```

Therefore:

```text
frame_count = end_frame_exclusive - start_frame
```

Adjacent intervals share a boundary without overlapping. Empty intervals have
equal start and exclusive-end values. Audio integration may expand an empty
requested sample range to one available source sample under its own non-empty
audio contract.

### Ordering and monotonicity

Valid canonical musical positions are ordered by their resolved tick. For valid
positions `p1 < p2`:

```text
position_to_tick(p1) < position_to_tick(p2)
```

Tempo maps must be monotonic: later ticks do not resolve to earlier seconds,
and later seconds do not resolve to earlier ticks.

### Boundary ownership

Musical timing produces requested real-time and video-frame metadata. The
audio integration layer converts requested seconds to `sample_index` values,
clamps them to the decoded track, guarantees its non-empty-audio behavior, and
recalculates returned times and video-frame metadata from the selected sample
indices.

Video-frame rounding never changes sample boundaries.

## TempoMap contract

A timing profile supplies the following source-independent resolver surface:

```text
tick_to_seconds(tick)                         -> float
seconds_to_tick(seconds)                     -> float
bar_to_tick(bar)                             -> int
tick_to_position(tick, spb)                  -> (bar, beat, subdivision)
position_to_tick(bar, beat, subdivision, spb)-> int
meter_at_bar(bar)                            -> (numerator, denominator)
sections()                                   -> tuple[Section, ...]
```

`spb` means `subdivisions_per_beat`. Bar and beat indices are 1-based;
subdivision indices are 0-based. Event ticks and resolved grid-boundary ticks
are integers. `seconds_to_tick()` returns a floating-point query result so its
caller can apply the contract's rounding rule at the boundary that requires an
integer tick.

`tick_to_seconds()` and `seconds_to_tick()` operate in Score-relative time:
tick 0 corresponds to Score second 0. Audio alignment is carried separately by
`ResolvedScore.audio_seconds_at_tick_zero`; it is not embedded in a `Score` or
tempo map.

Both `ConstantTempoMap` and `ScoreTempoMap` satisfy this contract.
`ConstantTempoMap` presents the uniform grid described by the compatibility
profile below. `ScoreTempoMap` resolves piecewise tempo and meter events from a
`Score`.

## ConstantTempoMap compatibility profile

`ConstantTempoMap` is the currently implemented runtime profile. The profile
retains the complete version 0.1 behavior below. Expressing this behavior as a
timing profile must not change any existing input validation, calculation,
rounding, overflow behavior, or result produced by the pure timing core.

`ConstantTempoMap` retains the existing BPM and tempo-unit floating-point
calculations. It must not obtain its timing by first quantizing the
configuration to an integer MIDI `us_per_quarter` value.

The original configured `bpm` and `tempo_unit` remain the timing authority,
and current expected values are calculated directly from that configuration.
`TempoEvent.us_per_quarter: int` remains the MIDI and `ScoreTempoMap` event
contract. Constructing or serializing a future synthetic constant Score must
not feed a quantized integer tempo value back into `ConstantTempoMap`. The
version 0.1 numeric results must remain unchanged.

### Tempo configuration

A BPM value is interpreted together with a tempo note unit.

The compatibility profile supports:

| tempo_unit | Length in quarter notes |
|---|---:|
| Quarter | 1.0 |
| Eighth | 0.5 |
| Dotted Quarter | 1.5 |

Default:

```text
tempo_unit = Quarter
```

The tempo unit defines which note value occurs `bpm` times per minute.

### Real-number input validation

`bpm`, `fps`, and `downbeat_offset` must be built-in Python integer or
floating-point values. Boolean values are not accepted, and strings or other
types are not silently coerced. All three values must be finite; NaN and
positive or negative infinity are invalid. `bpm` and `fps` must be greater
than zero. `downbeat_offset` may be negative, zero, or positive.

### Time signature

The time signature is represented by:

```text
beats_per_bar
beat_unit
```

Examples:

```text
4/4:
beats_per_bar = 4
beat_unit = 4

6/8:
beats_per_bar = 6
beat_unit = 8
```

`beat_unit` defines the note value represented by one positional beat.

### Base calculations

```text
seconds_per_tempo_pulse =
    60.0 / bpm
```

Tempo-unit lengths in quarter notes:

```text
Quarter        = 1.0
Eighth         = 0.5
Dotted Quarter = 1.5

seconds_per_quarter =
    seconds_per_tempo_pulse
    / tempo_unit_in_quarters

seconds_per_beat =
    seconds_per_quarter
    * (4.0 / beat_unit)

seconds_per_bar =
    seconds_per_beat
    * beats_per_bar
```

`seconds_per_beat` always describes one positional beat according to
`beat_unit`.

Examples:

- In 4/4, one positional beat is a quarter note.
- In 6/8, one positional beat is an eighth note.
- In 3/2, one positional beat is a half note.

### Downbeat alignment

`downbeat_offset` defines the absolute audio time of:

```text
Bar 1 / Beat 1 / Subdivision 0
```

The offset is measured in seconds.

`downbeat_offset` may be negative. This can produce a negative start time and
negative frame index in the pure timing core. A later audio integration layer
must clamp calculated sample boundaries to the available waveform range.

For the Score-compatible alignment contract:

```text
audio_seconds_at_tick_zero = downbeat_offset
audio_seconds = tick_to_seconds(tick) + audio_seconds_at_tick_zero
score_seconds = audio_seconds - audio_seconds_at_tick_zero
```

`audio_seconds_at_tick_zero == downbeat_offset`

### Musical start position

Index rules:

```text
start_bar          is 1-based
start_beat         is 1-based
start_subdivision  is 0-based
```

The following musical count and index inputs must be built-in Python integer
values:

```text
beats_per_bar
beat_unit
subdivisions_per_beat
start_bar
start_beat
start_subdivision
duration_bars
duration_beats
duration_subdivisions
```

Floating-point values such as `1.0` and boolean values such as `True` are not
accepted as integers.

`start_subdivision` may be greater than or equal to
`subdivisions_per_beat`. The pure calculation core applies nonnegative
overflow arithmetically without normalizing or rejecting it. Only
`start_beat` is restricted to the range from 1 through `beats_per_bar`. A
later UI may enforce canonical ranges.

The number of beats between Bar 1 / Beat 1 and the selected start position is:

```text
start_beats =
    (start_bar - 1) * beats_per_bar
    + (start_beat - 1)
    + start_subdivision / subdivisions_per_beat
```

The absolute start time is:

```text
start_seconds =
    downbeat_offset
    + start_beats * seconds_per_beat
```

### Musical duration

Duration fields are quantities and therefore start at zero:

```text
duration_bars
duration_beats
duration_subdivisions
```

`duration_beats` may exceed `beats_per_bar`, and `duration_subdivisions` may
be greater than or equal to `subdivisions_per_beat`. The pure calculation
core applies these nonnegative quantities arithmetically without upper-range
restrictions or normalization. A later UI may enforce canonical ranges.

```text
duration_beats_total =
    duration_bars * beats_per_bar
    + duration_beats
    + duration_subdivisions / subdivisions_per_beat

duration_seconds =
    duration_beats_total
    * seconds_per_beat

end_seconds =
    start_seconds
    + duration_seconds
```

### Video-frame metadata

Frame calculations use the configured `fps` value.

```text
frames_per_beat =
    fps * seconds_per_beat

frames_per_bar =
    fps * seconds_per_bar
```

`frames_per_beat` and `frames_per_bar` remain floating-point values. They
must not be truncated to integers.

```text
start_frame =
    round_half_away_from_zero(start_seconds * fps)

frame_count =
    round_half_away_from_zero(duration_seconds * fps)
```

`round_half_away_from_zero` rounds to the nearest integer. Exact half ties
round away from zero:

```text
0.5  ->  1
1.5  ->  2
2.5  ->  3
-0.5 -> -1
-1.5 -> -2
```

Frame rounding is applied only when producing discrete frame indices or frame
counts.

### Audio sample boundary ownership

Audio trimming remains sample-based.

The timing model produces time boundaries in seconds. Conversion to clamped,
non-empty audio sample ranges is defined by the
[Audio Integration Contract](AUDIO_INTEGRATION_SPEC.md). The audio integration
layer owns sample conversion, clamping, exclusive-end slicing, and
returned-range recalculation. Video-frame rounding never determines audio
sample boundaries.

### Golden test

Input:

```text
bpm = 180
tempo_unit = Quarter
beats_per_bar = 4
beat_unit = 4
fps = 24
downbeat_offset = 0
duration_bars = 4
```

Expected results:

```text
seconds_per_tempo_pulse = 0.333333333333...
seconds_per_quarter     = 0.333333333333...
seconds_per_beat        = 0.333333333333...
seconds_per_bar         = 1.333333333333...

frames_per_beat = 8.0
frames_per_bar  = 32.0

duration_seconds = 5.333333333333...
frame_count      = 128
```

### Fractional-frame test

Input:

```text
bpm = 174
tempo_unit = Quarter
beats_per_bar = 4
beat_unit = 4
fps = 24
downbeat_offset = 0
```

Expected values include:

```text
seconds_per_beat =
    60 / 174
    = 0.344827586206...

frames_per_beat =
    24 * 60 / 174
    = 8.275862068965...
```

`frames_per_beat` must remain fractional.

Rounding is applied only to discrete frame indices and frame counts.

### Compound-meter example

Input:

```text
bpm = 90
tempo_unit = Dotted Quarter
beats_per_bar = 6
beat_unit = 8
```

Expected results:

```text
seconds_per_tempo_pulse = 0.666666666667
seconds_per_quarter     = 0.444444444444
seconds_per_beat        = 0.222222222222
seconds_per_bar         = 1.333333333333
```

A 6/8 bar therefore contains two dotted-quarter tempo pulses.

## Canonical Score positions

A canonical Score position is `(bar, beat, subdivision)` under a requested
`subdivisions_per_beat` value:

- `bar` is 1-based.
- `beat` is 1-based within the effective meter at that bar.
- `subdivision` is 0-based and less than `subdivisions_per_beat`.
- `subdivisions_per_beat` is a positive integer and must satisfy the
  over-fine-grid rule in Shared invariants.

Bar 1 / Beat 1 / Subdivision 0 is tick 0. A canonical position resolves from
the bar's precomputed start tick and the effective meter:

```text
ticks_per_beat = ticks_per_quarter * 4 / denominator

beat_start_tick =
    bar_start_tick
    + round_half_away_from_zero((beat - 1) * ticks_per_beat)

position_tick =
    beat_start_tick
    + round_half_away_from_zero(
        subdivision * ticks_per_beat / subdivisions_per_beat
    )
```

Every calculation is anchored absolutely to the canonical bar or beat start.
It must not repeatedly add rounded beat or subdivision widths.

A meter change inside a bar ends the current bar at the event tick and creates
a shortened bar. The event remains at its exact tick, the next bar begins there
under the new meter, and a diagnostic records the partial bar. Neither meter
events nor positions are silently moved.

`bar_starts` is not serialized.

It is a resolver cache of integer bar-start ticks derived from meter events and
an extrapolation target. `bar_to_tick()` is O(1). `tick_to_position()` locates
the containing bar with a binary search and is O(log n).

The version 0.1 `ConstantTempoMap` start and duration fields retain their
documented nonnegative arithmetic overflow behavior. Canonical Score-position
APIs use canonical ranges instead. This distinction must not be used to change
the compatibility profile's accepted inputs.

## Complete Score selection

This section defines the request contract whenever the complete TempoMap
planning profile owns a resolved Score selection. It is independent of any
implementation roadmap or delivery step.

### Exact exclusive end position

A complete Score request has these three internal fields in addition to its
canonical start position:

```text
score_end_bar
score_end_beat
score_end_subdivision
```

They describe the exclusive musical end position. Their one canonical unset
value is exactly:

```text
score_end_bar         = 0
score_end_beat        = 0
score_end_subdivision = 0
```

Bar zero is outside the canonical 1-based Score domain and therefore cannot
collide with a valid position. The unset state is all-or-nothing. A zero end
bar with a nonzero beat or subdivision, a positive end bar with an end beat
below one, or any other incomplete sentinel combination is invalid.

An exact end is present only when `score_end_bar` is positive and all three
fields form one canonical Score position under the active
`subdivisions_per_beat` grid. Invalid exact-end fields are not normalized,
clamped, or replaced with legacy duration values.

When present, the exact end has priority over `duration_bars`,
`duration_beats`, and `duration_subdivisions`:

```text
start_tick = position_to_tick(
    start_bar,
    start_beat,
    start_subdivision,
    subdivisions_per_beat,
)

end_tick_exclusive = position_to_tick(
    score_end_bar,
    score_end_beat,
    score_end_subdivision,
    subdivisions_per_beat,
)
```

This exact-position path supports every valid Score shape: one or multiple
tempo events, constant or changing meter, meter changes inside a bar, and
unusual meters. Both boundaries are canonical ticks; tempo conversion happens
only after the range is valid.

### Meter-stable duration fallback

When the exact end is canonically unset, the nonnegative legacy duration
quantities may determine `end_tick_exclusive` only when the requested
half-open interval is meter-stable. This rule is independent of
`ScoreTempoMap.supports_uniform_timing`. That bridge predicate also requires
exactly one tempo event and is intentionally narrower than the question of
whether a duration request is unambiguous.

Resolve the fallback in this order:

1. Resolve the canonical requested start position to `start_tick`.
2. Obtain the effective meter signature `(numerator, denominator)` at that
   start.
3. Interpret `duration_bars`, `duration_beats`, and
   `duration_subdivisions` as nonnegative quantities under that meter. They
   retain their arithmetic overflow behavior.
4. Combine the canonical start's subdivision index within its bar with the
   complete duration quantity. Resolve the resulting provisional end from the
   canonical start-bar and containing-beat anchors with the absolute
   round-half-away-from-zero grid rule; do not accumulate rounded beat or
   subdivision widths. The result is `candidate_end_tick`.
5. Verify that the effective meter signature remains unchanged throughout
   `[start_tick, candidate_end_tick)`.
6. Only after that verification set
   `end_tick_exclusive = candidate_end_tick`.
7. Convert the accepted start and exclusive-end ticks to seconds through the
   complete piecewise tempo map.

The tick-domain quantity in step 4 has the same linear overflow meaning as the
legacy duration fields under the meter effective at the start. Let
`start_bar_tick` be the exact canonical start of `start_bar`:

```text
duration_subdivision_count =
    (duration_bars * numerator + duration_beats)
    * subdivisions_per_beat
    + duration_subdivisions

provisional_end_subdivision_index =
    ((start_beat - 1) * subdivisions_per_beat + start_subdivision)
    + duration_subdivision_count

provisional_end_beat_index, provisional_end_subdivision =
    divmod(provisional_end_subdivision_index, subdivisions_per_beat)

candidate_end_tick =
    start_bar_tick
    + round_half_away_from_zero(
        provisional_end_beat_index
        * ticks_per_quarter * 4
        / denominator
    )
    + round_half_away_from_zero(
        provisional_end_subdivision
        * ticks_per_quarter * 4
        / (denominator * subdivisions_per_beat)
    )
```

The provisional beat index may overflow the nominal bar; that is the defined
quantity behavior. Each rounded boundary is calculated absolutely from its
canonical bar or beat anchor, never by repeated addition of a rounded width.

Any number of tempo changes may occur inside a meter-stable interval. Tempo
changes never make bar-count duration ambiguous, and accepted tick boundaries
are converted to seconds by integrating every intersected tempo segment. The
calculation must not use one global `seconds_per_beat` or
`seconds_per_bar`.

A meter change strictly before `candidate_end_tick` makes the duration
fallback invalid and requires an exact end position. A meter change exactly at
`candidate_end_tick` is outside the half-open interval and is allowed. A
zero-length interval is valid and meter-stable. A Score that is variable in
general may therefore use this fallback when the requested interval lies
wholly within one meter-stable region.

This fallback validates one request; it is not a Score activation test. Under
the complete TempoMap planning profile, a resolved Score remains the timing
authority whether the request uses an exact end or the meter-stable duration
fallback. A duration request that crosses a meter change without an exact end
is a fatal request error and must not select `ConstantTempoMap`.

### Complete Score timing authority

When the complete TempoMap planning profile is active, every valid resolved
Score is the timing authority for both uniform and nonuniform tempo and meter
shapes. Score shape must not cause a `ConstantTempoMap` fallback, mixed
Score/constant planning, display-only timing, or partial Score activation.

A concrete request can still be invalid because it lacks enough information
to identify an unambiguous range. That is a request error, not deactivation of
the Score. The delivered node's smaller uniform bridge is an implementation
limitation described in Status; it is not this complete-planning contract.

### Range validation and fatal diagnostics

The requested Score interval is half-open. These range relations are exact:

```text
end_tick_exclusive == start_tick  -> valid empty requested interval
end_tick_exclusive <  start_tick  -> invalid requested interval
```

The audio integration layer may apply its existing non-empty returned-audio
rule after accepting an empty requested interval. It must not reinterpret an
end before the start.

Complete Score selection adds these diagnostic codes without changing the
existing provider and uniform-bridge diagnostic codes:

```text
score_end_position_required
score_selection_range_invalid
```

Both have severity `error`.

`score_end_position_required` applies when no exact end is present and the
duration fallback would cross a meter change within the requested half-open
interval.

`score_selection_range_invalid` applies to a mixed or malformed unset/end
state, a noncanonical exact end, an invalid bar, beat, subdivision, or grid,
an end before the start, or another structurally invalid exact Score range.

Either error terminates node execution deterministically:

- Raise `ValueError`.
- The exception text is compact diagnostic JSON.
- The JSON is an array containing exactly one entry.
- The entry keys are inserted in the order `code`, `severity`, `message`.
- Do not call the clip planner.
- Do not slice a waveform or return node outputs.
- Do not fall back to `ConstantTempoMap`.
- Do not substitute the one-second silence fallback.

The one-second silence fallback remains exclusively an audio-availability and
decode-recovery behavior under the Audio Integration Contract.

## Score data contract

The Score contract separates canonical musical data from the provider that
found it. The language-neutral model is represented here with Python-like type
notation:

```python
ScoreFormat = Literal["json", "midi", "analyzed", "constant"]
ProviderKind = Literal[
    "explicit",
    "json_sidecar",
    "midi_sidecar",
    "analysis",
    "constant",
]

@dataclass(frozen=True)
class TempoEvent:
    tick: int
    us_per_quarter: int

@dataclass(frozen=True)
class MeterEvent:
    tick: int
    numerator: int
    denominator: int

@dataclass(frozen=True)
class Marker:
    tick: int
    name: str

@dataclass(frozen=True)
class Section:
    name: str
    start_tick: int
    end_tick_exclusive: int
    bar_aligned: bool
    confidence: float | None = None

@dataclass(frozen=True)
class Score:
    ticks_per_quarter: int
    tempos: tuple[TempoEvent, ...]
    meters: tuple[MeterEvent, ...]
    markers: tuple[Marker, ...]
    sections: tuple[Section, ...]
    source: ScoreFormat
    meter_estimated: bool = False
    has_variable_meter: bool = False
    has_midbar_meter_change: bool = False

@dataclass(frozen=True)
class ResolvedScore:
    score: Score
    audio_seconds_at_tick_zero: float
    provider: ProviderKind
```

The fields have these binding meanings:

Sections are canonical; markers are raw data.

- `ticks_per_quarter` is a positive integer. Synthetic Scores use 960; MIDI
  Scores use the file's PPQ value.
- Tempo, meter, marker, and Section boundaries use integer ticks.
- `markers` are raw point data from the source. They are not snapped.
- `sections` are canonical, editable, serializable annotations. Their
  intervals are half-open.
- `start_bar`, `end_bar`, and `bar_count` are derived display values and are
  not stored on `Section`.
- `bar_starts` is a resolver cache and is not stored on `Score`.
- `source` describes the Score's format. `provider` describes how the Score
  was selected. For example, `source="midi", provider="explicit"` is valid.
- `confidence=None` means that confidence does not apply because the
  annotation was specified rather than estimated.
- `meter_estimated` records that meter was inferred rather than read.
- `has_variable_meter` is derived from effective meter changes, not merely
  from the number of meter events.
- `has_midbar_meter_change` records that at least one meter event shortened a
  bar.

Two effective 4/4 events at different ticks do not by themselves make
`has_variable_meter` true. Duplicate events and a genuine change are resolved
under `ScoreTempoMap` semantics.

Score and audio remain separate. A `Score` contains no samples, waveform
state, sample rate, decoded duration, or audio filename. Alignment belongs to
`ResolvedScore`, allowing one Score to be used with differently aligned audio
exports.

## ScoreTempoMap semantics

`ScoreTempoMap` resolves the canonical Score event lists into the shared
`TempoMap` contract. This section defines the delivered resolver mathematics;
the Status section separately identifies which planning profile the node
currently activates.

### Effective events and defaults

The resolver establishes effective event streams before answering queries:

| Case | Rule |
|---|---|
| No initial tempo | Use 120 BPM at tick 0, the MIDI default |
| No initial meter | Use 4/4 at tick 0 |
| Multiple events at the same tick | The last event wins |
| Events from multiple tracks | Merge stably by `(tick, track_index, event_index)` |
| SMPTE division instead of PPQ | Reject as `invalid` with a diagnostic |

An effective event applies from its tick up to, but not including, the next
event of the same kind.

### Tick and seconds conversion

For a tempo segment with `us_per_quarter` microseconds per quarter note:

```text
segment_seconds =
    segment_ticks * us_per_quarter
    / (ticks_per_quarter * 1_000_000)
```

`tick_to_seconds()` integrates every intersected tempo segment piecewise.
It must not multiply by one global factor when tempo changes occur inside a
bar or elsewhere. `seconds_to_tick()` performs the inverse piecewise lookup
and returns a floating-point tick query.

Tick-to-seconds and seconds-to-tick conversion must remain monotonic. At an
event boundary, both directions resolve the same boundary without a gap or
overlap.

### Meter resolution and bars

The resolver builds `bar_starts` from effective meter events. A meter
change on a bar boundary starts the next bar under the new meter. A meter
change inside a bar terminates that bar at the exact event tick, begins the
next bar there under the new meter, sets `has_midbar_meter_change`, and emits
a diagnostic.

Meter events and markers are never snapped to bar boundaries. A marker's
derived display bar is the bar containing its exact tick. `bar_aligned`
exposes whether a Section start lies exactly on a calculated bar boundary.

### Extrapolation

Tempo and meter data commonly end before the associated audio. This is a
normal condition.

When a resolver is created, it precomputes `bar_starts` through the
audio-duration target. Beyond the final event, it continues arithmetically
using the most recently valid tempo and meter. Queries beyond the precomputed
target continue under the same most-recent-event rule; they do not fail merely
because the source event list ended.

Extrapolated bar boundaries use absolute calculation from the final canonical
meter-event anchor. They do not accumulate rounded bar widths.

### Audio alignment

Score-relative seconds and audio seconds are related only through
`ResolvedScore.audio_seconds_at_tick_zero`:

```text
audio_seconds =
    score.tick_to_seconds(tick)
    + audio_seconds_at_tick_zero

score_seconds =
    audio_seconds
    - audio_seconds_at_tick_zero
```

The existing `downbeat_offset` has exactly this sign and role:

```text
audio_seconds_at_tick_zero = downbeat_offset
```

Negative alignment values are valid. Pre-roll, count-in, and differently
aligned exports do not require a second alignment convention.

### Sections and intervals

Markers remain exact source points. Sections are canonical half-open
intervals `[start_tick, end_tick_exclusive)`. Two markers at the same tick,
empty marker names, markers before the first bar, markers at track end,
zero-length Sections, and unaligned Sections must be represented or rejected
according to explicit validation; they must never be silently snapped or
renamed by timing resolution.

Section display bar values are derived through the resolver. The timing model
does not store a potentially misleading bar count on a Section that begins or
ends inside a bar.

### Delivered uniform-bridge limitation

The delivered node currently activates Score timing only through the smaller
uniform bridge. Its runtime gate requires constant, bar-aligned meter and one
tempo event; Scores outside that gate remain display-only in the delivered
implementation. There is no partially active path within that bridge.

This subsection records implementation status rather than a normative limit
on complete `ScoreTempoMap` mathematics. Under the complete TempoMap planning
profile, the Complete Score selection rules apply instead: a valid resolved
Score remains the sole timing authority for every valid tempo and meter shape.

## Provider resolution

Score selection is an ordered provider chain:

```text
ExplicitFileProvider
SidecarJsonProvider
MidiSidecarProvider
AnalysisProvider
ConstantProvider
```

The first provider that returns a found Score wins. `ConstantProvider` is the
terminator and always supplies the legitimate constant-tempo fallback, so a
successful resolution never needs a second empty-Score error path.

Providers return three states:

```python
@dataclass(frozen=True)
class ProviderResult:
    status: Literal["not_applicable", "found", "invalid"]
    score: Score | None
    diagnostics: tuple[str, ...]
    provenance: Mapping[str, str]
```

Resolution rules:

- `not_applicable` continues to the next provider.
- `found` stops the chain and produces a `ResolvedScore`.
- `invalid` stops the chain with an error and diagnostics. It never silently
  falls through to another provider.

A missing optional sidecar is `not_applicable`. A present but malformed file
is `invalid`.

An explicitly selected invalid Score file must never silently fall back to the
constant timing profile.

Provider conventions are:

- `ExplicitFileProvider` reads the optional `score_file`; the selected file
  may contain JSON or MIDI, so provider and format remain separate.
- `SidecarJsonProvider` considers `<audio-name>.score.json`.
- `MidiSidecarProvider` considers `<audio-name>.mid`.
- `AnalysisProvider` is a reserved provider slot. This specification does not
  claim that analysis is implemented and does not define an analyzer
  algorithm.
- `ConstantProvider` constructs the `ConstantTempoMap` profile from BPM,
  tempo unit, time signature, and alignment inputs.

Provider diagnostics and provenance describe the resolution result. They do
not alter musical-position text or timing calculations.

## Cross-language parity

Python and JavaScript share a golden corpus.

Python and JavaScript implementations of the Score resolver must use the same
mathematics, deterministic rounding, effective-event rules, and fixture data.
Neither implementation is a looser display approximation of the other.

The shared golden corpus covers at least:

```text
tests/fixtures/scores/
    constant_4_4.json
    changing_meter.json
    midbar_tempo.json
    midbar_meter.json
    odd_meter_31_32.json
    unaligned_markers.json
    truncated_tempo_track.json
```

Both languages verify Tick↔seconds, Bar↔Tick, Position↔Tick, snapping to
bar/beat/subdivision, extrapolation, alignment, interval boundaries, and
video-frame rounding.

The required invariants are directional:

```text
tick_to_position(position_to_tick(p)) == p
    for every valid canonical position p

position_to_tick(tick_to_position(x))
    is the nearest grid boundary under deterministic rounding;
    it is not necessarily the original arbitrary tick x

tick_to_seconds(seconds_to_tick(x)) ≈ x

p1 < p2 implies position_to_tick(p1) < position_to_tick(p2)

Python and JavaScript produce identical discrete boundaries
for the same fixture
```

The position-originating round trip is lossless. An arbitrary tick between
grid points is quantized when routed through a musical position; requiring
that round trip to reproduce the arbitrary tick would reject correct
behavior.

Floating-point seconds comparisons use an explicitly shared tolerance.
Integer tick and video-frame boundaries must match exactly. Sample-index
resolution remains governed by the Audio Integration Contract.
Deterministically generated tempo and meter fixtures may supplement the golden
corpus without introducing a property-testing dependency.

## Current implementation state and exclusions

The current runtime implements the `ConstantTempoMap` compatibility profile,
the Score model and serialization core, MIDI parsing, provider resolution,
`ScoreResolver`, `ScoreTempoMap`, Score Sections, and uniform Score routing
through the audio node. The node currently uses the smaller uniform bridge;
complete nonuniform Score selection and start-anchored local timing metrics
described above are not yet implemented.

The current runtime also does not include:

- automatic BPM detection
- automatic downbeat detection
- complete nonuniform Score planning in the audio node
- frontend editing against changing or inside-bar meter
- swing timing
- tuplets beyond equal subdivisions
- external music-analysis dependencies

These are statements about current implementation availability, not removals
from the version 0.2 normative contract. The complete TempoMap planning
profile remains specified here so its implementation can be evaluated against
one stable contract.

This specification does not define audio decoding, waveform clamping, source
sample selection, or node output compatibility. Those remain under the
[Audio Integration Contract](AUDIO_INTEGRATION_SPEC.md). It also does not add
runtime behavior, dependencies, build configuration, or UI behavior.
