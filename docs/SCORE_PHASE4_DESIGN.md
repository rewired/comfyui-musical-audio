# Score Phase 4 Design

> **Status:** binding implementation design for Phase 4.
> **Revision:** 1 - provider resolution and node integration are divided into
> separately reviewable Phase 4a and Phase 4b changes.

## Status and normative precedence

The governing documents have this precedence:

1. `docs/SCORE_SUBSYSTEM.md`
2. `docs/MUSICAL_TIMING_SPEC.md`
3. `docs/AUDIO_INTEGRATION_SPEC.md`
4. `docs/SCORE_PHASE3_DESIGN.md` for the completed runtime-adapter boundary
5. `docs/SCORE_PHASE4_DESIGN.md`

`docs/SCORE_PHASE2_DESIGN.md` records the completed pure Score core and is an
implementation-history source for its delivered APIs. It is not a higher-level
Phase 4 contract. `docs/README_FILE_CONVENTION.md` is user-facing guidance and
does not override the specifications.

This document explains and refines Phase 4 implementation. It does not amend
the frozen architecture, S1-S10, or either normative specification. A
contradiction is a defect here. The delivered source is authoritative for the
spelling and fields of existing identifiers.

## Purpose and completion boundary

Phase 4 connects the completed Score core and runtime adapters to
`MusicalLoadAudioUI`. It resolves one Score through the specified provider
chain, activates a uniform Score only when the published bridge can represent
it, keeps unsupported Scores display-only, appends the frozen node outputs,
and extends cache invalidation to every Score candidate.

The work is split into two commits:

- **Phase 4a** implements the ComfyUI-free provider core in
  `score/providers.py` and its isolated tests.
- **Phase 4b** resolves ComfyUI paths, integrates the provider selection into
  `musical_audio_ui.py`, appends node outputs, and updates node-level tests.

Phase 4 is complete when provider precedence and invalidity are deterministic,
the constant path remains compatible, uniform Score timing reaches the
published `UniformTimingBridge`, display-only data cannot partially control
editing, every output has one defined source, and missing sidecars invalidate
ComfyUI's cache when they later appear.

Phase 4 does not implement a route, frontend Score transport, JavaScript
timing, variable-meter editing, Section editing, analysis, stems, or batches.

## Frozen delivered contracts

Phase 4 reuses the delivered value types from `score/model.py` unchanged:

```python
ScoreFormat = Literal["json", "midi", "analyzed", "constant"]

ProviderKind = Literal[
    "explicit",
    "json_sidecar",
    "midi_sidecar",
    "analysis",
    "constant",
]

ProviderStatus = Literal["not_applicable", "found", "invalid"]

@dataclass(frozen=True)
class ResolvedScore:
    score: Score
    audio_seconds_at_tick_zero: float
    provider: ProviderKind

@dataclass(frozen=True)
class ProviderResult:
    status: ProviderStatus
    score: Score | None
    diagnostics: tuple[str, ...]
    provenance: Mapping[str, str]
```

Phase 4 also reuses, rather than wrapping or duplicating, these delivered
boundaries:

- `ScoreSidecar`, `ScoreSerializationError`, `score_from_dict()`, and
  `sidecar_from_dict()` from `score/serialize.py`
- `ParsedMidiScore`, `MidiParseError`, and `parse_midi()` from
  `score/midi_parse.py`
- `ScoreResolver` from `score/resolver.py`
- `ScoreTempoMap` from `score/tempo_map.py`
- `ConstantTempoMap` and `round_half_away_from_zero()` from
  `musical_timing.py`
- `AudioClipPlan` and `create_audio_clip_plan()` from `audio_clip_plan.py`

`score/model.py` and all existing frozen value types remain byte-untouched.
Phase 4 adds no replacement `Score`, `ProviderResult`, or `ResolvedScore`.

## Dependency direction

The Phase 4 dependency shape is:

```text
score/model.py      score/serialize.py      score/midi_parse.py
       ^                    ^                       ^
       +--------------------+-----------------------+
                            |
                    score/providers.py
                    stdlib and score/ only
                            ^
                            |
                   musical_audio_ui.py
                 folder_paths and node policy
                            |
              musical_timing.py + audio_clip_plan.py
                    existing APIs unchanged
```

`score/providers.py` uses only standard-library file and JSON operations. It
must not import `folder_paths`, `torch`, `av`, ComfyUI, a repository-root
module, or a third-party package. All Score sibling imports remain
package-relative. It must import silently and perform no discovery, reads,
stats, logging, or cache mutation at import time.

`folder_paths` is used only in Phase 4b, at the node boundary that already
owns annotated ComfyUI paths. The provider core receives ordinary resolved
paths and narrow configuration values.

Neither `musical_timing.py` nor `audio_clip_plan.py` changes in Phase 4. The
published parameter-injection boundary is sufficient.

## Phase 4a provider core

### Provider interface and selection result

`score/providers.py` defines one structural provider interface. Each provider
has a fixed `kind: ProviderKind` and returns an internal typed selection:

```python
def resolve(self) -> ProviderSelection: ...
```

The internal provider-chain value is:

