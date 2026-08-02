# Score Phase 3 Design

> **Status:** binding implementation design for Phase 3.
> **Revision:** 1 — the runtime adapter architecture and Phase 3a/3b
> boundaries are settled.

## Status and normative precedence

The governing documents have this precedence:

1. `docs/SCORE_SUBSYSTEM.md`
2. `docs/MUSICAL_TIMING_SPEC.md`
3. `docs/AUDIO_INTEGRATION_SPEC.md`
4. `docs/SCORE_PHASE3_DESIGN.md`

`docs/SCORE_PHASE2_DESIGN.md` is an implementation-history source for the
completed pure Score core. It is not a higher-level Phase 3 contract.

This document explains and refines the implementation of the frozen
architecture and normative specifications. It does not amend them. Any
contradiction is a defect in this document. Phase 3 integrates timing adapters;
it does not activate provider resolution or node-level Score selection.

## Purpose and boundary

Phase 3 is the pure runtime-adapter layer between:

- the existing constant floating-point timing calculations
- the canonical tick mathematics in `ScoreResolver`
- the existing 13-field `MusicalTimingResult`
- the existing sample-oriented `AudioClipPlan`

The objectives are to preserve every existing constant result, validation
rule, rounding decision, and output string; expose the complete normative
`TempoMap` contract; and route the constant-shaped runtime result through a
smaller uniform bridge. A uniform Score can then supply timing without
inventing global beat or bar durations for variable timing.

Phase 3 does not select or discover a Score.

Phase 3 is complete when the complete adapters and the uniform bridge exist,
constant behavior remains unchanged, a uniform Score can drive the pure timing
and clip-plan functions, no provider or node automatically activates a Score,
nonuniform timing remains unavailable to the constant-shaped edit bridge, and
all regression contracts pass.

Completion does not mean Score selection, Score UI activation, provider
fallback, route integration, a frontend ruler, JavaScript parity, or
variable-meter editing.

## Non-goals

Phase 3 explicitly excludes:

- the provider chain and `ConstantProvider` activation
- explicit-file, JSON-sidecar, MIDI-sidecar, or analysis providers
- Score discovery, activation, persistence changes, or feature-gate UI
- node integration, widget changes, or returned-audio compatibility changes
- route or frontend changes
- JavaScript parity, waveform rulers, and snapping
- sample-conversion changes
- section editing and naming policy
- package, dependency, or version changes

Phase 3 does not claim completion of Phase 4 or Phase 5.

## Confirmed existing runtime contract

The current source and tests establish these facts:

1. `calculate_musical_timing()` currently owns alignment:

   ```text
   start_seconds = downbeat_offset + start_beats * seconds_per_beat
   ```

2. Alignment remains there during Phase 3.
3. `MusicalTimingResult` has exactly thirteen fields and is shaped around a
   globally constant tempo and meter:

   ```text
   seconds_per_tempo_pulse
   seconds_per_quarter
   seconds_per_beat
   seconds_per_bar
   start_beats
   start_seconds
   duration_beats_total
   duration_seconds
   end_seconds
   frames_per_beat
   frames_per_bar
   start_frame
   frame_count
   ```

4. `seconds_per_beat`, `frames_per_beat`, `seconds_per_bar`, and
   `frames_per_bar` are public node outputs.
5. `audio_clip_plan.py` is imported both as a package module and as a flat
   standalone test module.
6. It already has a narrow package/standalone import shim for
   `musical_timing.py`.
7. It must not import `score` directly.
8. Musical-mode display text is assembled directly from `start_bar`,
   `start_beat`, `start_subdivision`, and the duration fields.
9. Musical-mode display does not derive its visible requested position through
   a timing map.
10. A Score changes the timing behind that position, not its literal requested
    position text.
11. Existing tests protect these Seconds-mode substrings exactly:

    ```text
    Nearest: Bar 2 · Beat 2 · Subdivision 2
    Nearest: 4 subdivisions before Bar 1 · Beat 1
    ```

