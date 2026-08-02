# Score Phase 2 Design

> **Status:** binding implementation design for Phase 2.
> **Revision:** 1 — chapters through the Phase 2b acceptance criteria are
> settled, except for the explicitly open Phase 2c resolver material.

## Status and normative precedence

Three documents, clear precedence:

| Document | Role |
|---|---|
| `docs/SCORE_SUBSYSTEM.md` | frozen overall architecture, S1–S10 |
| `docs/MUSICAL_TIMING_SPEC.md` | normative timing contract v0.2 |
| `docs/SCORE_PHASE2_DESIGN.md` | this document: implementation design |

This document **explains and refines**; it changes nothing. Where it
contradicts the timing contract or S1–S10, the contract wins and the
contradiction is a defect here, not an amendment.

It is deliberately not a fourth normative document. It describes *how* Phase 2
implements the existing contract, and it is allowed to go stale once the phase
is complete.

## Scope and non-goals

Phase 2 builds the pure Score core: parser, canonical form, bar geometry,
resolver, serialization. Fully tested, with no integration.

The implementation is divided at the boundary between settled and open
contracts:

- **Phase 2a:** `model.py`, `bars.py`, `normalize.py`, and `midi_parse.py`.
- **Phase 2b:** `serialize.py`, the JSON and sidecar schema, Score and sidecar
  round trips, and canonical JSON fixture production.
- **Phase 2c:** `resolver.py`, tempo segments, tick/seconds queries, the
  audio-duration-dependent `BarGrid`, position queries, and extrapolation.

This division follows the design boundary between settled and open contracts.
It is not a weakening of Phase 2 acceptance.

**Phase 2 does not touch the file system.** Every function takes `bytes`,
`str`, or objects and returns objects. No `open()`, no `Path`, no network, no
ComfyUI import, no third-party dependency.

Explicitly **not** in Phase 2:

- the `TempoMap` protocol and `ConstantTempoMap` as runtime adapters
- an optional `tempo_map` parameter in `musical_timing.py`
- changes to `audio_clip_plan.py`
- the provider chain, discovery, sidecar lookup
- node integration, outputs, routes, frontend
- `score/naming.py` — without a consumer that would be Phase 6 work pulled
  forward

Phase 3 wraps the shared `TempoMap` protocol around the by-then tested
resolver and demonstrates that the existing constant path is unchanged.

## Package and dependency structure

```text
score/
  __init__.py     empty
  model.py        dataclasses and public types
  bars.py         meter geometry and BarGrid
  normalize.py    canonical form and section policy
  resolver.py     tempo and position queries, audio-dependent cache
  midi_parse.py   Standard MIDI bytes to raw events
  serialize.py    Score and sidecar to and from dict
```

Dependencies are acyclic; `model` is the leaf:

```text
        model
          ^
        bars
          ^
      normalize
       ^     ^
midi_parse  serialize

    model + bars
          ^
      resolver
```

`score/__init__.py` stays **empty**. Re-exports read conveniently but pull the
whole package in on any partial import; the repository's root `__init__.py`
already registers routes at import time, so import order is not a theoretical
concern here. Phase 2 has no external consumer that would need the
convenience.

**Why `model.py` does not contain the resolver.** `bar_starts` has a lifetime
— it is built up to an audio-dependent target. That is state with context,
whereas `Score` is frozen canonical data with no knowledge of any audio. In
addition, the resolver is what `js/score.js` mirrors under S10, and one file
per side is easier to keep at parity than "the part of `model.py` that also
exists over there".

**Why `normalize.py` is required.** Without this layer, `midi_parse`,
`serialize`, and later `ConstantProvider` would each construct a `Score`
themselves — three places deciding which simultaneous event wins, when
defaults are inserted, which events are effectively meaningless, and how the
two meter flags are computed. That is exactly the drift the contract exists to
prevent.

## Canonical construction pipeline

A `Score` is created only through this chain:

```text
raw events
    -> normalize_events()
    -> build_bar_grid()
    -> derive_sections()        MIDI path only
    -> finalize_score()
```