```python
@dataclass(frozen=True)
class ProviderSelection:
    provider: ProviderKind
    result: ProviderResult
    provider_alignment_seconds: float | None
```

`ProviderResult` remains the delivered frozen provider payload. The additional
typed field carries wrapper alignment without changing that payload or
encoding runtime data into provenance text.

The public Phase 4 orchestration result is:

```python
@dataclass(frozen=True)
class ScoreResolution:
    resolved_score: ResolvedScore | None
    provider: ProviderKind
    provider_alignment_seconds: float | None
    diagnostics: tuple[str, ...]
    provenance: Mapping[str, str]
    dependency_fingerprint: tuple[object, ...]
```

`dependency_fingerprint` is the complete immutable nested candidate tuple from
the fingerprint contract below. `ScoreResolution` is orchestration state, not
a replacement for `Score`, `ProviderResult`, or `ResolvedScore`.

The chain entry point has the semantic surface:

```python
def resolve_provider_chain(
    providers: Sequence[ScoreProvider],
    *,
    audio_seconds_at_tick_zero: float,
    dependency_fingerprint: tuple[object, ...],
) -> ScoreResolution: ...
```

The entry point validates the three-state invariants before selecting:

- `not_applicable` requires `score is None` and continues.
- nonconstant `found` requires `score is not None`, constructs one
  `ResolvedScore` with the supplied effective alignment and the winning
  provider's `kind`, and returns it in `ScoreResolution`
- constant `found` requires `score is None`, records provider `"constant"`,
  leaves `ScoreResolution.resolved_score=None`, and stops
- `invalid` requires `score is None` and stops with a deterministic provider
  error. It never proceeds to a lower-priority provider.

The constant case is deliberately explicit. The delivered legacy
`ConstantTempoMap` accepts every positive `beat_unit`, while the frozen Score
model accepts only power-of-two meter denominators through 64. Fabricating a
synthetic Score would either narrow existing input behavior or create an
invalid Score. `ProviderResult.score` is already optional, so the terminating
constant timing profile uses that existing absence instead of changing a
frozen type or lying about the meter.

Provider diagnostics and provenance are retained in their original order.
No provider writes to stdout, stderr, a logger, or global state.

The alignment field on every internal selection is exact:

```text
wrapper JSON  -> decoded built-in float
bare JSON     -> None
MIDI          -> None
analysis      -> None
constant      -> None
```

The winning value is copied unchanged into
`ScoreResolution.provider_alignment_seconds`. A provenance mapping can repeat
the alignment as display text, but no runtime branch parses that string or
uses it as the source of an alignment comparison.

On `found`, `ScoreResolution.diagnostics` and `.provenance` are the winning
`ProviderResult` values, preserved in source order, and
`.dependency_fingerprint` is the exact tuple supplied to the chain. Phase 4b
starts its deterministic diagnostic accumulator with
`ScoreResolution.diagnostics` before appending resolver and integration
diagnostics.

### Ordered providers

The node constructs and runs exactly this order:

1. `ExplicitFileProvider`
2. `SidecarJsonProvider`
3. `MidiSidecarProvider`
4. `AnalysisProvider`
5. `ConstantProvider`

The first `found` result wins. A higher-priority `invalid` result terminates
resolution. `ConstantProvider` is the final terminator and returns `found`, so
a successful chain always selects a timing provider even when no external
Score exists.

### ExplicitFileProvider

The future `score_file` value is semantically absent only when it is empty or
contains whitespace only. The literal filename `none` is valid and is never a
sentinel.

Phase 4b resolves a nonblank ComfyUI value to an ordinary path before
constructing `ExplicitFileProvider`. An unresolved explicit value and a
resolved but missing path are both `invalid`. Their messages distinguish
resolution from existence, but their control flow is identical and neither
falls back.

Suffix dispatch is case-insensitive and deterministic:

- `.json`, including `.score.json`, selects JSON decoding.
- `.mid` and `.midi` select Standard MIDI decoding.
- every other suffix is `invalid`.

No content sniffing, extension fallback, or heuristic retry is permitted. A
file selected as JSON that contains MIDI bytes is invalid JSON; a file selected
as MIDI that contains JSON is invalid MIDI.

### Automatic sidecars

The automatic paths are derived from the normalized resolved audio path:

```text
<audio-stem>.score.json
<audio-stem>.mid
```

For `music.wav`, these are `music.score.json` and `music.mid`. The original
audio suffix is replaced, not retained. The exact automatic order is JSON and
then MIDI. A missing optional automatic candidate is `not_applicable`; a
present but unreadable or malformed candidate is `invalid` and stops the
chain.

Automatic discovery is unavailable when the audio path itself cannot be
resolved. Both automatic providers then return `not_applicable`; an explicit
Score can still win, and otherwise the chain reaches the constant provider.

### JSON decoding and schema selection

JSON text is decoded with the standard library. The decoded root must be a
built-in `dict`. An array, string, number, Boolean, or `null` root is
`invalid`; it is not treated as a schema with missing fields.

Both supported schemas contain `schema_version`. That field must never select
the decoder.

Decoder selection uses only these structural discriminators:

- a top-level `score` field identifies a `ScoreSidecar` envelope
- a top-level `ticks_per_quarter` field identifies a bare `Score`