Phase 3b preserves their capitalization, spaces, middle-dot characters, the
word `subdivisions`, and the omission of a subdivision number in the
pre-Bar-1 form.

## Two distinct structural protocols

`TempoMap` and `UniformTimingBridge` are not synonyms. The smaller bridge must
never be named `TempoMap`.

### Complete `TempoMap`

`TempoMap` is the complete source-independent timing profile from
`docs/MUSICAL_TIMING_SPEC.md`:

```python
class TempoMap(Protocol):
    def tick_to_seconds(self, tick: int | float) -> float: ...
    def seconds_to_tick(self, seconds: int | float) -> float: ...
    def bar_to_tick(self, bar: int) -> int: ...

    def position_to_tick(
        self,
        bar: int,
        beat: int,
        subdivision: int,
        subdivisions_per_beat: int,
    ) -> int: ...

    def tick_to_position(
        self,
        tick: int | float,
        subdivisions_per_beat: int,
    ) -> tuple[int, int, int]: ...

    def meter_at_bar(self, bar: int) -> tuple[int, int]: ...
    def sections(self) -> tuple[object, ...]: ...
```

The root protocol uses `tuple[object, ...]` to avoid importing
`score.model.Section`. A concrete Score implementation retains the precise
`tuple[Section, ...]` annotation.

### Small `UniformTimingBridge`

The constant-shaped runtime functions use only this bridge:

```python
class UniformTimingBridge(Protocol):
    @property
    def supports_uniform_timing(self) -> bool: ...

    @property
    def binding(self) -> TempoMapBinding: ...

    def uniform_timing_metrics(self) -> UniformTimingMetrics: ...

    def resolve_score_relative_span(
        self,
        *,
        start_bar: int,
        start_beat: int,
        start_subdivision: int,
        duration_bars: int,
        duration_beats: int,
        duration_subdivisions: int,
        subdivisions_per_beat: int,
    ) -> ScoreRelativeSpan: ...

    def describe_nearest_position(
        self,
        score_seconds: float,
        subdivisions_per_beat: int,
    ) -> NearestPosition: ...
```

`ConstantTempoMap` and `ScoreTempoMap` implement both protocols structurally.
Neither needs to inherit from a `Protocol`. `calculate_musical_timing()` uses
only `UniformTimingBridge`; Phase 5 may consume the complete `TempoMap`.

## Shared contract leaf and dependency direction

Phase 3a introduces this neutral dependency shape:

```text
tempo_map_contract.py
    shared frozen values and structural Protocols
          ↑                         ↑
musical_timing.py             score/tempo_map.py
    ConstantTempoMap              ScoreTempoMap
          ↑
audio_clip_plan.py
    injected UniformTimingBridge
```

`tempo_map_contract.py` contains:

- `TempoUnit`
- `LegacyTimingConfiguration`
- `TempoMapBinding`
- `UniformTimingMetrics`
- `ScoreRelativeSpan`
- `NearestPosition`
- `TempoMap`
- `UniformTimingBridge`

It imports only `dataclasses`, `typing`, or strictly necessary standard-library
typing helpers. It imports no `musical_timing`, `audio_clip_plan`, `score`,
ComfyUI, or third-party package.

`musical_timing.py` imports and publicly re-exports the contract names, defines
`ConstantTempoMap`, and retains `calculate_musical_timing()`. It does not import
`score`.

`score/tempo_map.py` imports `.resolver` and, if needed, `.model` with
package-relative imports. It imports the shared leaf and defines
`ScoreTempoMap`; it does not import `musical_timing.py` or duplicate shared
dataclasses.

`audio_clip_plan.py` continues importing through `musical_timing.py`, receives
the bridge by parameter injection, and does not import `score`.

### Dual import context

The repository supports both package-context imports and flat standalone
imports used by tests. The adapter boundary therefore has one narrowly scoped
exception:

- `musical_timing.py` supports `.tempo_map_contract` and
  `tempo_map_contract`.
- `score/tempo_map.py` supports `..tempo_map_contract` and
  `tempo_map_contract`.