```python
def normalize_events(
    *,
    ticks_per_quarter: int,
    tempos: Sequence[OrderedTempoEvent],
    meters: Sequence[OrderedMeterEvent],
    markers: Sequence[Marker],
) -> NormalizedEvents: ...

def derive_sections(
    *,
    markers: Sequence[Marker],
    end_tick_exclusive: int,
    bar_grid: BarGrid,
) -> tuple[Section, ...]: ...

def finalize_score(
    *,
    events: NormalizedEvents,
    sections: Sequence[Section],
    source: ScoreFormat,
    meter_estimated: bool,
    bar_grid: BarGrid,
) -> Score: ...
```

`finalize_score()` does **not accept** the two derived meter flags as input.
They are produced solely from the supplied `BarGrid`, which in turn derives
only from the normalized meter events. An inconsistent `Score` is therefore
structurally impossible to construct.

The MIDI path calls `derive_sections()` **explicitly**. The JSON path passes
its already canonical sections straight to `finalize_score()`. There is no
`sections=None` and no implicit derivation — otherwise the JSON path would
eventually reconstruct sections from markers and overwrite hand edits to the
sidecar.

## Canonical ordering and normalization

```text
TempoEvents
    same tick: last one wins
    sorted by tick
    consecutive identical effective values removed

MeterEvents
    same tick: last one wins
    sorted by tick
    consecutive identical effective values removed

Markers
    sorted by (tick, name)
    no deduplication

Sections
    sorted by (start_tick, end_tick_exclusive, name)
    no deduplication
```

Step order, not interchangeable:

```text
raw events
-> stable sort by (tick, track_index, event_index)
-> same-tick last-event-wins
-> insert defaults (120 BPM, 4/4 at tick 0)
-> remove effectively identical consecutive events
-> build BarGrid
-> set flags
```

Removal of identical consecutive events applies to **tempo as well as meter**.
For meter the reason is obvious: a redundant 4/4 event inside a 4/4 bar must
not produce an artificial short bar or set `has_midbar_meter_change`. For
tempo the reason differs but is no weaker: two identical tempo segments are
mathematically harmless, yet they break `Score.__eq__` and with it the
round-trip test.

**Python owns the canonical ordering.** String ordering is language dependent
— Python compares by code point, JavaScript's `sort()` by UTF-16 code units.
For characters outside the BMP, such as emoji in marker names, this yields a
different order. Binding rule:

> JavaScript adopts the serialized order unchanged and never re-sorts markers
> or sections by name.

## Bar-grid construction

```python
def build_bar_grid(
    *,
    ticks_per_quarter: int,
    meters: Sequence[MeterEvent],
    through_tick: int,
) -> BarGrid: ...

@dataclass(frozen=True)
class BarGrid:
    boundaries: tuple[int, ...]          # n bars -> n+1 boundaries
    meters_by_bar: tuple[MeterEvent, ...]
    midbar_change_ticks: tuple[int, ...]
    diagnostics: tuple[str, ...]
    has_variable_meter: bool
    has_midbar_meter_change: bool
```

`boundaries` are **boundaries, not starts**: `boundaries[i]` is the start of
bar `i+1` and `boundaries[i+1]` is that bar's exclusive end. The entry past
`through_tick` is therefore not a sentinel special case but the same
half-openness as S5, and binary search resolves unambiguously at the far edge.

`through_tick` means: the grid contains the bar in which this tick falls, and
therefore that bar's exclusive end.

**Bar lengths are computed absolutely, never accumulated** (S2). From the
anchor of the most recently effective meter event:

```python
boundary_k = anchor_tick + round_half_away_from_zero(
    k * numerator * 4 * ticks_per_quarter / denominator
)
```

Summing rounded bar lengths drifts even with integer ticks. The case is real:
`4 * TPQ / denominator` is not always integral — with TPQ 100 and denominator
32 it is 12.5.

A **mid-bar meter change** terminates the running bar at the exact event tick,
starts the next bar there under the new meter, records the tick in
`midbar_change_ticks`, sets `has_midbar_meter_change`, and emits a diagnostic.
Nothing is snapped.

`has_variable_meter` derives from the **effective distinct** signatures, not
from `len(meters)`. Two effective 4/4 events at different ticks are not a
meter change.

**Flag invariance.** The grid is built in two places with different
`through_tick` values — statically at Score construction, longer at resolver
construction. If a flag differed between them, the resolver would contradict
the `Score`. The implementation should make the guarantee structural: flags
derive only from the normalized meter sequence, extrapolated regular
boundaries never change them, and `through_tick` affects only table length.

As a generated test over several random `t`:

```text
for every t >= max_effective_meter_tick:
    build_bar_grid(..., through_tick=t).has_variable_meter
        == score.has_variable_meter
    build_bar_grid(..., through_tick=t).has_midbar_meter_change
        == score.has_midbar_meter_change
```

## MIDI parser contract

No `mido`. The repository currently has neither `requirements.txt` nor
`pyproject.toml`, so zero third-party dependencies — and for four meta events
plus end of track the trade is poor. `mido` would absorb the container format
and running status, but it brings a large message model, replaces neither the
normalization nor the stable merge order `(tick, track_index, event_index)`,
and would re-wrap exactly the rawness we need.

`midi_parse.py` is not a MIDI toolkit but a strict, stdlib-only SMF reader.

### Accepted

SMF format 0 and 1, PPQ division, multiple tracks, variable-length quantities,
running status for channel messages, safely skipped SysEx blocks, safely
skipped unknown meta events.

### Extracted

```text
0x51  Set Tempo
0x58  Time Signature
0x06  Marker
0x2F  End of Track
```

Per event: `absolute_tick`, `track_index`, `event_index`, payload.

### Time signature: the denominator is an exponent

The meta event carries four bytes `nn dd cc bb`:

```text
nn = numerator
dd = denominator exponent   ->   denominator = 2 ** dd
cc = ignored (MIDI clocks per metronome click)
bb = ignored (32nd notes per quarter)
```

`4/4` is stored as `04 02`. Taking `dd` directly as the denominator yields
`4/2`, a bar of double length — with no error, no diagnostic, and the
checkpoints for bar 55 and bar 169 failing without the cause being anywhere
nearby.

From the real test export:

```text
04 02 18 08   ->  4 / 2^2  =  4/4
1F 05 18 08   ->  31 / 2^5 =  31/32
3F 06 18 08   ->  63 / 2^6 =  63/64
```

`dd > 6` is invalid, keeping the denominator inside the contractual range of 4
to 64. `dd = 1` remains explicitly valid because the model supports 3/2.

### Running status

```text
Running status applies only to channel messages 0x80-0xEF.

An explicit system, SysEx, or meta event clears the stored running status
before the next track event is read.
```

Without the reset, a parser continues past a SysEx block as if the previous
channel status still applied, slips in the byte stream, and produces
plausible-looking but wrong ticks. A data byte with no currently valid channel
running status is a structural error.

### Rejected

- malformed or truncated `MThd` structure
- malformed or truncated `MTrk` length
- SMPTE division instead of PPQ
- SMF format 2 — independent sequences that must not be merged onto one Score
  axis without additional policy
- invalid VLQ
- data byte with no valid status or running status
- illegal system status inside a track
- wrong length on a tempo or time-signature meta event
- `us_per_quarter <= 0`
- numerator 0, `dd > 6`
- declared and actual track count disagree
- integer or length overflow, reads past the end of a chunk

The `MTrk` chunk length is the hard byte boundary. After an end-of-track event
no further track events are accepted within the same chunk.

### End of track: diagnostic, not abort

```text
EOT present:  end_tick_exclusive = EOT tick
EOT missing:  end_tick_exclusive = tick of the last fully read event,
              plus a diagnostic
```

End of track is mandatory, but a file without it is still structurally
readable — the chunk length bounds it cleanly. Rejecting would only affect
foreign files, over a defect that stops nobody from computing. The "never
guess silently" rule still holds, because a diagnostic is not silent.

`end_tick_exclusive` is the maximum across all tracks. The name is correct
despite EOT being a point event, because this tick forms the exclusive end of
the last derived section.

### Marker text

```text
1. decode as UTF-8
2. on invalid UTF-8, decode as Latin-1
3. emit a diagnostic on the Latin-1 fallback
```

Every byte sequence is representable, modern names stay correct, and the
fallback is visible rather than silent.

### Result and errors

```python
@dataclass(frozen=True)
class ParsedMidiScore:
    score: Score
    end_tick_exclusive: int
    diagnostics: tuple[str, ...]

class MidiParseError(ValueError):
    code: str
    message: str
    byte_offset: int
    track_index: int | None
    event_index: int | None
```

`ParsedMidiScore` is an internal Phase 2 result — not a new public Score field
and not a change to the normative model. Structural errors raise; non-fatal
findings land in `diagnostics`.

### Order within the MIDI path