A root with neither discriminator is invalid. A root with both is ambiguous
and invalid. There is no heuristic based on filename, schema version, nested
fields, or failed-first-decoder retry.

The selected decoded object is passed unchanged to the existing strict
decoder:

```text
wrapper -> sidecar_from_dict(root)
bare    -> score_from_dict(root)
```

Phase 4 does not duplicate required-field, unknown-field, type, version,
meter, alignment, Section, or extent validation from `score/serialize.py`.
`ScoreSerializationError.code`, `.path`, and `.message` remain the JSON error
authority.

A wrapper supplies its decoded built-in float
`audio_seconds_at_tick_zero` as the typed
`ProviderSelection.provider_alignment_seconds`. A bare Score supplies `None`.
In either case the winning `ResolvedScore` receives the one effective runtime
alignment supplied to `resolve_provider_chain()`; the wrapper value is never
substituted into it.

Automatic `.score.json` candidates support either JSON shape.
A bare Score simply has no wrapper-derived alignment or source-file metadata.

### Sidecar source identity

For a wrapper, `derived_from` is compared with the selected audio file's
filename, size, and nanosecond mtime when those values are available.

- A matching identity is ordinary `found`.
- An automatically discovered, unedited wrapper whose source identity is
  stale is `not_applicable`, allowing the next provider to run. Phase 4 does
  not regenerate it because analysis is not implemented.
- An edited wrapper remains `found` when source identity differs, as required
  by S9, and carries a nonfatal provider warning.
- An explicitly selected valid wrapper remains the user's explicit choice and
  is `found`; stale source metadata is a nonfatal provider warning.

This policy never rewrites, deletes, or regenerates a sidecar.

### MIDI decoding

MIDI providers read built-in `bytes` and call `parse_midi()` exactly once.
The returned Score is `ParsedMidiScore.score`. Its diagnostics are preserved
in source order. `end_tick_exclusive` is retained as deterministic provenance;
it is not added to `Score` or `ResolvedScore`.

`MidiParseError` makes the provider `invalid`. The provider does not catch
`KeyboardInterrupt` or `SystemExit`, and it does not replace the parser's
stable code or location with an incidental traceback.

### AnalysisProvider

`AnalysisProvider` is a reserved stub and nothing more:

```text
kind        = "analysis"
status      = "not_applicable"
score       = None
diagnostics = ()
provenance  = empty mapping
```

It always returns `not_applicable`. It reads no audio, imports no analysis
library, schedules no work, creates no cache, and contains no placeholder
algorithm. Phase 4 does not create `score/analyze.py`.

### ConstantProvider

`ConstantProvider` is the terminator and returns exactly:

```text
kind        = "constant"
status      = "found"
score       = None
diagnostics = ()
provenance  = {"format": "constant", "provider": "constant"}
```

It selects the delivered legacy `ConstantTempoMap` profile; it does not
construct a synthetic Score, quantize `us_per_quarter`, or narrow the legacy
meter domain. Phase 4b omits `tempo_map` or supplies the exact matching
`ConstantTempoMap`, preserving the historical floating-point calculation.

### Provenance

Every provider uses a deterministic built-in mapping with string keys and
string values. File providers record provider kind, normalized path, selected
schema or format, and the stable parser/serialization identity available to
them. MIDI results include `end_tick_exclusive`. Wrapper results can include a
display representation of stored alignment and include source identity, but
the typed `provider_alignment_seconds` remains the sole comparison source.

Provenance never controls decoder choice after parsing, is never parsed to
recover provider alignment, never overrides the effective alignment, and never
appears in `musical_position`. Mapping order is defined by construction,
although callers must not depend on arbitrary mapping iteration for
diagnostics ordering.

## Candidate fingerprint contract

Phase 4a supplies a deterministic path-fingerprint helper that Phase 4b uses
from `IS_CHANGED`. The helper performs path normalization and `stat` only. It
does not open or hash file contents, matching the existing audio fingerprint
cost model.

Each candidate contributes one nested tuple with this conceptual shape:

```text
(role, normalized_path, "missing")
(role, normalized_path, "file", size, mtime_ns)
(role, normalized_path, "unreadable")
```

The exact tuple field order is fixed in implementation tests. Paths use the
existing node convention:

```python
os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))
```

No value contains NaN, an exception instance, a mutable object, Python's
process-randomized `hash()`, or an exception message whose wording can change
between calls.

The complete Score portion of the node fingerprint contains, in order:

1. a fixed fingerprint schema/version marker
2. the raw `score_file` selection or the stable blank marker
3. the explicit candidate state, if semantically supplied
4. the automatic `.score.json` candidate state
5. the automatic `.mid` candidate state
6. the fixed tuple `("provider_contract", 1, "analysis", "stub")`

Normal ComfyUI input hashing continues to cover ordinary timing input values.
The AnalysisProvider has no configuration in Phase 4. BPM, tempo unit, meter,
alignment, and the other timing inputs remain ordinary ComfyUI inputs and are
already part of its input cache key; duplicating them inside `IS_CHANGED` would
create a second configuration identity. The raw explicit selection and fixed
provider-contract marker are included because they determine candidate
interpretation and invalidate a future provider-contract revision.