This exception must not spread into `score/model.py`, `score/bars.py`,
`score/normalize.py`, `score/resolver.py`, `score/midi_parse.py`, or
`score/serialize.py`. The pure Score core keeps package-relative sibling
imports only.

Phase 3a tests cover flat import, package import, arbitrary-parent package
import, absence of a top-level `score`, absence of side effects, and zero
stdout and stderr.

## Shared frozen value types

All shared values are frozen dataclasses.

```python
@dataclass(frozen=True)
class LegacyTimingConfiguration:
    bpm: float
    tempo_unit: TempoUnit
    beats_per_bar: int
    beat_unit: int


@dataclass(frozen=True)
class TempoMapBinding:
    legacy_configuration: LegacyTimingConfiguration | None
    alignment_seconds: float | None


@dataclass(frozen=True)
class UniformTimingMetrics:
    seconds_per_tempo_pulse: float
    seconds_per_quarter: float
    seconds_per_beat: float
    seconds_per_bar: float
    beats_per_bar: int
    beat_unit: int


@dataclass(frozen=True)
class ScoreRelativeSpan:
    start_beats: float
    start_seconds: float
    duration_beats_total: float
    duration_seconds: float


@dataclass(frozen=True)
class NearestPosition:
    bar: int | None
    beat: int | None
    subdivision: int | None
    subdivisions_before_bar_one: int
```

### `TempoMapBinding` invariant

Exactly one binding kind is active:

| Legacy configuration | Alignment | Valid meaning |
|---|---|---|
| set | `None` | constant legacy binding |
| `None` | set | Score alignment binding |
| set | set | invalid |
| `None` | `None` | invalid |

An invalid state raises a deterministic error describing an internal
binding-contract violation, not ordinary user input.

### `NearestPosition` invariant

`NearestPosition` has exactly two variants:

- Canonical: positive built-in `bar` and `beat`, nonnegative built-in
  `subdivision`, and `subdivisions_before_bar_one == 0`.
- Pre-Bar-1: `bar`, `beat`, and `subdivision` are all `None`, and
  `subdivisions_before_bar_one` is a positive built-in integer.

The binding biconditional is:

```text
subdivisions_before_bar_one > 0
if and only if
bar is None and beat is None and subdivision is None
```

Bar 0, placeholder numbers, mixed variants, a negative before-count, all-None
with a zero count, and a canonical position with a positive before-count are
invalid.

## `ConstantTempoMap`

`ConstantTempoMap` lives in `musical_timing.py` and binds the exact
configuration `bpm`, `tempo_unit`, `beats_per_bar`, and `beat_unit`:

```text
binding = TempoMapBinding(
    legacy_configuration=LegacyTimingConfiguration(
        bpm=bpm,
        tempo_unit=tempo_unit,
        beats_per_bar=beats_per_bar,
        beat_unit=beat_unit,
    ),
    alignment_seconds=None,
)
```

`supports_uniform_timing` is always `True`.

### Historical floating-point metrics and span

The uniform bridge preserves the existing expression structure and operation
order:

```text
seconds_per_tempo_pulse = 60.0 / bpm
seconds_per_quarter = seconds_per_tempo_pulse / tempo_unit_in_quarters
seconds_per_beat = seconds_per_quarter * (4.0 / beat_unit)
seconds_per_bar = seconds_per_beat * beats_per_bar

start_beats =
    (start_bar - 1) * beats_per_bar
    + (start_beat - 1)
    + start_subdivision / subdivisions_per_beat

start_seconds = start_beats * seconds_per_beat

duration_beats_total =
    duration_bars * beats_per_bar
    + duration_beats
    + duration_subdivisions / subdivisions_per_beat

duration_seconds = duration_beats_total * seconds_per_beat
```

It does not construct a `Score`, quantize `us_per_quarter`, convert through
integer ticks, resolve an end position and subtract, or normalize legacy
subdivision overflow. The constant nearest-position calculation likewise
uses the historical linear floating-point formula and half-away-from-zero
rounding, never synthetic tick methods. It first quantizes a signed relative
subdivision count. A nonnegative result returns canonical numeric fields,
including the Bar-1 origin when the count is zero; a negative result returns
the pre-Bar-1 `NearestPosition` variant with the positive absolute count.