```text
1. read complete track chunks
2. determine end_tick_exclusive
3. normalize events
4. build the static BarGrid with
       through_tick = max(last effective meter tick, end_tick_exclusive)
5. derive sections from markers and this grid
6. finalize the Score
```

Step 4 must include `end_tick_exclusive` because `derive_sections()` needs the
grid for `bar_aligned`, and markers routinely sit well past the last meter
event. In the current test export, meter data and track end happen to fall on
the same tick; once markers are added they will not.

This is harmless because flag invariance across `through_tick` is guaranteed
independently.

## MIDI section derivation

Binding policy for the MIDI path:

- markers are sorted by `(tick, name)`
- no marker is removed or moved
- every marker starts a section
- the end is the tick of the next marker
- the last section ends at `end_tick_exclusive`
- two markers on the same tick produce a zero-length section
- a marker exactly at the track end produces a zero-length section
- an empty marker name stays empty; no automatic `Untitled`
- no markers produce no sections
- `bar_aligned` follows from membership of the start tick in
  `bar_grid.boundaries`
- every MIDI-derived Section has `confidence=None`; MIDI markers are specified
  facts rather than estimated boundaries, so neither `0.0` nor `1.0` applies
- marker names are preserved exactly after the parser's decoding policy: no
  whitespace trimming, no case conversion, no Unicode normalization, and no
  replacement of empty names; `"  Chorus  "` remains exactly `"  Chorus  "`

Markers are raw user data. Naming normalization belongs to later naming and
output policy, not Score canonicalization.

Sections need **not tile the track**. If the first marker sits past tick 0,
the region before it stays uncovered. No implicit leading section, no invented
name.

For MIDI markers, `derive_sections()` may assume:

```text
0 <= marker.tick <= end_tick_exclusive
```

Negative ticks and markers past the track end cannot be constructed from an
SMF file: delta times are unsigned VLQs, absolute ticks start at 0 and grow
monotonically, and EOT is by definition the last event of its track. Those
rules therefore belong to JSON validation, not here — in the MIDI path they
would be unreachable code that someone later mistakes for a real case.

## JSON and sidecar boundaries

**Binding for Phase 2b.** Serialization has two independently versioned
levels:

- two levels: `score_to_dict` / `score_from_dict` for the pure Score,
  `sidecar_to_dict` / `sidecar_from_dict` for the envelope holding
  `end_tick_exclusive`, `audio_seconds_at_tick_zero`, `generator`,
  `derived_from`, and `edited`
- `meter_estimated` is serialized because it is not derivable
- `has_variable_meter`, `has_midbar_meter_change`, and `bar_starts` are
  **not** serialized; they are recomputed on load
- `ResolvedScore.provider` does not belong in the file: the same file can be
  found as `explicit` or as `json_sidecar`

A pure `Score` has no global track or audio end and remains that way.
`score_from_dict()` validates intrinsic Score structure only. The sidecar
envelope carries `end_tick_exclusive`, and `sidecar_from_dict()` validates
markers and Sections against that declared end. The extent is sidecar/source
metadata, not a `Score` field. Phase 2c may later calculate an appropriate
extent from audio duration; Phase 2b only serializes and validates the supplied
value.

```python
SCORE_SCHEMA_VERSION = 1
SIDECAR_SCHEMA_VERSION = 1

@dataclass(frozen=True)
class GeneratorInfo:
    name: str
    version: str
    algorithm_version: int
    config_fingerprint: str

@dataclass(frozen=True)
class DerivedFrom:
    filename: str
    size: int
    mtime_ns: int

@dataclass(frozen=True)
class ScoreSidecar:
    score: Score
    end_tick_exclusive: int
    audio_seconds_at_tick_zero: float
    generator: GeneratorInfo
    derived_from: DerivedFrom
    edited: bool
```

The pure Score object uses this key order:

```text
schema_version, ticks_per_quarter, tempos, meters, markers, sections,
source, meter_estimated
```

Its nested objects are also exact and ordered: tempo objects use
`tick, us_per_quarter`; meter objects use
`tick, numerator, denominator`; marker objects use `tick, name`; and Section
objects use
`name, start_tick, end_tick_exclusive, bar_aligned, confidence`. Every field
is required and unknown fields are rejected. `source` is exactly one of
`json`, `midi`, `analyzed`, or `constant` and is preserved across a round
trip. Marker and Section names are preserved without trimming, case changes,
Unicode normalization, or empty-string replacement.