Missing automatic candidates are always represented. A missing
`.score.json` is not omitted merely because the MIDI or constant provider
currently wins. Its later appearance changes `"missing"` to `"file"`, makes
`IS_CHANGED` return a different value immediately, and allows its higher
priority to take effect without restarting ComfyUI. The same rule applies to
the MIDI candidate.

The audio-file fingerprint remains present and keeps its existing
none/unresolved/missing/file states. Score candidate entries are appended to
that stable audio identity; they do not replace it.

## Phase 4b node integration

### Input contract

Append one local required widget after the existing `snap_mode` widget:

```python
"score_file": (
    "STRING",
    {"default": "", "socketless": True},
)
```

This preserves the complete existing required-widget prefix. The input is
semantically optional because blank or whitespace means no explicit file.
The string is not a combo and does not use `none` as a sentinel.

`load_audio()` receives `score_file` after `snap_mode` and before the existing
optional arguments. The seven external timing inputs retain their names,
types, defaults, and precedence. `VALIDATE_INPUTS(cls, audio)` remains narrow;
it does not accept `score_file` or `**kwargs`.

`IS_CHANGED(cls, audio, **_kwargs)` remains a classmethod for compatibility
and reads `score_file` from `_kwargs` without mutating it.

### Execution order

One node execution follows this order:

1. Resolve all external-or-local timing values.
2. Resolve the audio annotated path and decode or construct the existing
   one-second silence fallback.
3. Determine the actual sample rate, sample count, and audio duration.
4. Resolve the one effective `downbeat_offset`, including the external input.
5. Resolve a nonblank explicit `score_file` through `folder_paths`.
6. Derive automatic candidate paths from the resolved audio path.
7. Construct and run the ordered provider chain, producing one
   `ScoreResolution` with the same dependency fingerprint used by
   `IS_CHANGED`.
8. Construct `ScoreResolver` for a nonconstant
   `ScoreResolution.resolved_score` using the actual audio duration and the
   one effective alignment.
9. Construct `ScoreTempoMap` and apply the edit-mode-specific activation rules.
10. Call `create_audio_clip_plan()` exactly once with either the active
    `ScoreTempoMap` or the unchanged constant timing authority.
11. Slice the waveform using the returned sample boundaries.
12. Derive display-only Score values and append the six outputs.

The resolver is never built from the stale local widget value when an
external `downbeat_offset_input` is connected.

### One alignment authority

For every nonconstant winning provider:

```text
effective_alignment
    == effective downbeat_offset
    == ResolvedScore.audio_seconds_at_tick_zero
    == ScoreResolver.audio_seconds_at_tick_zero
    == create_audio_clip_plan(downbeat_offset=...)
```

No provider offset is added to a widget offset. No second resolver is built
for display. A wrapper's typed provider alignment is restoration metadata, not
a second execution-time origin.

If `ScoreResolution.provider_alignment_seconds` is not `None`, Phase 4b
compares that typed float directly with the effective `downbeat_offset` using
exact built-in float equality. A difference emits
`score_alignment_divergence` as a warning. The comparison never reads or
parses `ScoreResolution.provenance`. The effective node value remains
authoritative, the resolver is built with it, and an otherwise supported Score
remains active. This is required so a user can deliberately correct alignment
through the existing control without editing the sidecar.

The warning never causes addition, averaging, tolerance-based snapping, or a
stale-binding error inside Phase 3. `ScoreTempoMap.binding.alignment_seconds`
therefore still equals the exact value passed into `create_audio_clip_plan()`.

### Uniform activation gate

`ScoreTempoMap.supports_uniform_timing` is the only timing-shape gate. Phase 4
does not duplicate or weaken its exact condition:

```text
not score.has_variable_meter
and not score.has_midbar_meter_change
and len(score.tempos) == 1
```

After the timing-shape gate succeeds, activation depends on edit mode.

#### Seconds-mode activation

Seconds mode does not inspect `start_bar`, `start_beat`, or
`start_subdivision`. Those widgets do not select the clip in Seconds mode and
cannot refuse Score activation. A uniform `ScoreTempoMap` activates directly.

The published Phase 3b planner still calculates uniform metrics before its
Seconds-mode branch. To keep unused legacy position widgets out of that
calculation, Phase 4b supplies the neutral canonical timing-only start
`Bar 1 / Beat 1 / Subdivision 0` when it invokes an active Score map in Seconds
mode. This is not normalization or validation of the user's unused widget
values. It changes no sample boundary or Seconds-mode text. Duration fields
remain the original nonnegative quantities and retain their documented
overflow behavior.

#### Musical-mode activation

Musical mode checks only the requested start position. `duration_bars`,
`duration_beats`, and `duration_subdivisions` remain quantities; their overflow
is valid and is excluded from activation preflight.

The preflight is a direct deterministic query, not `try`/`except` control flow
around `ScoreResolver.position_to_tick()`. Under the proved uniform-meter gate,
perform these checks in order:

1. `start_bar` is a built-in integer and is positive.
2. Query `resolver.meter_at_bar(start_bar)` and retain the effective Score
   numerator and denominator.
3. `start_beat` is a built-in integer satisfying
   `1 <= start_beat <= numerator`.