### Complete constant `TempoMap`

The complete normative surface presents a synthetic uniform 960-TPQ tick
space. Its tick/seconds, bar, position, meter, and inverse-position methods
obey the canonical `TempoMap` rules and use absolute, non-cumulative rounding.
`meter_at_bar()` returns the bound meter, and `sections()` returns `()` because
the constant profile has no Sections.

The synthetic tick space is isolated from the legacy span and nearest-position
calculations. It must never change historical floating-point results.

## `ScoreTempoMap`

`ScoreTempoMap` lives in `score/tempo_map.py` and wraps exactly one immutable
`ScoreResolver`. It does not copy or reimplement resolver mathematics.

Its complete `TempoMap` methods delegate directly:

| `ScoreTempoMap` method | `ScoreResolver` method |
|---|---|
| `tick_to_seconds` | `tick_to_seconds` |
| `seconds_to_tick` | `seconds_to_tick` |
| `bar_to_tick` | `bar_to_tick` |
| `position_to_tick` | `position_to_tick` |
| `tick_to_position` | `tick_to_position` |
| `meter_at_bar` | `meter_at_bar` |
| `sections` | `sections` |

Resolver-specific helpers such as `bar_length_ticks()` may be exposed, but
they are not members of either required Phase 3 protocol.

Its binding is:

```text
TempoMapBinding(
    legacy_configuration=None,
    alignment_seconds=resolver.audio_seconds_at_tick_zero,
)
```

### Uniform feature gate

The bridge gate is exactly:

```text
not score.has_variable_meter
and not score.has_midbar_meter_change
and len(score.tempos) == 1
```

The tempo-count clause is required because a constant-meter Score with
multiple tempo events still has no global `seconds_per_beat` or
`seconds_per_bar`. Complete `TempoMap` mathematics remains available for
nonuniform Scores; only the constant-shaped bridge is gated. No misleading
global timing metric is invented.

Bridge methods perform real runtime gate checks and reject nonuniform Scores
deterministically. They must not rely on `assert`, which disappears under
`python -O`.

### Uniform Score metrics

For a gated Score, with the meter effective at Bar 1:

```text
seconds_per_quarter = score.tempos[0].us_per_quarter / 1_000_000
seconds_per_beat = seconds_per_quarter * (4.0 / denominator)
seconds_per_bar = seconds_per_beat * numerator
seconds_per_tempo_pulse = seconds_per_quarter
beats_per_bar = numerator
beat_unit = denominator
```

`seconds_per_tempo_pulse` equals `seconds_per_quarter` because a `TempoEvent`
contains only `us_per_quarter`. `ScoreTempoMap` neither reconstructs nor claims
knowledge of the DAW's original tempo-unit label.

### Canonical Score span

The start is resolved through canonical Score position and tick APIs:

```text
start_tick = resolver.position_to_tick(
    start_bar,
    start_beat,
    start_subdivision,
    subdivisions_per_beat,
)
start_seconds = resolver.tick_to_seconds(start_tick)
```

Duration fields remain nonnegative quantities and may overflow their nominal
units. Under the proved uniform-meter gate, the implementation converts the
start and duration to a linear subdivision count, converts the resulting end
index back to one canonical position, resolves its exact Score tick, and uses:

```text
subdivisions_per_bar = numerator * subdivisions_per_beat

start_linear_subdivision =
    ((start_bar - 1) * numerator + (start_beat - 1))
    * subdivisions_per_beat
    + start_subdivision

duration_linear_subdivisions =
    (duration_bars * numerator + duration_beats)
    * subdivisions_per_beat
    + duration_subdivisions

end_linear_subdivision =
    start_linear_subdivision + duration_linear_subdivisions

end_bar_index, end_subdivision_in_bar =
    divmod(end_linear_subdivision, subdivisions_per_bar)

end_beat_index, end_subdivision =
    divmod(end_subdivision_in_bar, subdivisions_per_beat)

end_position = (
    end_bar_index + 1,
    end_beat_index + 1,
    end_subdivision,
)

start_tick = resolver.position_to_tick(
    start_bar,
    start_beat,
    start_subdivision,
    subdivisions_per_beat,
)
end_tick = resolver.position_to_tick(*end_position, subdivisions_per_beat)
start_score_seconds = resolver.tick_to_seconds(start_tick)
end_score_seconds = resolver.tick_to_seconds(end_tick)

duration_seconds = end_score_seconds - start_score_seconds
```