The sidecar object uses this key order:

```text
schema_version, score, end_tick_exclusive, audio_seconds_at_tick_zero,
generator, derived_from, edited
```

The nested `score` is the complete pure Score representation. The outer
schema version governs the envelope and the nested schema version governs the
Score independently; the duplication is intentional.

Generator objects use the exact key order
`name, version, algorithm_version, config_fingerprint`; source-provenance
objects use `filename, size, mtime_ns`. Every field is required and unknown
fields are rejected at the envelope and both metadata levels.

Deserializers accept decoded Python JSON values, not strings. Objects and
arrays must be built-in `dict` and `list` values. Required fields are exact:
missing and unknown fields are errors at every level. Integers are built-in
`int`, never `bool`. Real fields accept built-in `int` or `float`, never
`bool`, must be finite, and become built-in `float`. Confidence accepts
`None` or any finite built-in real value; no range is invented.

Schema failures raise `ScoreSerializationError`, a `ValueError` carrying
stable `code`, `path`, and `message` strings. Codes are limited to
`invalid_type`, `missing_field`, `unknown_field`,
`unsupported_schema_version`, and `invalid_value`; paths use deterministic
JSON notation rooted at `$`, and the string representation includes the path
and message. Deserializers perform no JSON string parsing and no filesystem
access.

Ticks and sidecar extents are nonnegative. TPQ, tempo values, and meter
numerators are positive; meter denominators are powers of two through 64.
Section ends are not before their starts. Names are built-in strings and are
preserved exactly. Metadata strings may be empty; algorithm version, source
size, and mtime are nonnegative; `edited` is a built-in bool.

Loading a pure Score parses primitive values, calls `normalize_events()`,
builds a static `BarGrid`, validates each stored `bar_aligned` value against
exact boundary membership, then calls `finalize_score()`. The construction
grid reaches the maximum of zero, the last effective meter tick, all marker
ticks, and every Section boundary. It affects cache length only. Sections are
sorted by `(start_tick, end_tick_exclusive, name)` without deduplication.

The sidecar additionally rejects markers past `end_tick_exclusive`, Section
starts past it, and Section ends past it. Equality with the declared end is
valid, including an end marker or zero-length Section at the end.

Canonical fixture JSON is UTF-8 without BOM, LF-only, two-space indented,
`ensure_ascii=False`, and terminated by exactly one newline. Schema key order,
not `sort_keys=True`, defines object order; Python canonicalization defines
array order.

## Resolver construction order

Binding:

```text
1. build effective tempo segments        (needs no grid)
2. convert audio end to Score seconds    (needs tempo)
3. convert Score seconds to through_tick
4. clamp through_tick to at least 0
5. build the BarGrid through the containing bar
```

```text
audio_end_score_seconds = audio_duration_seconds - audio_seconds_at_tick_zero
through_tick            = ceil(score_seconds_to_tick(audio_end_score_seconds))
```

If `audio_seconds_at_tick_zero` exceeds the audio duration, the entire audio
lies before Score tick 0 and `through_tick` would go negative. In that case
`through_tick = 0` plus a diagnostic. The alignment itself is **not** altered
— negative Score time queries remain ordinary tempo arithmetic; only the
forward-built bar cache starts at tick 0. Pre-roll is permitted under S1, and
a mis-set `downbeat_offset` reaches this case easily.

`bars.py` therefore still knows nothing about tempo, seconds, or audio.

## Diagnostics and error model

Diagnostics are **values**, not output:

```text
BarGrid.diagnostics          tuple[str, ...]
ScoreResolver.diagnostics    tuple[str, ...]
ParsedMidiScore.diagnostics  tuple[str, ...]
```

No `print`, no logging, no global collector, no ComfyUI dependency. Phase 4
collects these values into `ProviderResult.diagnostics` without adding
interpretation logic.

Structural errors raise `MidiParseError` or the corresponding validation error
on the JSON path. The dividing line: an error prevents a meaningful `Score`, a
diagnostic does not.

## Required invariants

```text
normalize(normalize(x)) == normalize(x)

score_from_dict(score_to_dict(parse_midi(data).score))
    == parse_midi(data).score

build_bar_grid(..., through_tick=t).flags == score.flags
    for every t >= max_effective_meter_tick

tick_to_position(position_to_tick(p)) == p          for valid positions
position_to_tick(tick_to_position(x))               nearest grid boundary
tick_to_seconds(seconds_to_tick(x))                 approximately x
p1 < p2  implies  position_to_tick(p1) < position_to_tick(p2)
```