4. `subdivisions_per_beat` is a built-in positive integer.
5. `start_subdivision` is a built-in integer satisfying
   `0 <= start_subdivision < subdivisions_per_beat`.
6. The grid is representable in integer Score ticks:

   ```text
   subdivisions_per_beat * denominator
       <= 4 * score.ticks_per_quarter
   ```

The widget `beats_per_bar` is never Score authority. A failed direct check
emits `score_start_position_not_canonical` followed by
`score_activation_refused` and selects constant editing without calling
`position_to_tick()`.

Only after every direct check succeeds does Phase 4b call:

```python
musical_start_tick = resolver.position_to_tick(
    start_bar,
    start_beat,
    start_subdivision,
    subdivisions_per_beat,
)
```

The exact integer result is retained for unclamped active-Musical Section
lookup. This resolver call is not wrapped in a broad exception handler;
unexpected failure after a successful preflight remains a visible defect.
The `ScoreTempoMap` then activates and is supplied to
`create_audio_clip_plan()`.

If the Score fails the uniform gate, Phase 4 emits
`score_activation_refused` and uses the constant editing path. The Score
remains available for permitted display metadata.

There is no partially active timing path. A Score either supplies the one
bridge used by both timing and Seconds-mode nearest lookup, or it supplies
neither. Phase 4 never mixes Score metrics with constant selection, Score
selection with constant nearest display, or a Score meter with constant span
arithmetic.

When constant editing is selected, the current legacy start-beat clamp against
the effective widget `beats_per_bar` remains in force. That clamp must not run
before an active Score has applied its edit-mode rule. This preserves existing
constant workflows, allows a 4/4 Score to accept Beat 4 in Musical mode when
the widget still says 3/4, and prevents an unused Beat 4 widget from disabling
a uniform 3/4 Score in Seconds mode.

### Invalid provider boundary

A Score provider result of `invalid` is fatal for node execution. Phase 4b
raises a deterministic node error containing a serialized
`score_provider_error` diagnostic. It does not call the constant provider,
does not create a clip plan, and cannot return a misleading successful
`diagnostics` output.

The existing audio decode fallback is different: the Audio Integration
Contract already requires one second of returned silence. That recoverable
condition remains a successful node execution and is represented as a warning
with code `score_provider_error` in the new diagnostics output. It no longer
alters `musical_position`.

`KeyboardInterrupt` and `SystemExit` are never swallowed at any file,
provider, resolver, or node boundary.

## Section selection

### Query helper

The pure Phase 4a helper accepts a `Sequence[Section]` and an integer or finite
floating-point query tick. Section membership is half-open:

```text
section.start_tick <= tick < section.end_tick_exclusive
```

When multiple Sections contain the tick, selection is independent of input
order and uses this exact key:

1. greatest `start_tick`
2. smallest `end_tick_exclusive`
3. lexicographically smallest `name` under Python string ordering

Equivalently, select the minimum of:

```python
(-section.start_tick, section.end_tick_exclusive, section.name)
```

No Section is moved, snapped, merged, deduplicated, or rewritten. The helper
returns the winning Section or `None`.

A zero-length Section contains no tick and can never win. Consequently a
zero-length marker-derived Section exactly at track end never appears as
`section_name`. This is intentional half-open behavior, not an off-by-one
error.

When the constant fallback wins or when no interval contains the query tick,
`section_name` is the empty string.

### Query ownership by edit mode

There is no single audio-seconds reference rule shared by both edit modes.
Section query ownership is exact.

For **Musical mode with an active Score and no clamping**:

```text
section_query_tick = musical_start_tick
```

Use the exact integer canonical tick retained by the successful Musical
activation preflight. Do not convert `timing.start_seconds` or
`plan.requested_start_seconds` back through
`resolver.audio_seconds_to_tick()`. Avoiding that floating-point and sample
round trip guarantees that an exact Section boundary belongs to the following
half-open Section rather than drifting into the preceding one.

For **Musical mode with clamping**:

```text
section_query_tick =
    resolver.audio_seconds_to_tick(plan.start_seconds)
```

The Section describes the actually returned clip start.

For **Seconds mode without clamping**:

```text
section_query_tick =
    resolver.audio_seconds_to_tick(plan.requested_start_seconds)
```

The requested time remains authoritative for Section membership. Do not use
the sample-quantized `plan.start_seconds`.

For **Seconds mode with clamping**:

```text
section_query_tick =
    resolver.audio_seconds_to_tick(plan.start_seconds)
```

For a **display-only Score**, no active canonical Score start tick exists.
Both edit modes therefore use the same requested-versus-returned audio-seconds
rule based on clamping: requested start without clamping, returned start with
clamping. The resulting lookup remains display-only and never changes
selection, timing metrics, nearest-position behavior, or selected samples.

Every audio-derived floating-point query tick is compared directly with
integer half-open Section boundaries. `seconds_to_tick()` already returns a
floating-point query by contract; Section containment does not invent a second
rounding policy.

### Intentional Seconds-mode distinction

In Seconds mode, `musical_position` and `section_name` can use different
reference times and therefore different ticks.

The existing protected nearest-position path remains unchanged:

```text
describe_nearest_position(
    plan.start_seconds - effective_downbeat_offset,
    subdivisions_per_beat,
)
```

It uses the post-sample-quantization, post-clamp returned start. The protected
text containing:

```text
Nearest: Bar 2 · Beat 2 · Subdivision 2
```

must remain byte-for-byte unchanged.

When no clamping occurred, `section_name` instead uses
`plan.requested_start_seconds`. Those values normally differ only by sample
quantization, but they can fall on opposite sides of an exact Section
boundary. Therefore this combination is valid:

```text
musical_position: Nearest: Bar 9 · Beat 1
section_name: Verse 2
```

even when Bar 9 begins `Chorus 2`. The outputs answer different questions:
nearest describes the returned sample start, while Section describes the
unclamped request. They must not be forced onto one tick. When clamping did
occur, both use the returned start reference, although nearest still resolves
to a grid position while Section uses half-open interval containment.

## Appended node outputs

The existing fourteen outputs retain their exact order, names, types, and
meaning. Append these six outputs after `fps`:

| Index | Type | Name | Derivation |
|---:|---|---|---|
| 15 | `INT` | `end_frame_exclusive` | `plan.start_frame + plan.frame_count` |
| 16 | `STRING` | `section_name` | selected canonical Section name or `""` |
| 17 | `INT` | `sample_rate` | actual decoded or fallback sample rate |
| 18 | `STRING` | `score_format` | resolved Score source or `"constant"` |
| 19 | `STRING` | `score_provider` | selected provider kind |
| 20 | `STRING` | `diagnostics` | deterministic compact JSON described below |

The frame invariant is unconditional:

```text
end_frame_exclusive == start_frame + frame_count
```

It uses the returned clip plan and does not independently round
`end_seconds * fps`.

For the terminating constant provider:

```text
score_format   = "constant"
score_provider = "constant"
section_name   = ""
```

For every nonconstant provider, `score_format` is
`ScoreResolution.resolved_score.score.source` and `score_provider` is
`ScoreResolution.provider`. The constant values come directly from the
terminal selection and do not require a fabricated `ResolvedScore`.

For a display-only Score, `score_format` and `score_provider` continue to
identify the Score that was actually found, not the constant editing bridge.
`score_activation_refused` explains why it did not control editing.

`musical_position` remains domain text. Score provenance, activation warnings,
audio fallback warnings, and resolver findings move to `diagnostics`; they are
not appended to `musical_position`.

## Diagnostics contract

### Serialized form

`diagnostics` is a compact JSON array encoded with:

```python
json.dumps(values, ensure_ascii=False, separators=(",", ":"))
```

Every entry has exactly these keys in this order:

```json
{"code":"...","severity":"warning","message":"..."}
```

`code` and `message` are built-in strings. `severity` is exactly `warning` or
`error`. Messages are made single-line by collapsing whitespace and are
bounded to 200 characters with the existing ellipsis convention. Empty
diagnostics serialize as `[]`, not an empty string, `null`, or an object.

Fatal Score-provider invalidity uses the same one-entry JSON representation in
the raised node error, making the error machine-readable without pretending a
node output was returned.

### Closed code list

The Phase 4 integration code list is closed:

```text
score_activation_refused
score_alignment_divergence
score_provider_error
score_start_position_not_canonical
score_resolver_diagnostic
```

No other Phase 4 code is emitted.

The meanings are:

| Code | Severity | Condition |
|---|---|---|
| `score_activation_refused` | warning | a found Score cannot own editing because the uniform gate failed, or because Musical-mode start preflight failed |
| `score_alignment_divergence` | warning | wrapper alignment differs from the effective node alignment; the node value remains authoritative |
| `score_provider_error` | warning or error | recoverable audio/provider warning, edited stale provenance, or fatal Score-provider invalidity; fatality is expressed by `severity="error"` and the raised node error |
| `score_start_position_not_canonical` | warning | the requested Musical-mode start failed direct canonical Score preflight; Seconds mode never emits it for unused position widgets |
| `score_resolver_diagnostic` | warning | generic carrier for an existing Phase 2 parser, BarGrid, or ScoreResolver diagnostic string |

Existing Phase 2 diagnostic strings remain the `message` of
`score_resolver_diagnostic`. Phase 4 does not turn each text into a new public
code.

### Ordering and repetition

Diagnostics append in execution order:

1. recoverable audio/provider warnings
2. provider parser diagnostics in their original order
3. resolver diagnostics in their original order
4. alignment divergence
5. noncanonical-start diagnostic
6. activation-refused diagnostic

No set, lexical sort, or dictionary iteration determines order. Repeated
Phase 2 messages are preserved because they can describe distinct source
events. Each integration-generated alignment, start-position, and activation
diagnostic is emitted at most once per execution by construction; there is no
text-based deduplication.

An invalid Score provider stops before resolver construction and produces one
fatal `score_provider_error` containing its deterministic underlying code and
message. Lower-priority provider diagnostics do not exist because those
providers were not run.

## Compatibility guarantees

When no external Score file or automatic sidecar is available:

- `ConstantProvider` wins with `resolved_score=None`.
- the same effective local/external timing values reach the existing constant
  path