This is a **uniform-meter bridge only**. The linear index must never be reused
for a variable-meter ruler or snapping. The end is resolved canonically; the
historical constant arithmetic is not used for `ScoreTempoMap`.

`ScoreRelativeSpan.start_beats` intentionally has map-relative semantics:

- `ConstantTempoMap` counts legacy beats under its widget meter.
- `ScoreTempoMap` counts Score beats under the effective Score meter.

For the Score map, this is
`start_linear_subdivision / subdivisions_per_beat`.

At Bar 11 in uniform 3/4, `start_beats == 30`, not 40 from a 4/4 widget.
`start_beats` is not currently a node output, but this semantic difference is
intentional.

Score positions remain canonical: bars and beats are 1-based, subdivisions
are 0-based and below `subdivisions_per_beat`, and nominal positions outside a
shortened bar are invalid. Legacy constant `start_subdivision` overflow remains
accepted arithmetically. Phase 3 activates neither behavior in a node, so no
existing workflow changes.

Canonical start-beat validation follows the active binding. A
`ConstantTempoMap` preserves the legacy `start_beat <= beats_per_bar` check
against its bound legacy meter. A `ScoreTempoMap` does not apply the widget
meter's cross-field limit: it validates the complete position through
`ScoreTempoMap`/`ScoreResolver`, using the effective Score meter as authority.
The widget `beats_per_bar` remains required and independently domain-valid,
but it cannot reject or admit a Score position.

### Nearest position

`describe_nearest_position()` receives Score-relative seconds and no audio
offset. Its caller performs exactly once:

```text
score_seconds = audio_start_seconds - downbeat_offset
```

Both maps first calculate the signed relative grid position under their
uniform timing and quantize it with round-half-away-from-zero. The resulting
signed subdivision count selects the variant:

- A nonnegative count returns the canonical variant.
- A negative count returns the pre-Bar-1 variant with its positive absolute
  value as `subdivisions_before_bar_one`.

For nonnegative Score time, `ScoreTempoMap` calls
`resolver.seconds_to_tick()` and then `resolver.tick_to_position()`. Negative
raw Score seconds never enter `resolver.tick_to_position()` directly. They are
first quantized as a signed uniform-grid displacement. A displacement that
quantizes to zero returns canonical Bar 1, Beat 1, Subdivision 0; only a
strictly negative quantized count returns the pre-Bar-1 variant. No Bar 0,
negative bar, or placeholder canonical fields are invented.

The required negative half-boundaries are:

| Relative time | Quantized count | Result |
|---|---:|---|
| -0.49 subdivision widths | 0 | Bar 1 / Beat 1 / Subdivision 0 |
| exactly -0.5 subdivision width | -1 | 1 subdivision before Bar 1 / Beat 1 |
| -4.0 subdivision widths | -4 | 4 subdivisions before Bar 1 / Beat 1 |

Neither map returns display strings. Formatting belongs solely to
`audio_clip_plan.py`.

## `calculate_musical_timing()` integration — Phase 3a

Phase 3a adds one optional final keyword parameter equivalent to:

```python
tempo_map: UniformTimingBridge | None = None
```

The parameter may remain named `tempo_map` because production adapters
implement both protocols; its static dependency is the smaller bridge.

When it is `None`, the function constructs `ConstantTempoMap` from the existing
timing arguments. When supplied, dispatch is structural: no `isinstance()` and
no `score` import. Validation retains the public argument domains while the
active bridge determines which meter validates the canonical start beat.