The round trip compares `.score` on both sides, not `ParsedMidiScore` —
diagnostics and `end_tick_exclusive` are not part of it. In particular,
`ParsedMidiScore` gets **no** `__eq__` that compares more than intended.

A `Score` is deliberately not a mirror of the MIDI file: discarded redundant
events do not come back, and that is intended.

## Fixture and test matrix

**Binding through Phase 2b.** The real material fixes the MIDI parser happy
path, while canonical JSON fixtures establish the initial shared Score corpus.

The real Cubase MIDI is authoritative for the happy path. A small test-only
SMF writer may generate valid synthetic fixtures, while intentionally
malformed cases remain handwritten raw bytes or precise byte mutations. The
production parser must never import the test writer, and generated fixtures
must not be used as the only proof of reader correctness.

The `velvet-lies` test export as the primary fixture:

| Property | Value |
|---|---|
| Format / tracks | 1 / 1 |
| TPQ | 480 |
| File size | 129 bytes |
| Tempo | 333333 us per quarter = 180 BPM |
| End of track | tick 322350 |
| Markers | **none** |
| MIDI time range | 223.854 s |
| Audio duration | 310.68 s |
| To extrapolate | 86.826 s, about 65.12 bars of 4/4 |

Three observations, each carrying a test case:

Up to EOT there are **671.5625 quarters** — not an integer. That is the
fingerprint of the 31/32 and 63/64 bars and a fast check that the exponent
conversion is right.

The 65.12 extrapolated bars do **not** end on a bar boundary. The last bar
extends past the end of the audio — precisely the case for which
`through_tick` means "contains the bar in which the tick falls" and
`boundaries` needs an entry beyond it.

Meter data and track end coincidentally fall on the same tick here. That masks
the difference between the two `through_tick` sources; a synthetic fixture
must pull them apart.

Still to add: synthetic fixtures for marker and section edge cases, a format 0
file, multiple tracks, mid-bar meter change, mid-bar tempo change, running
status after SysEx, missing EOT, invalid VLQ, SMPTE division, Latin-1 marker
name.

Phase 2b adds seven pure Score fixtures — `constant_4_4.json`,
`changing_meter.json`, `midbar_tempo.json`, `midbar_meter.json`,
`odd_meter_31_32.json`, `unaligned_markers.json`, and
`truncated_tempo_track.json` — plus `sidecar_v1.json`. Tests regenerate every
fixture with `json.dumps(value, ensure_ascii=False, indent=2) + "\n"` and
compare the UTF-8 bytes exactly. They cover constant and changing meter,
mid-bar tempo and meter events, odd meter, exact raw names, unaligned and
zero-length Sections, and event data ending before sidecar extent. Resolver
expectations do not belong in these fixtures yet.

## Phase 2 acceptance criteria

**Binding for Phase 2b:** strict schema and metadata validation; exact object
key order; pure Score and sidecar round trips; normalization through the Phase
2a pipeline; recomputed meter flags; exact marker and Section names; preserved
duplicates; Section-alignment validation; sidecar-extent validation; canonical
fixture bytes; stdlib-only imports; no production I/O or side effects; and the
complete Phase 2a and repository test suites remaining green.

**Open for Phase 2c and final Phase 2 acceptance:** tempo segments,
tick/seconds conversion, the audio-duration-dependent grid, position queries,
extrapolation, and resolver round-trip/monotonicity acceptance. Phase 2b does
not claim that all of Phase 2 is complete.

## Appendix: what changes once markers exist

The current MIDI file has no markers yet. Two paths are therefore unexercised
today and will not stay that way.

**Merge ordering is untested.** With a single track, `(tick, track_index,
event_index)` is trivially satisfied. Cubase normally writes markers to a
dedicated marker track, so the export will become format 1 with at least two
tracks — and the stable merge order runs for real for the first time. A
synthetic multi-track fixture is therefore mandatory, not optional.

**Section derivation moves from a synthetic path to a real one.** While it
runs only against invented markers it is a function with tests. Once the track
carries markers it decides where cuts fall. The policy list above should
therefore exist before the first marker-bearing export, not after — otherwise
it gets adjusted to whatever happened to come out.