- all fourteen existing outputs remain unchanged
- sample rounding, clamping, one-sample fallback, final-sample fallback, and
  returned-time recalculation remain unchanged
- both protected nearest-position strings remain unchanged
- the new outputs report constant provenance and `[]`

When a Score is active, `create_audio_clip_plan()` receives exactly one
`ScoreTempoMap`. The same instance drives `calculate_musical_timing()` and
Seconds-mode nearest lookup through the already published Phase 3b routing.

When a Score is display-only, `create_audio_clip_plan()` receives the constant
authority. The Score resolver answers only Section and provenance display
queries but cannot provide timing metrics, selection boundaries, snapping, or
nearest-position behavior.

No Phase 4 code reaches into a concrete adapter with `isinstance()` dispatch.
Node policy constructs `ScoreTempoMap`, inspects its published
`supports_uniform_timing` property, and inject it through
`UniformTimingBridge`; timing behavior stays behind the published interface.

## Exact implementation scope

### Phase 4a - provider core

```text
A score/providers.py
A tests/test_score_providers.py
```

No other file changes in Phase 4a.

The tests import `score.providers` without ComfyUI and cover provider classes,
the internal typed `ProviderSelection`, the public frozen `ScoreResolution`,
chain resolution, decoding, source identity, fingerprints, and Section
selection. Temporary files use the standard library and are cleaned by the
test owner.

### Phase 4b - node integration

```text
M musical_audio_ui.py
M tests/test_audio_change_detection.py
M tests/test_load_audio_outputs.py
M tests/test_node_contract.py
A tests/test_score_node_integration.py
```

No route, JavaScript, CSS, root registration, `score/model.py`,
`musical_timing.py`, or `audio_clip_plan.py` change belongs in Phase 4b.
`musical_audio_ui.py` consumes `ScoreResolution` directly and never parses
alignment from its provenance mapping.

## Phase 4a test matrix

Provider-core tests cover at least:

- exact provider order and winner
- `not_applicable`, `found`, and `invalid` invariants
- invalid stops without constructing or running later providers
- exact frozen `ScoreResolution` fields and immutable dependency fingerprint
- typed wrapper alignment survives provider selection and chain resolution
- bare JSON, MIDI, analysis, and constant alignment are exactly `None`
- provenance alignment text is never parsed for runtime comparison
- explicit blank and whitespace values are not applicable
- a literal path named `none` is addressable
- explicit unresolved and missing paths are invalid
- `.json`, `.score.json`, `.mid`, and `.midi` suffix dispatch
- case-insensitive suffixes and invalid suffixes
- JSON wrapper and bare Score roots
- non-object JSON roots: array, string, number, Boolean, and null
- neither and both schema discriminators
- no `schema_version` heuristic and no decoder retry
- strict `ScoreSerializationError` preservation
- valid and malformed MIDI
- parser diagnostics and `end_tick_exclusive` provenance
- automatic JSON-before-MIDI precedence
- missing automatic candidate is not applicable
- present malformed automatic candidate is invalid
- wrapper typed provider alignment plus optional display provenance
- matching and divergent alignment without offset addition
- edited and unedited stale source identity policy
- AnalysisProvider always returns the exact empty `not_applicable` result
- ConstantProvider always terminates without fabricating a Score
- no integer tempo or meter representation enters legacy timing calculations
- path normalization and stable present/missing/unreadable fingerprints
- missing JSON appears, missing MIDI appears, mutation, disappearance, and
  higher-priority appearance
- no content read during fingerprint calculation
- overlapping Section selection and every tie-break level
- input-order independence
- half-open start/end boundaries
- zero-length and exact-track-end Sections never win
- no matching Section returns `None`
- package-relative Score imports only
- no ComfyUI, root, third-party, network, logging, environment, or import-time
  filesystem side effect

## Phase 4b test matrix

Node integration tests cover at least:

- the existing required-widget sequence remains an exact prefix
- `score_file` has the exact STRING/default/socketless contract
- the existing optional timing inputs remain unchanged
- `VALIDATE_INPUTS` remains narrow
- all fourteen existing outputs remain an exact prefix
- the six new types and names have exact order
- local and external effective timing values retain precedence
- effective external alignment is used for provider, resolver, and planner
- `folder_paths` is absent from all files under `score/`
- blank/whitespace score_file and literal filename `none`
- explicit annotated-path resolution, unresolved path, and missing path
- automatic candidate construction beside the resolved audio file
- provider precedence through the node
- fatal provider invalidity does not fall back or call the planner
- constant fallback returns legacy-identical first fourteen outputs
- constant format/provider/empty Section/empty diagnostics outputs
- uniform Score activation in Seconds and Musical modes
- a 4/4 Score accepts Beat 4 despite a stale 3/4 widget meter
- Seconds mode with a uniform 3/4 Score and unused `start_beat=4` remains
  Score-active
- Seconds mode does not inspect any unused start-position widget
- Musical mode with a uniform 3/4 Score and `start_beat=4` refuses activation
  without an uncaught exception
- Musical mode with `start_subdivision >= subdivisions_per_beat` refuses
  activation
- Musical preflight is direct, calls `position_to_tick()` only after success,
  and does not catch unexpected resolver failures