### Existing validation remains authoritative

All legacy timing arguments remain required. Validation independent of the
active meter occurs before map resolution:

- `bpm` is a positive finite built-in `int` or `float`.
- `tempo_unit` is a supported built-in string.
- `beats_per_bar` is a built-in `int` and at least one.
- `beat_unit` is a built-in `int` and greater than zero.
- `fps` is a positive finite built-in `int` or `float`.
- `subdivisions_per_beat` is a built-in `int` and at least one.
- `downbeat_offset` is a finite built-in `int` or `float`.
- `start_bar` and `start_beat` are built-in `int` values and at least one.
- `start_subdivision` is a nonnegative built-in `int`.
- Every duration field is a nonnegative built-in `int`.

Thus `bpm=0` still raises the existing `ValueError` with a supplied
`ScoreTempoMap`. Meter-dependent start-beat validation then follows the active
bridge:

- `ConstantTempoMap` preserves `start_beat <=` its bound legacy
  `beats_per_bar`.
- `ScoreTempoMap` does not reject against stale or nonauthoritative widget
  `beats_per_bar`; it validates the full canonical position through the map
  and resolver against the effective Score meter.

Consequently, a uniform 4/4 Score with widget `beats_per_bar=3` accepts
`start_beat=4`. A uniform 3/4 Score with widget `beats_per_bar=4` rejects
`start_beat=4` through Score canonical-position validation. A
`ConstantTempoMap` bound to 3/4 retains the existing `ValueError` for
`start_beat=4`. Legacy type and value-domain validation remains active, but a
legacy cross-field relationship never overrides Score meter authority.

### Central binding validation

For a constant binding, compare `LegacyTimingConfiguration` exactly with the
effective `bpm`, `tempo_unit`, `beats_per_bar`, and `beat_unit` arguments. A
mismatch raises `ValueError`; neither source silently wins. Exact float
equality is intentional because both values must come from the same effective
configuration source.

For a Score binding, require:

```text
binding.alignment_seconds == downbeat_offset
```

A mismatch raises a deterministic error equivalent in meaning to:

```text
ScoreTempoMap resolver alignment is stale or inconsistent with the effective
downbeat_offset
```

The error does not claim that Score-relative arithmetic is wrong. It prevents
a resolver cache built under another alignment, stale audio-before-zero
diagnostics, and two competing runtime alignment values.

### Alignment and result assembly

`downbeat_offset` is absolute:

```text
downbeat_offset
    == audio_seconds_at_tick_zero
    == audio time of Score tick 0
```

It is not additive to a provider alignment. `calculate_musical_timing()`
applies it exactly once:

```text
start_seconds = downbeat_offset + span.start_seconds
duration_seconds = span.duration_seconds
end_seconds = start_seconds + duration_seconds

start_frame = round_half_away_from_zero(start_seconds * fps)
frame_count = round_half_away_from_zero(duration_seconds * fps)
frames_per_beat = fps * metrics.seconds_per_beat
frames_per_bar = fps * metrics.seconds_per_bar
```

The bridge consumes Score-relative values. It does not call
`resolver.tick_to_audio_seconds()`.

The function obtains metrics from `uniform_timing_metrics()`, a span from
`resolve_score_relative_span()`, and assembles the unchanged
`MusicalTimingResult`. Constant operation order remains historical;
`ScoreTempoMap` uses canonical ticks. No concrete-map type check, constant-path
`us_per_quarter` quantization, or Score-path legacy BPM arithmetic is allowed.

The public outputs `seconds_per_beat`, `frames_per_beat`, `seconds_per_bar`, and
`frames_per_bar` keep their names and types. They retain historical values for
the constant map and describe the uniform Score for a gated Score map. They are
unavailable for a nonuniform bridge rather than made optional or misleading.
No existing output is removed or made optional in Phase 3.

## Golden divergence pair

Phase 3a protects both mathematical paths with this exact pair:

| Input | Constant float path | Canonical Score path |
|---|---:|---:|
| BPM concept | 127 BPM | 127 BPM |
| Constant tempo unit | Quarter | not reconstructed |
| FPS | 25 | 25 |
| Subdivisions per beat | 7 | 7 |
| Position | Bar 11, Beat 1, Subdivision 3 | same |
| TPQ | not used by bridge | 960 |
| `us_per_quarter` | not quantized | 472441 |
| Meter | 4/4 | 4/4 |
| Position tick | not used by bridge | 38811 |
| `start_seconds` | approximately 19.100112 | approximately 19.099904 |
| `start_frame` | **478** | **477** |

`472441` is the Quarter-note microsecond value for this pair. `314961` belongs
to the separate Dotted-Quarter example and must not appear in this pair.

The Phase 3a test carries this rationale:

> This divergent regression pair uses the contract's synthetic Score tick
> space of 960 TPQ. ConstantTempoMap's legacy span calculation must not use
> that tick space. If the synthetic TPQ contract changes, replace this pair
> with a newly verified divergent case.

The Constant result protects the historical floating-point path. The Score
result protects canonical tick arithmetic from replacement by that path.

## `AudioClipPlan` routing — Phase 3b

Phase 3b adds an optional `UniformTimingBridge` argument to
`create_audio_clip_plan()`. The module forwards it to
`calculate_musical_timing()`, changes no sample conversion, rounding,
clamping, return field, or import context, and imports no `score` module.

### Seconds mode

`start_time` and `end_time` keep selecting the same samples. After existing
sample quantization and clamping, nearest-position display uses the actual
selected `start_seconds`, because the string describes returned audio:

```text
score_seconds = start_seconds - downbeat_offset

nearest = tempo_map.describe_nearest_position(
    score_seconds,
    subdivisions_per_beat,
)
```

`audio_clip_plan.py` formats the value:

```text
Canonical:
Bar {bar} · Beat {beat} · Subdivision {subdivision}

Pre-Bar-1:
{subdivisions_before_bar_one} subdivisions before Bar 1 · Beat 1
```

Those forms feed the existing `Nearest: ` prefix. No Bar 0 or placeholder
numeric field can appear.

Changing `downbeat_offset` changes only the musical interpretation of a fixed
Seconds-mode sample range. A larger offset means earlier Score-relative time
for the same audio second.

### Musical mode

Musical mode keeps formatting the requested widget position and existing
duration label directly. It does not call `describe_nearest_position()`.
Map-derived timing changes the requested time behind the text; a uniform 3/4
Score may therefore change time without rewriting the literal requested
Bar/Beat/Subdivision text.

Before clamping, changing the effective offset by `delta`:

- shifts `requested_start_seconds` by approximately `delta`
- shifts `requested_end_seconds` by approximately `delta`
- changes neither requested duration nor requested musical position
- changes neither uniform metrics nor `frame_count` before clamping

Tests inspect `requested_start_seconds` and `requested_end_seconds`, not the
post-quantization and post-clamping `start_seconds` and `end_seconds`. They use
`assertAlmostEqual` unless the delta is exactly representable in binary.

The Audio Integration Contract remains authoritative: selected sample
boundaries, half-away rounding, nonempty selection, clamping, and recalculated
returned times and frames do not change.

## Phase 4 carry-forward requirements

### Effective absolute offset order

Phase 4 performs these steps in order:

1. Resolve the one effective `downbeat_offset`, including any external
   `downbeat_offset_input` override.
2. Build `ScoreResolver` only after that value is known.
3. Build `ScoreTempoMap` from that resolver.
4. Pass the same exact value to `calculate_musical_timing()` and
   `create_audio_clip_plan()`.

The resolver must not use a stale widget value while timing uses the external
override. Provider alignment is not added to widget alignment:

```text
not: effective_offset = provider_offset + widget_offset
```

A provider value may initialize or restore the control, but one node execution
has exactly one effective absolute alignment.

### Legacy subdivision overflow

Legacy constant timing permits `start_subdivision >= subdivisions_per_beat`;
canonical Score positions reject it. Before Score activation, Phase 4 must
normalize the position canonically, decline activation for that state, or
return an explicit UI-handleable diagnostic. A previously valid state such as
`start_subdivision=9` with `subdivisions_per_beat=4` must not become an
uncaught node exception merely because a Score is available.

## Separately reviewed implementation commits

Architecture documentation:

```text
A docs/SCORE_PHASE3_DESIGN.md
```

Phase 3a — runtime adapters:

```text
A tempo_map_contract.py
M musical_timing.py
A score/tempo_map.py
A tests/test_tempo_map.py
```

Phase 3b — clip-plan routing:

```text
M audio_clip_plan.py
A tests/test_audio_clip_plan_tempo_map.py
```

Existing tests remain unchanged unless a genuinely new test module is needed.
Phase 3a and Phase 3b are not mixed.

## Phase 3a acceptance criteria

- All existing `test_musical_timing`, Score, and node-contract tests pass
  unchanged.
- Direct legacy calls, internally constructed `ConstantTempoMap`, and an
  explicitly supplied matching constant map return the same numeric results.
- Conflicting constant configuration raises `ValueError`.
- Constant span and nearest-position calculations do not call tick methods.
- Complete constant `TempoMap` methods use the synthetic 960-TPQ space only
  where the normative surface requires it.
- `ScoreTempoMap` delegates the complete surface to `ScoreResolver`; full
  mathematics works for variable tempo and meter.
- The uniform bridge rejects multiple tempo events, variable meter, and
  mid-bar meter changes.
- A uniform 3/4 Score overrides 4/4 widget timing authority.
- Legacy arguments remain required and retain their independent type and
  value-domain validation under `ScoreTempoMap`.
- A uniform 4/4 Score accepts Beat 4 when widget `beats_per_bar` is 3.
- A uniform 3/4 Score rejects Beat 4 when widget `beats_per_bar` is 4.
- A constant 3/4 map retains the existing `ValueError` for Beat 4.
- Alignment mismatch and invalid `TempoMapBinding` states are rejected.
- Invalid and mixed `NearestPosition` states are rejected.
- No `isinstance` map dispatch exists.
- `musical_timing.py` imports no `score`; `score/tempo_map.py` imports no
  `musical_timing`; shared dataclasses are not duplicated.
- Flat, package, and arbitrary-parent imports pass with no top-level `score`,
  side effects, stdout, or stderr.
- The golden divergence pair returns exactly 478 and 477.

## Phase 3b acceptance criteria

- All existing `test_audio_clip_plan` tests pass unchanged.
- Both protected nearest-position strings remain byte-for-byte unchanged.
- Musical mode uses Score-derived timing but keeps requested-position and
  duration formatting.
- Seconds mode uses `describe_nearest_position()` and formats both variants
  exactly; no Bar 0 or placeholder numeric fields are possible.
- A time at -0.49 subdivision widths resolves to canonical Bar 1, Beat 1,
  Subdivision 0.
- The exact -0.5 subdivision-width tie resolves to one subdivision before
  Bar 1, Beat 1.
- A time at -4.0 subdivision widths resolves to four subdivisions before
  Bar 1, Beat 1.
- `downbeat_offset` is subtracted exactly once for nearest lookup and added
  exactly once for Musical start time.
- Requested offset-shift tests inspect requested times and use approximate
  equality for non-binary-exact deltas.
- Sample ownership, sample and frame rounding, and clamping remain governed by
  the Audio Integration Contract.
- `audio_clip_plan.py` imports no `score`, and standalone flat imports remain
  valid.

## Phase 3 completion boundary

Phase 3 acceptance requires the complete `TempoMap` adapters, the distinct
uniform bridge, unchanged constant results, uniform Score timing in both pure
runtime functions, and all regressions above. Provider or node selection does
not activate a Score. Nonuniform Scores retain their complete mathematical
surface but remain unavailable to the constant-shaped edit bridge.

Phase 3 does not claim Score selection, Score UI activation, provider fallback,
route integration, a frontend ruler, JavaScript parity, or variable-meter
editing.