- `duration_beats` and `duration_subdivisions` can overflow their nominal
  units without refusing activation; `duration_bars` remains a quantity too
- variable tempo, variable meter, and mid-bar meter Scores remain display-only
- no partially active Score metrics or nearest lookup
- alignment divergence warns, keeps the effective node value, and remains
  active when the Score is otherwise supported
- typed provider alignment, not provenance text, drives divergence comparison
- provider and resolver strings map only to the five closed codes
- deterministic compact diagnostic JSON, ordering, bounds, and repetition
- audio fallback warning moves out of `musical_position`
- `end_frame_exclusive == start_frame + frame_count`
- actual decoded and fallback `sample_rate`
- Score format and provider remain actual for display-only Scores
- overlap selection and empty `section_name`
- active unclamped Musical lookup uses the retained exact integer start tick
- an exact active-Musical Section boundary selects the following Section and
  cannot drift backward through seconds conversion
- Seconds-mode unclamped requested-time Section lookup
- Seconds-mode clamped returned-time Section lookup
- exact sample-quantization and Section-boundary divergence
- clamped Musical lookup uses returned audio time
- display-only lookup uses requested-versus-returned audio time, produces its
  Section name, and cannot affect selection
- protected `Nearest: Bar 2 · Beat 2 · Subdivision 2` behavior
- missing `.score.json` and `.mid` candidates participate in `IS_CHANGED`
- candidate appearance, mutation, removal, and precedence without restart
- existing audio fingerprint states remain present
- no input mutation or module-level cache state

## Forbidden work and non-goals

Phase 4 contains no:

- route or server endpoint
- JavaScript, CSS, or frontend change
- widget JavaScript or UI extension
- change to `score/model.py` or any frozen Score type
- change to `musical_timing.py`
- change to `audio_clip_plan.py`
- runtime TempoMap redesign
- variable-meter editing
- Section editing or persistence writing
- Score analysis implementation
- meaningful `AnalysisProvider` behavior
- `score/analyze.py`
- stem discovery or material package
- package or dependency addition
- network access
- version bump or changelog change
- README change
- tag, merge, PR, or branch operation

The future frontend route and JavaScript Score resolver remain Phase 5. The
analysis slot remains reserved for Phase 8. Phase 4 must not pull either
forward.

## Phase 4a acceptance criteria

- Only `score/providers.py` and `tests/test_score_providers.py` change.
- The provider chain uses the delivered frozen model types and exact order.
- Frozen `ScoreResolution` carries typed provider alignment, diagnostics,
  provenance, and the immutable dependency fingerprint explicitly.
- No runtime alignment is recovered from provenance text.
- Invalid stops; absent optional candidates continue; constant terminates.
- Both JSON shapes use deterministic object-root discrimination.
- MIDI and JSON errors preserve their existing stable information.
- `AnalysisProvider` is exactly an always-`not_applicable` stub.
- Candidate fingerprints include missing candidates and detect later
  appearance.
- Section lookup is half-open, deterministic under overlap, and independent of
  input order.
- The module imports without ComfyUI, root imports, third-party dependencies,
  stdout, stderr, or side effects.
- All existing Score and repository-compatible tests remain green.

## Phase 4b acceptance criteria

- Only the documented node integration and node-test paths change.
- The first fourteen node outputs and existing inputs retain compatibility.
- The six frozen Phase 4 outputs are appended in exact order.
- One effective alignment is resolved before `ScoreResolver` construction and
  reaches every timing boundary unchanged.
- Typed provider alignment is compared directly with the effective offset;
  provenance is never parsed for alignment.
- Uniform supported Scores activate directly in Seconds mode without
  inspecting unused start-position widgets.
- Musical mode uses direct start-only canonical preflight, retains the exact
  integer start tick, and leaves duration overflow valid.
- Unsupported Scores and Musical start-preflight refusals never partially
  control editing.
- Invalid providers never silently reach constant timing.
- Display-only Scores retain their true format, provider, and permitted
  Section display.
- Active unclamped Musical Section lookup uses the retained integer start tick;
  clamped and display-only lookups follow their documented audio-time rules.
- Seconds-mode nearest and Section reference times follow their deliberately
  distinct contracts.
- The five diagnostic codes are the complete public Phase 4 code set.
- `IS_CHANGED` detects a newly created higher-priority sidecar without a
  ComfyUI restart.
- `folder_paths` remains exclusively at the Phase 4b boundary.
- `score/model.py`, `musical_timing.py`, `audio_clip_plan.py`, routes,
  frontend files, dependencies, and versions remain unchanged.
- All existing Score, timing, clip-plan, node-contract, load-output, and
  change-detection tests remain green together with the new integration tests.

## Phase 4 completion boundary

Phase 4 ends with Score selection and uniform node timing in Python. It does
not make variable timing editable in the existing frontend, expose Score data
at edit time, or implement cross-language parity. Those remain behind the
existing feature gate until Phase 5.

The decisive guarantees are: one provider winner, one absolute alignment, one
active timing bridge, no partial Score editing, one deterministic Section
selection rule, one cache identity that includes absent future candidates, and
one closed diagnostic vocabulary.
