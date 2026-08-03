# Score Phase 5 Design

> **Status:** binding implementation design.
> **Revision:** 1.
> **Boundary:** Phase 5 boundaries and behavior are settled. This document
> refines implementation details but does not amend higher-level
> specifications.

## Normative precedence

Conflicts are resolved in this order:

1. `docs/SCORE_SUBSYSTEM.md`
2. `docs/MUSICAL_TIMING_SPEC.md`
3. `docs/AUDIO_INTEGRATION_SPEC.md`
4. `docs/SCORE_PHASE3_DESIGN.md`
5. `docs/SCORE_PHASE4_DESIGN.md`
6. `docs/SCORE_PHASE5_DESIGN.md`

The exact-exclusive-end, meter-stable-duration, fatal-error, shared-start
anchor, and start-local metric contracts remain unchanged. This design only
assigns them to concrete implementation boundaries. A contradiction with a
higher-precedence document is a defect in this design.

## Purpose and completion boundary

Phase 5 completes the Score timing path across server transport, a JavaScript
Score resolver, one frontend `TimeAxis`, complete tick-based Python planning,
exact Score selection persistence, a time-proportional ruler, Score-aware
selection and snapping, the node view, and the waveform-editor dialog.

A frontend-only implementation is insufficient. The delivered node still
activates Scores through `UniformTimingBridge`. A correct variable-meter ruler
paired with constant backend cutting is a forbidden partial Score mode.

Phase 5 is complete only when:

- every valid resolved Score controls planning regardless of tempo or meter
  shape;
- Python and JavaScript use the same tick mathematics;
- the frontend persists a canonical start and exclusive end that the backend
  reproduces exactly;
- no valid nonuniform Score silently selects `ConstantTempoMap`; and
- route, frontend, and node use the same provider order, candidate identity,
  diagnostic policy, and absolute alignment.

## Verified delivered baseline

These are current implementation facts, not Phase 5 claims:

- `score/providers.py` implements the Phase 4 provider chain, typed provider
  alignment, dependency fingerprint, and missing-candidate identity.
- `score_file` is the final required node widget.
- `MusicalLoadAudioUI` has twenty outputs. A nonconstant found Score already
  supplies format, provider, diagnostics, and Section metadata.
- Only `ScoreTempoMap.supports_uniform_timing` can currently activate Score
  editing. Nonuniform Scores are display-only.
- Section anchoring already retains the exact active, unclamped Musical start
  tick. Other cases use requested or returned audio seconds according to the
  clamping contract.
- `js/musical_grid.js` represents positions with one global linear
  subdivision index. Its snapping assumes one global `beatsPerBar` and one
  beat duration.
- The node and dialog waveform x-axis is audio seconds.
- `js/waveform_editor_dialog.js` is a mounting spike, not a complete editor.
- `waveform_routes.py` supplies the established secure-path, strong-ETag,
  bounded-byte-cache, 304, and `asyncio.to_thread` pattern.
- `package.json` selects ES modules and declares no external test framework or
  runtime dependency. JavaScript tests use the built-in `node:test` runner.
- `__init__.py` currently registers only the waveform route.

Future behavior is identified by its 5a, 5b, 5c, or 5d publication boundary.
No route, UI, or complete-planning behavior is delivered merely by publishing
this document.

## Binding subphase split

### Phase 5a — Score route and transport

Purpose: publish a secure, cached Score endpoint and make provider and
diagnostic resolution one shared Python repository service.

Dependency direction:

```text
score model/serialize/MIDI/providers
                 |
          score/runtime.py
        stdlib + score siblings
          ^               ^
          |               |
musical_audio_ui.py   score/routes.py
 folder_paths boundary  folder_paths + aiohttp boundary
          ^               ^
          +-------+-------+
                  |
       audio_duration_probe.py

route_cache.py <- waveform_routes.py
route_cache.py <- score/routes.py
```

Behavior added: shared repository resolution, shared structured diagnostics,
decoded-duration probing, the schema-versioned Score route, shared cache
primitives, and idempotent registration.

Excluded: JavaScript consumption, complete Score planning, new widgets,
frontend editing, and analysis work.

Tests cover the repository service, secure route, schema/status matrix,
duration probe, ETag/cache behavior, registration, import modes, and unchanged
waveform-route behavior. Phase 5a is one separately reviewed commit and is
runnable after publication.

Acceptance requires exact node/route provider and annotated-path parity,
path-safe version-1 responses, decoded-sample duration, bounded revalidation,
unchanged waveform transport, deterministic registration, and a green full
Python/PyAV suite.

### Phase 5b — JavaScript resolver and TimeAxis

Purpose: publish a pure JavaScript mirror and one source-independent time-axis
facade without changing the rendered node.

Dependency direction:

```text
js/score_loader.js -> HTTP schema only
js/score.js        -> no DOM, app, or loader
js/time_axis.js    -> js/score.js + js/musical_grid.js primitives
future UI          -> js/score_loader.js + js/time_axis.js only
```

Behavior added: route loading and validation, Score normalization, complete
resolver mathematics, constant and Score `TimeAxis` implementations, snapping,
range conversion, and a shared cross-language corpus.

Excluded: widget mutation, ruler replacement, dialog integration, backend
planning, and CSS changes.

Tests run with `node --test`; Python verifies the same golden corpus. Phase 5b
is one separately reviewed commit and remains usable without a browser.

Acceptance requires exact discrete Python/JavaScript parity, the complete
facade and tie policy, safe rejection, all loader lifecycle states, unchanged
constant arithmetic, and green Python and dependency-free Node suites.

### Phase 5c — Complete Python Score planning

Purpose: make every resolved nonconstant Score the backend timing authority and
route its requested range through the one neutral sample-boundary layer.

Dependency direction:

```text
score/resolver.py <- score/selection.py
                            ^
                            |
musical_audio_ui.py -> RequestedAudioRange
                            |
                    audio_clip_plan.py
                  no import from score/
```

Behavior added: exact-end widgets, pure Score selection, meter-stable duration
fallback, fatal request errors, complete nonuniform planning, the shared start
anchor, local metrics, and exact-end display text.

Excluded: route schema changes, frontend widget writes, frontend ruler work,
Section editing, and analysis.

Tests cover pure selection, neutral sample planning, node integration, output
compatibility, fatal boundaries, and imports. Phase 5c is one separately
reviewed commit and makes backend execution complete before frontend
publication. Its three newly appended socketless exact-end widgets remain
visible low-level controls because this subphase does not modify JavaScript.

Acceptance requires every valid Score shape to control one sample-planning
pass, both fatal codes to stop before that pass, exact start/end ticks, shared
local metadata, unchanged twenty-output compatibility, a successful manual
cross-meter request through the visible exact-end controls, and green full
Python/PyAV suites.

### Phase 5d — Frontend ruler, selection, snapping, and dialog integration

Purpose: replace the linear frontend authority with the Phase 5b `TimeAxis`
and make both UI surfaces share one transport and selection runtime.

Dependency direction:

```text
score_loader.js + time_axis.js
              |
      selection_model.js
        ^             ^
        |             |
musical_audio_ui.js  waveform_editor_dialog.js
        |             |
        +-- shared node transport, waveform state, and TimeAxis --+
```

Behavior added: time-proportional Score ruler, actual-time snapping, exact-end
persistence, state-dependent editability, variable Score metronome scheduling,
ownership and hiding of the exact-end widgets, and the real dialog consumer.

Excluded: backend changes, new route schemas, analysis algorithms, Section
editing/saving, package dependencies, and batch behavior.

Automated tests precede a mandatory manual browser gate. Phase 5d stays
uncommitted until the user reports the complete visual checklist accepted; it
is then published as one separate commit.

Acceptance requires one axis and selection model in both views, a seconds-axis
ruler, actual-time snapping, atomic exclusive-end persistence, exact state
editability, complete lifecycle cleanup, green Python/Node suites, and the
user's manual validation report.

## Shared provider and diagnostic boundary

### `score/runtime.py`

Phase 5a creates `score/runtime.py`. It imports only standard-library modules
and package-relative Score siblings. It never imports `folder_paths`, ComfyUI,
Torch, PyAV, a repository-root module, or aiohttp.

The module owns these frozen values:

```python
@dataclass(frozen=True)
class ResolvedPathState:
    selection: str
    resolved_path: str | None
    resolution_error: str | None


@dataclass(frozen=True)
class ScoreDiagnostic:
    code: str
    severity: Literal["warning", "error"]
    message: str


@dataclass(frozen=True)
class ScoreRepositoryRequest:
    score_file: str
    audio_path: str | None
    explicit: ResolvedPathState
    json_sidecar_path: str | None
    midi_sidecar_path: str | None
    dependency_fingerprint: tuple[object, ...]


@dataclass(frozen=True)
class ScoreRepositoryResult:
    resolution: ScoreResolution
    diagnostics: tuple[ScoreDiagnostic, ...]
    score_format: ScoreFormat
    score_provider: ProviderKind


class ScoreRepositoryError(ValueError):
    provider: ProviderKind
    category: Literal["not_found", "unprocessable"]
    diagnostic: ScoreDiagnostic
```

Its public API is exact:

```python
prepare_score_repository_request(
    *,
    score_file: str,
    audio_path: str | None,
    explicit: ResolvedPathState,
) -> ScoreRepositoryRequest

resolve_score_repository(
    request: ScoreRepositoryRequest,
    *,
    audio_seconds_at_tick_zero: float,
) -> ScoreRepositoryResult

append_resolver_diagnostics(
    result: ScoreRepositoryResult,
    *,
    resolver_diagnostics: tuple[str, ...],
    effective_alignment: float,
) -> ScoreRepositoryResult

diagnostic(code: str, severity: str, message: object) -> ScoreDiagnostic
serialize_diagnostics(values: Sequence[ScoreDiagnostic]) -> str
```

`resolve_score_repository()` constructs the provider sequence in the fixed
order explicit, JSON sidecar, MIDI sidecar, analysis stub, constant. It catches
`ProviderChainError` and raises `ScoreRepositoryError` containing exactly one
`score_provider_error` diagnostic with severity `error`. The shared module
classifies the delivered stable explicit-missing/unresolved provider codes as
`not_found`; malformed, unreadable, unsupported, and parser failures are
`unprocessable`. Node and route never parse diagnostic text or convert the
provider exception independently. The route maps those typed categories to
404 and 422; the node raises the shared diagnostic JSON.

`prepare_score_repository_request()` preserves the raw `score_file` string,
the explicit resolution state, both automatic candidates, and the complete
Phase 4 fingerprint. Missing higher-priority candidates remain in that
fingerprint so their later appearance changes both `IS_CHANGED` and the route
ETag. Automatic candidates require a resolved audio path. No raw selection is
interpreted relative to cwd or to the audio path.

Blank or whitespace-only `score_file` is absent. The literal filename `none`
is a real selection. An unresolved or missing explicit selection remains
terminal invalid. Provider alignment remains typed. It is compared directly
with the effective alignment, never parsed from provenance, and never added to
widget alignment.

ComfyUI annotated-path resolution stays in `musical_audio_ui.py` and
`score/routes.py`. Both pass an explicit `ResolvedPathState` into the shared
module. `score/model.py`, `score/resolver.py`, and all frozen model types remain
independent of ComfyUI.

### Shared diagnostic representation

`ScoreDiagnostic` is the only route/node structured diagnostic value. The
helper collapses whitespace to one line, replaces an empty message with
`"no diagnostic message"`, and truncates beyond 200 characters by retaining
the first 197 characters and appending `...`.

Serialization is:

```python
json.dumps(
    [{"code": d.code, "severity": d.severity, "message": d.message}
     for d in values],
    ensure_ascii=False,
    separators=(",", ":"),
)
```

The insertion order is `code`, `severity`, `message`; messages and diagnostic
ordering are deterministic. The delivered Phase 4 five-code vocabulary and
the normative Phase 5c fatal codes retain their meanings.

## Phase 5a Score route

### Query contract

```text
SCORE_ROUTE = "/comfyui-musical-audio/score"
```

The method is GET. The only query keys are `audio`, `score_file`, and
`downbeat_offset`. The frontend constructs them with `URLSearchParams`, so
UTF-8 names, spaces, backslashes, and punctuation are percent-encoded by the
browser rather than concatenated manually.

- `audio` occurs exactly once and is nonblank. The literal `none` denotes the
  current no-audio widget state.
- `score_file` occurs zero or one time. Omission, empty text, and whitespace
  mean absent. Its original nonblank string is retained in fingerprints.
- `downbeat_offset` occurs zero or one time. Omission means `0.0`. Present text
  is parsed with `float()`, must produce a finite built-in float, and is
  canonicalized as that float for identity and payload output.
- Duplicate known keys, unknown keys, a missing `audio`, a blank `audio`, NUL,
  or an invalid numeric value produce HTTP 400.

The route constants are exact:

```text
SCORE_ROUTE_SCHEMA_VERSION = 1
SCORE_PROVIDER_CONTRACT_VERSION = 1
SCORE_PAYLOAD_VERSION = 1
AUDIO_DURATION_PROBE_VERSION = 1
```

`downbeat_offset` is the local preview value. It is the absolute audio time of
Score tick zero. Stored provider alignment is comparison metadata and is never
added. An external connection is unknowable at edit time; execution remains
authoritative.

| Query state | Normalized value | Result |
|---|---|---|
| one nonblank non-sentinel `audio` satisfying the shared annotated-path contract | exact decoded string | continue |
| `audio=none` | no-audio sentinel | continue with one-second duration target |
| missing, blank, or repeated `audio` | none | 400 `score_route_query_invalid` |
| omitted/blank/whitespace `score_file` | absent selection | continue |
| one nonblank `score_file` satisfying the shared annotated-path contract | exact decoded string, including the literal filename `none` | continue |
| repeated `score_file` | none | 400 `score_route_query_invalid` |
| unknown annotation, forbidden path form, or containment escape in either file value | none | 400 `score_route_query_invalid` |
| omitted `downbeat_offset` | `0.0` | continue |
| one finite parsed `downbeat_offset` | built-in float | continue |
| blank, nonnumeric, nonfinite, or repeated `downbeat_offset` | none | 400 `score_route_query_invalid` |
| any unknown query key | none | 400 `score_route_query_invalid` |

### Secure annotated path boundary

Phase 5a adds private route helpers in `score/routes.py`; it does not weaken
`waveform_routes.resolve_input_file()`.

For both a non-sentinel `audio` value and a nonblank `score_file`, the route and
node accept exactly the same ComfyUI annotation vocabulary: an unannotated
relative value uses the input root, while explicit `[input]`, `[output]`, and
`[temp]` annotations select their named roots. No value is interpreted relative
to cwd or to the audio file.

Before resolution, each client value is rejected if it has an unknown
annotation, is absolute, is drive-qualified outside the annotation contract,
has a URL scheme or authority, contains NUL or a parent-traversal component, or
contains an empty path component. `folder_paths.annotated_filepath()` then
identifies the annotation and selected root, and
`folder_paths.get_annotated_filepath()` resolves the candidate. The boundary
verifies real-path containment against that corresponding real ComfyUI root:
`folder_paths.get_input_directory()`, `folder_paths.get_output_directory()`, or
`folder_paths.get_temp_directory()`. It never forces an annotated output or
temp value into the input root.

A winning path must be a regular file. Lexical failure, containment escape, and
nonregular winners fail under the exact HTTP matrix below; symlinks cannot
escape the annotation-selected root. Route security therefore consists of
lexical validation plus real-path containment in the annotation-selected root,
not input-only narrowing. Automatic `.score.json` and `.mid` sidecars remain
beside the resolved ordinary audio path regardless of which accepted ComfyUI
root supplied that audio. Neither payloads, diagnostics, logs, nor ETags
disclose a raw, resolved, or normalized filesystem path.

After lexical and containment validation, `score/routes.py` passes explicit
Score resolution state to `score/runtime.py` even when the candidate is
missing. The shared explicit provider therefore remains the sole authority for
terminal missing/invalid Score behavior. The route resolves audio separately
because duration probing cannot proceed without a regular audio file.

`audio="none"` skips audio resolution and automatic candidates. An explicit
Score can still resolve, matching the node. The duration target is the node's
one-second fallback duration and the response includes
`score_audio_unavailable` with severity `warning`. With no explicit Score, the
constant provider wins. A non-sentinel missing audio returns 404; a
decode/probe failure returns 422. The node can still use its normative
one-second execution fallback, so the route error visibly records that preview
is unavailable instead of claiming preview/execution equality.

Provider/alignment parity is required whenever the route has a duration target:
a successful decoded probe or the defined `audio="none"` fallback. A missing
or undecodable non-sentinel audio selection produces no route provider winner;
the frontend stays in error while later node execution may recover audio with
silence. This is the sole documented availability exception to preview/runtime
provider parity.

### Exact HTTP matrix

| Condition | HTTP | Status | Diagnostic code |
|---|---:|---|---|
| valid nonconstant Score | 200 | `ready` | provider/resolver warnings or none |
| constant provider wins | 200 | `constant` | warnings or none |
| analysis provider reports pending | 202 | `analyzing` | analysis diagnostic or none |
| `If-None-Match` matches a current ready/constant representation | 304 | no body | none |
| missing/duplicate/unknown/malformed query or unsafe path syntax | 400 | `error` | `score_route_query_invalid` |
| non-sentinel audio or explicit Score resolves safely but is not a regular file | 404 | `error` | `score_route_file_not_found` |
| no schema-version-1 condition | 409 (unused) | no response | none |
| malformed/unsupported JSON or MIDI, terminal provider decode error, or audio probe failure | 422 | `error` | `score_provider_error` or `score_audio_unavailable` |
| unexpected server defect | 500 | `error` | `score_route_internal_error` |

HTTP 409 is intentionally unused in schema version 1. A valid stale explicit
sidecar and alignment divergence remain 200 `ready` warnings under Phase 4;
missing optional candidates continue; malformed or unsupported present
sources are 422. Inventing a conflict response would weaken those settled
provider rules.

Every 4xx/500 body is compact JSON and contains no exception class, traceback,
path, candidate filename, or server configuration. The 500 handler logs only
the exception type through the module logger.

### Transport schema version 1

Every JSON body begins with these keys in this order:

```text
schema_version
status
```

Every body ends with `diagnostics`. `schema_version` is the integer `1`.

The ready payload key order and types are:

```text
schema_version: 1
status: "ready"
ticks_per_quarter: positive safe integer
audio_seconds_at_tick_zero: finite number
audio_duration_seconds: finite nonnegative number
bar_starts: array of nonnegative safe integer ticks
tempos: array of Tempo objects
meters: array of Meter objects
markers: array of Marker objects
sections: array of Section objects
source: "json" | "midi" | "analyzed"
provider: "explicit" | "json_sidecar" | "midi_sidecar" | "analysis"
meter_estimated: boolean
has_variable_meter: boolean
has_midbar_meter_change: boolean
diagnostics: array of Diagnostic objects
```

Object key order is fixed:

```text
Tempo:      tick, us_per_quarter
Meter:      tick, numerator, denominator
Marker:     tick, name
Section:    name, start_tick, end_tick_exclusive, bar_aligned, confidence
Diagnostic: code, severity, message
```

`confidence` is a finite number or JSON null. `bar_starts` contains the
resolver's complete cached boundaries, including the exclusive boundary after
the last cached bar, and therefore covers the visible audio target.

Wrapper generator data, `derived_from`, `edited`, wrapper
`end_tick_exclusive`, provenance mappings, candidate paths, file statistics,
and parser-internal state are intentionally excluded. They are provider and
cache concerns, not public timing-model fields.

The constant payload has exactly:

```text
schema_version, status, audio_seconds_at_tick_zero,
audio_duration_seconds, source, provider, diagnostics
```

with `status="constant"`, `source="constant"`, and `provider="constant"`.
It directs the client to build a local `ConstantTimeAxis` from existing node
widgets and does not fabricate a Score.

The analyzing payload has exactly:

```text
schema_version, status, poll_after_ms, source, provider, diagnostics
```

with `status="analyzing"`, `poll_after_ms=1000`, `source="analyzed"`, and
`provider="analysis"`. The response also sends `Retry-After: 1`. The current
`AnalysisProvider` always returns `not_applicable`, so this response is a
reserved transport state and does not claim analysis implementation.

The error payload has exactly:

```text
schema_version, status, diagnostics
```

with `status="error"` and exactly one error-severity diagnostic. A present
invalid Score source never becomes `constant`.

The server transport code vocabulary is closed to
`score_route_query_invalid`, `score_route_file_not_found`,
`score_audio_unavailable`, `score_route_internal_error`, and the shared
provider/resolver codes emitted by `score/runtime.py`. The client adds only
`score_route_schema_invalid` and `score_tick_out_of_safe_range` when it cannot
accept a server representation. These client codes never become node output
codes.

### Decoded audio duration

Phase 5a creates `audio_duration_probe.py` with:

```python
@dataclass(frozen=True)
class AudioDurationProbe:
    sample_rate: int
    sample_count: int
    duration_seconds: float


def probe_audio_duration(filename: str) -> AudioDurationProbe: ...
```

The helper opens the first PyAV audio stream, decodes every frame, and counts
decoded samples. It never appends PCM frames, constructs a NumPy track, creates
a Torch tensor, or executes the node. Duration is exactly
`sample_count / sample_rate`, matching the Audio Integration Contract. Missing
streams, empty decoded streams, invalid rates, and decoder failures are typed
probe errors.

`score/routes.py` calls the probe through `asyncio.to_thread`. The helper is a
narrow metadata probe rather than shared node decoding: the node still needs
the actual waveform, while sharing a full decoder would either materialize
audio for the route or complicate the node's established PyAV/Torch boundary.
Both paths nevertheless select the first stream and define duration from
decoded samples. `audio_duration_seconds` is always the probe result, except
for `audio="none"`, which uses the node's exact one-second fallback duration.

### Shared ETag and bounded cache

Phase 5a extracts from `waveform_routes.py` into `route_cache.py`:

```python
class BoundedByteCache:
    get(key: Hashable) -> bytes | None
    put(key: Hashable, payload: bytes, *, logical_key: Hashable) -> None
    get_or_build(key, builder, *, logical_key) -> bytes
    clear() -> None
    snapshot() -> tuple[tuple[Hashable, ...], int]

def strong_etag(namespace: str, identity: tuple[object, ...]) -> str
def if_none_match_matches(value: str | None, etag: str) -> bool
```

The cache is an `OrderedDict` LRU protected by a lock, bounded by positive
entry and byte limits. `put()` removes stale entries with the same immutable
logical key. Builders execute outside the lock. Keys contain only immutable
finite built-ins and no mutable path object. The waveform route retains its
published limits and byte behavior by consuming this primitive. Score route
defaults are 32 entries and 8 MiB.

The Score identity contains, in order:

1. route schema version;
2. the exact nested audio identity defined below;
3. the complete Phase 4 Score dependency fingerprint;
4. raw `score_file` selection state;
5. effective preview alignment;
6. duration-probe contract version and decoder configuration;
7. provider-contract version; and
8. payload-serialization version.

The nested audio identity is exactly one of these immutable tuple states, with
the shown element order:

```python
("none", "none")

("unresolved", raw_audio)

("missing", raw_audio, normalized_path)

(
    "file",
    raw_audio,
    normalized_path,
    file_size,
    modification_time_ns,
)
```

`normalized_path` is calculated by the delivered node convention
`normcase(abspath(normpath(fspath(path))))`. Only `("none", "none")` and the
`"file"` state can produce cached `ready` or `constant` representations. The
`"unresolved"` and `"missing"` states enter the documented error handling and
are never successful cache identities. The normalized path is internal
identity data and is never exposed.

The complete Score dependency fingerprint is a separate nested identity
component; it is never flattened into or substituted for the audio identity.
The raw `score_file` state, effective alignment, and every version marker retain
the exact representations and ordering already specified above.

No file-content hash is required. Audio resolution, appearance, mutation, and
disappearance, as well as Score candidate appearance, mutation,
disappearance, priority change, alignment change, and serialization change
alter the identity. The ETag is an opaque SHA-256-based strong token namespaced
for the Score route and exposes no identity component.

The Score cache logical key is exactly `("score_route", raw_audio,
raw_score_file_or_blank)`. Alignment and dependency changes therefore replace
older payloads for the same two widget selections instead of accumulating
stale variants. The full cache key is `(logical_key, etag_identity)`.

The handler resolves and stats safe paths and computes the dependency identity
before cache access. It checks `If-None-Match` before payload-cache access and
before duration probing or parsing. Only ready and constant payloads are
cached/revalidated. Analyzing and error responses are not cached. Expensive
probe, parse, resolver, and serialization work occurs through
`asyncio.to_thread` and outside the cache lock.

### Registration

`score/routes.py` exports `register_score_routes(prompt_server=None) -> bool`.
It uses a per-`PromptServer` registration marker and returns `False` after the
route is present. `__init__.py` calls `register_waveform_routes()` first and
`register_score_routes()` second. Reimporting, direct test registration, or
registering twice against the same server never duplicates a route.

Pure module imports register nothing. Package import, direct supported test
imports, and arbitrary-parent package import remain silent apart from the
existing root package registration boundary.

`score/routes.py` uses one narrow package/direct import shim for
`route_cache.py` and `audio_duration_probe.py`, matching the delivered
root-leaf import pattern. Its imports of `score.runtime` and other Score
siblings remain package-relative. No pure Score module gains a root import.

## Phase 5b JavaScript resolver

### Module ownership

- `js/score_loader.js` owns URL construction, HTTP/ETag behavior, payload
  validation, loader state, generations, cancellation, and analysis polling.
- `js/score.js` owns immutable Score normalization and the complete resolver
  mathematics. It imports no DOM, ComfyUI app, API client, loader, or renderer.
- `js/time_axis.js` owns `ConstantTimeAxis`, `ScoreTimeAxis`, the public facade,
  boundary enumeration, actual-time snapping, and canonical range conversion.
  It imports pure helpers from `js/score.js` and compatibility arithmetic from
  `js/musical_grid.js`.

`js/musical_grid.js` remains the independently testable constant-compatibility
implementation. UI code stops calling its linear-index functions directly
after Phase 5d.

### Model and resolver parity

`normalizeScorePayload(payload)` validates schema version, exact object and
array shapes, booleans, finite values, event ordering, and every integer tick.
All tick-domain numbers, TPQ, bar starts, event values, and derived boundary
arithmetic must remain `Number.isSafeInteger`. An unsafe input or unsafe
derived result raises `ScorePayloadError` with code
`score_tick_out_of_safe_range`; it never rounds, truncates, converts through
BigInt, or approximates the display.

`createScoreResolver(normalizedReadyPayload)` mirrors Python for effective
tempo/meter events, last-event-at-tick ownership, piecewise
`tickToSeconds`/`secondsToTick`, bar lookup and extrapolation, boundary and
inside-bar meter changes, shortened bars, `meterAtBar`,
`positionToTick`/`tickToPosition`, absolute half-away rounding, over-fine-grid
rejection, half-open Sections, deterministic overlap selection, and absolute
alignment conversion. It also exposes `containingBarTicks(tick)` and
`containingBeatTicks(tick)` for golden parity. Raw payload arrays are frozen
and never mutated.

## TimeAxis facade

`js/time_axis.js` exports:

```javascript
createConstantTimeAxis(configuration)
createScoreTimeAxis(normalizedReadyPayload)
```

Both return a frozen object with this exact surface:

```text
kind                                      -> "constant" | "score"
positionToTick(position, spb)             -> safe integer tick
tickToPosition(tick, spb)                 -> {bar, beat, subdivision}
tickToScoreSeconds(tick)                   -> finite number
scoreSecondsToTick(seconds)                -> finite number
tickToAudioSeconds(tick)                   -> finite number
audioSecondsToTick(seconds)                -> finite number
barToTick(bar)                             -> safe integer tick
meterAtBar(bar)                            -> {numerator, denominator}
enumerateVisibleBoundaries(visibleRange, spb)-> Boundary[]
sectionAtTick(tick)                        -> Section | null
durationToCanonicalEnd(start, duration, spb)-> DurationEnd
snapToBar(audioSeconds)                    -> SnapResult
snapToBeat(audioSeconds)                   -> SnapResult
snapToSubdivision(audioSeconds, spb)       -> SnapResult
snapToVideoFrame(audioSeconds, fps)        -> SnapResult
audioRangeToCanonicalSelection(selectionRange, spb)-> CanonicalSelection
canonicalSelectionToAudioRange(value, spb) -> AudioRange
```

Arguments and returns are exact:

```text
position = {bar: safe positive integer, beat: safe positive integer,
            subdivision: safe nonnegative integer}
visibleRange = {startAudioSeconds: finite number,
                endAudioSeconds: finite number}
selectionRange = {startAudioSeconds: finite number,
                  endAudioSeconds: finite number,
                  nonEmptyIntent: boolean}
duration = {bars: safe nonnegative integer, beats: safe nonnegative integer,
            subdivisions: safe nonnegative integer}
Boundary = {kind: "bar" | "beat" | "subdivision", tick: safe integer,
            audioSeconds: finite number, position: position}
SnapResult = {audioSeconds: finite number, tick: safe integer | null,
              position: position | null}
DurationEnd = {endExclusive: position, startTick: safe integer,
               endTickExclusive: safe integer, meterStable: true}
CanonicalSelection = {start: position, endExclusive: position,
                      startTick: safe integer, endTickExclusive: safe integer,
                      expandedToOneSubdivision: boolean}
AudioRange = {startAudioSeconds: finite number, endAudioSeconds: finite number,
              durationSeconds: finite nonnegative number,
              startTick: safe integer, endTickExclusive: safe integer}
```

`enumerateVisibleBoundaries()` accepts a half-open audio range and returns all
canonical boundaries intersecting it, sorted by `(audioSeconds, kind)` with
kind order bar, beat, subdivision. A tick represented by a stronger boundary
appears once. It extrapolates beyond transported `bar_starts` from the final
meter anchor. Density reduction is a renderer operation over this result and
never removes snapping candidates.

`ConstantTimeAxis` preserves the historical floating-point grid, signed
pre-downbeat indices, and exact half-away behavior. Its synthetic tick methods
exist only to satisfy the facade and never replace legacy selection arithmetic.
`ScoreTimeAxis` uses safe integer ticks and the complete resolver. After axis
construction, UI code never reads raw tempo or meter arrays.

`durationToCanonicalEnd()` mirrors the normative absolute duration fallback.
It accepts `{bars, beats, subdivisions}` as nonnegative integer quantities,
allows every tempo shape, returns only a meter-stable boundary, and throws
`score_end_position_required` when a meter change lies strictly inside. The
JavaScript golden tests therefore verify the same fallback boundaries as
Python even though ready frontend commits persist exact ends.

### Snapping domain and tie policy

Score Bar, Beat, and Subdivision snapping is nearest in audio seconds:

1. convert pointer audio seconds to a Score-relative tick neighborhood;
2. enumerate the adjacent canonical boundaries of the requested strength;
3. convert each candidate through the piecewise map and absolute alignment;
4. compare absolute audio-seconds distance; and
5. on an exact distance tie, select the later audio-time boundary.

The later-boundary tie is the positive canonical-grid equivalent of
half-away-from-zero. Constant axes retain their historic signed half-away tie,
including the earlier negative boundary on a negative half tie.

For Score axes, a pointer before tick zero snaps to tick zero because negative
canonical Score positions do not exist. Tick zero maps exactly to itself. A
midpoint after tick zero chooses the following boundary. Tempo-event ticks do
not become grid candidates unless they coincide with the requested canonical
boundary. Meter-event and shortened-bar ticks are canonical bar boundaries and
own the following interval. Extrapolation uses the final meter anchor beyond
the transported table. Video Frame snapping remains
`roundHalfAwayFromZero(audioSeconds * fps) / fps` and does not consult ticks.

### Canonical range conversion

Score range conversion snaps each audio boundary with Subdivision policy,
rejects an end before the start, and stores the end as exclusive without
subtracting a subdivision. If `nonEmptyIntent` is true and both boundaries
quantize to one tick, the end advances to the next valid canonical subdivision
boundary. If it is false, an equal start and end remains a valid empty range.
Constant conversion delegates to the historical linear functions and returns
the equivalent facade shapes.

## Route-loader state machine

`createScoreLoader()` has exactly these states:

```text
idle, loading, ready, constant, analyzing, error
```

State contains only serializable/frozen request identity, payload or axis,
diagnostics, and public status. It contains no `Response`, Promise,
`AbortController`, timeout handle, or exception object.

Each load increments a generation, aborts the preceding request, and prevents
an older success, error, body decode, or polling result from publishing. The
loader validates HTTP status, schema version, payload status, exact required
keys, and safe integer domains before it constructs a `TimeAxis`. Malformed
payload becomes `error`; route errors never become `constant`.

The loader retains one ETag and last successful ready/constant payload per
exact request key. It sends `If-None-Match`; a 304 reuses that payload. A 304
without a matching retained payload performs one unconditional reload and
turns a second 304 into a protocol error.

Analyzing schedules one poll after `poll_after_ms`. Continued analyzing
responses back off by factors of two up to 10,000 ms. There is never more than
one request and one timer. Polling stops on generation change, node removal,
loader disposal, or release of the dialog's polling lease when no node-view
lease remains.

An analyzing state displays a visibly labeled provisional local Constant
ruler. Seconds editing and Video Frame snapping remain available; Musical
Score commits and Score Bar/Beat/Subdivision snapping are disabled. Error
retains waveform display and Seconds editing but disables Score authority.
Constant constructs a fully editable local `ConstantTimeAxis` and implies no
warning.

## Shared Python/JavaScript golden corpus

Phase 5b creates `tests/fixtures/score_timing_golden_v1.json`. Its root shape
is exact:

```text
schema_version: 1
floating_tolerance: 1e-9
cases: array of {
    name: string,
    score_fixture: repository-relative string,
    audio_duration_seconds: finite nonnegative number,
    audio_seconds_at_tick_zero: finite number,
    subdivisions_per_beat: positive integer,
    queries: array of {operation: string, arguments: array, expected: value}
}
```

Fixture paths are relative to `tests/fixtures/`. Query operations are the
public resolver and TimeAxis method names. Integer ticks, positions, discrete
boundaries, and video frames compare exactly. Every other finite numeric result
uses the one absolute `floating_tolerance` value.

The corpus references the delivered constant 4/4, changing-meter,
midbar-tempo, midbar-meter, 31/32, unaligned-marker/Section, and truncated-track
fixtures. Its query cases add positive and negative alignment, exact and
half-away subdivision grids, over-fine rejection, exact Section/bar
boundaries, multiple tempos, extrapolation, shortened local bars/beats,
audio-seconds snapping, exact-end conversion, meter-stable fallback boundaries,
video-frame rounding, monotonicity, and unsafe-value rejection.

The JSON is hand-reviewed deterministic data. It contains no Python `hash()`,
locale output, wall-clock value, filesystem-order result, or unfixed random
input. Python reads it in `tests/test_score_golden.py`; JavaScript reads the
same file in `tests_js/test_score.mjs` and `tests_js/test_time_axis.mjs`.

## Phase 5c complete Python Score planning

After Phase 5c, every nonconstant `ScoreRepositoryResult` uses complete Score
planning. `ScoreTempoMap.supports_uniform_timing` remains available for the
delivered adapter API and its compatibility tests, but the node no longer uses
it as a Score-shape gate. `ConstantProvider` alone selects the historical
constant path.

### Internal exact-end widgets

Append these required integer widgets immediately after `score_file`:

```python
"score_end_bar": (
    "INT",
    {"default": 0, "min": 0, "socketless": True},
),
"score_end_beat": (
    "INT",
    {"default": 0, "min": 0, "socketless": True},
),
"score_end_subdivision": (
    "INT",
    {"default": 0, "min": 0, "socketless": True},
),
```

The existing required inputs remain an exact prefix through `score_file` and
the optional inputs are unchanged. `load_audio()` receives the three values in
that order after `score_file` and before `audioUI`. Phase 5c appends them and
leaves them visible as socketless low-level fallback controls because 5c does
not modify JavaScript. Their exact defaults, minimums, sentinel validation, and
backend authority are unchanged. The visible controls allow manual construction
and execution of an exact cross-meter selection before the editor is published.

Phase 5d, and not 5c, adds all three names to the custom frontend's hidden-widget
ownership. It hides them only after the shared selection model can populate or
clear the complete triplet atomically. Consequently, no intermediate release
leaves a cross-meter request with neither visible controls nor a functional
editor.

The Phase 5c smoke validation uses those visible controls to enter a valid
exact end manually and execute one cross-meter Score selection successfully. It
then verifies that a malformed mixed triplet fails with
`score_selection_range_invalid` and that an absent 0/0/0 exact end for a
duration crossing a meter change fails with `score_end_position_required`.

The sole unset state is 0/0/0. Old workflows load the declared defaults. New
nodes, constant status, and copies without a committed Score range use 0/0/0.
Mode, audio, Score, and grid transition behavior is defined under frontend
persistence below. Seconds mode and `ConstantProvider` ignore the fields for
trim authority, but malformed mixed values are never reused when Musical Score
mode becomes active.

### Pure Score selection component

Phase 5c creates `score/selection.py`. It imports only dataclasses, typing, and
package-relative Score resolver/model helpers.

```python
ScoreSelectionMode = Literal["exact", "duration"]


@dataclass(frozen=True)
class ScorePosition:
    bar: int
    beat: int
    subdivision: int


@dataclass(frozen=True)
class ScoreSelection:
    start_tick: int
    end_tick_exclusive: int
    requested_start_score_seconds: float
    requested_end_score_seconds: float
    requested_start_seconds: float
    requested_end_seconds: float
    mode: ScoreSelectionMode
    start_position: ScorePosition
    exact_end_position: ScorePosition | None
    duration_bars: int
    duration_beats: int
    duration_subdivisions: int
    subdivisions_per_beat: int


class ScoreSelectionError(ValueError):
    code: Literal[
        "score_end_position_required",
        "score_selection_range_invalid",
    ]
    message: str
```

The public entry point is:

```python
resolve_score_selection(
    resolver: ScoreResolver,
    *,
    start_bar: int,
    start_beat: int,
    start_subdivision: int,
    score_end_bar: int,
    score_end_beat: int,
    score_end_subdivision: int,
    duration_bars: int,
    duration_beats: int,
    duration_subdivisions: int,
    subdivisions_per_beat: int,
) -> ScoreSelection
```

It validates built-in integer domains and the canonical start before any
seconds conversion. A positive canonical end has priority. The exact 0/0/0
triplet selects duration fallback. Every mixed sentinel or noncanonical start
or end raises `score_selection_range_invalid`.

The duration path implements the normative absolute anchored formula. It
allows multiple tempo events, detects meter signature changes over
`[start_tick, candidate_end_tick)`, allows a change exactly at the candidate
end, and raises `score_end_position_required` only when a change lies strictly
inside. It accepts an empty interval and rejects an end before the start.
Accepted integer ticks are converted piecewise through the resolver, then the
one absolute alignment is added to produce requested audio seconds.

No resolver arithmetic is copied. The result retains exact positions and
ticks so display and local metadata never reconstruct them from rounded
seconds.

### Neutral sample-plan refactor

`audio_clip_plan.py` remains Score-free and introduces these frozen values:

```python
@dataclass(frozen=True)
class RequestedAudioRange:
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class SampleRangePlan:
    requested_start_seconds: float
    requested_end_seconds: float
    start_sample: int
    end_sample: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    start_frame: int
    frame_count: int
    clamped: bool


@dataclass(frozen=True)
class ClipTimingMetadata:
    seconds_per_beat: float
    frames_per_beat: float
    seconds_per_bar: float
    frames_per_bar: float
    musical_position: str
```

The exact neutral API is:

```python
apply_sample_range(
    *,
    requested_range: RequestedAudioRange,
    sample_rate: int,
    sample_count: int,
    fps: float,
) -> SampleRangePlan

finalize_audio_clip_plan(
    sample_plan: SampleRangePlan,
    metadata: ClipTimingMetadata,
) -> AudioClipPlan
```

`apply_sample_range()` owns the existing seconds-to-sample rounding, clamping,
reversed-range handling, non-empty one-sample expansion, final-source-sample
fallback, returned-time calculation, and frame calculation. It is the only
sample-boundary implementation.

`create_audio_clip_plan()` keeps its current signature and callers. Its
constant/uniform compatibility wrapper resolves legacy requested timing and
display metadata, calls `apply_sample_range()` once, and finalizes the existing
`AudioClipPlan`. Its historical numerical and string behavior stays
bit-for-bit compatible.

The complete Score node path resolves `ScoreSelection` or a Seconds
`RequestedAudioRange` before entering the sample layer, calls
`apply_sample_range()` exactly once, computes Score metadata from the returned
`SampleRangePlan`, and calls `finalize_audio_clip_plan()`. It never invokes
`ConstantTempoMap` to obtain boundaries and never labels a Musical Score
request as public Seconds mode. The waveform is sliced only after successful
finalization.

### Musical mode

For a resolved nonconstant Score, `resolve_score_selection()` runs before
sample planning. Exact end wins; otherwise meter-stable duration fallback
applies. Any tempo count is valid. Exact integer start and exclusive-end ticks
survive through node metadata.

`ScoreSelectionError` becomes one `ScoreDiagnostic` with severity `error` and
is raised as `ValueError(serialize_diagnostics((diagnostic,)))`. There is no
sample-plan call, waveform slice, output tuple, constant fallback, or silence
substitution after that error.

The historic warning codes `score_start_position_not_canonical` and
`score_activation_refused` remain defined for the delivered Phase 4 bridge and
old tests. The complete path emits neither as a fallback mechanism.
Structural start failures use `score_selection_range_invalid`.

### Seconds mode

For a resolved nonconstant Score, `start_time` and `end_time` remain trim
authority and `score_end_*` does not affect the request. The same Score remains
the complete nearest-position, Section, local-metric, ruler, and snapping
authority. There is no uniform gate and no neutral fake start position.

Legacy BPM, meter, FPS, grid, and alignment inputs retain their existing
validation. BPM and meter widgets do not supply active Score timing.

### Shared start anchor and local intervals

`score/selection.py` exports:

```python
selection_start_score_tick(
    resolver: ScoreResolver,
    *,
    edit_mode: Literal["Seconds", "Musical"],
    clamped: bool,
    requested_start_seconds: float,
    returned_start_seconds: float,
    musical_start_tick: int | None,
) -> int | float
```

It returns the exact `musical_start_tick` for active, unclamped Musical Score
selection; otherwise it converts returned start seconds when clamped and
requested start seconds when unclamped. The same one result controls
`section_name`, `seconds_per_beat`, `frames_per_beat`, `seconds_per_bar`, and
`frames_per_bar`.

`ScoreResolver` adds:

```python
containing_bar_ticks(tick: int | float) -> tuple[int, int]
containing_beat_ticks(tick: int | float) -> tuple[int, int]
```

Each returns integer `[start_tick, end_tick_exclusive)` boundaries. Exact
boundaries belong to the following interval. `containing_beat_ticks()` anchors
absolute beat boundaries at the actual bar start and clips the final beat at a
shortened bar end. Both extrapolate through existing bar logic and duplicate
no tempo conversion.

For a negative pre-roll anchor, both methods extend the tick-zero meter
backward in absolute whole Bar/Beat intervals anchored at tick zero. These
timing-only negative intervals have no Bar 0 label and are never accepted by
canonical position APIs. They let the same negative anchor control local
metrics while Section lookup correctly returns no Section. The initial tempo
event likewise extends before tick zero under the delivered piecewise resolver
rule.

Local seconds are the resolver's piecewise `tick_to_seconds(end) -
tick_to_seconds(start)`. Frame metrics multiply by effective FPS and remain
floating point. No widget BPM, global tempo event, selection average, or
rounded sample duration supplies them.

### Outputs and exact display text

All twenty output types, names, positions, and public meanings remain. `bpm`
and `fps` retain current effective execution values. `score_format` and
`score_provider` are established by repository resolution before selection;
on a fatal selection no output tuple exists, but the internal winning identity
is not replaced by constant authority.

Constant and delivered uniform-duration Musical text remains exactly:

```text
Bar {start_bar} · Beat {start_beat} · Subdivision {start_subdivision} | Length: {duration_label} | Time: {start_seconds:.3f}–{end_seconds:.3f} s | Frames: {start_frame}–{frame_end}{clamp_suffix}
```

The complete Score duration-fallback path uses the same string because the
duration fields are its authority. Exact-end Musical requests use exactly:

```text
Bar {start_bar} · Beat {start_beat} · Subdivision {start_subdivision} | End (exclusive): Bar {score_end_bar} · Beat {score_end_beat} · Subdivision {score_end_subdivision} | Time: {start_seconds:.3f}–{end_seconds:.3f} s | Frames: {start_frame}–{frame_end}{clamp_suffix}
```

The two delivered Seconds forms remain byte-for-byte unchanged:

```text
Seconds mode | Nearest: Bar {bar} · Beat {beat} · Subdivision {subdivision} | Time: {start_seconds:.3f}–{end_seconds:.3f} s | Frames: {start_frame}–{frame_end}{clamp_suffix}

Seconds mode | Nearest: {count} subdivisions before Bar 1 · Beat 1 | Time: {start_seconds:.3f}–{end_seconds:.3f} s | Frames: {start_frame}–{frame_end}{clamp_suffix}
```

Exact-end text never displays stale duration values. No display string embeds
diagnostics, provenance, or filesystem identity.

## Phase 5d frontend integration

### Shared runtime ownership

Phase 5d creates `js/selection_model.js`. One
`node._musicalAudioScoreRuntime` owns the Score loader, current `TimeAxis`,
route state, selection model, generation, consumer leases, and exact-end
persistence transaction. The node view and dialog subscribe to this object.
They do not create parallel loaders, resolvers, axes, selection conversions,
or snapping implementations.

The pure module exports this exact surface:

```text
createSelectionModel({timeAxis, subdivisionsPerBeat}) -> SelectionModel
SelectionModel.getSelection()                         -> SelectionState
SelectionModel.setAudioRange(range)                   -> SelectionState
SelectionModel.setCanonicalRange(selection)           -> SelectionState
SelectionModel.snapBoundary(seconds, mode, fps)       -> SnapResult
SelectionModel.commit(node, status)                   -> boolean
SelectionModel.invalidate(reason)                     -> SelectionState
commitWidgetTransaction(node, writes, reason)         -> boolean
```

`SelectionState` is frozen and contains `startAudioSeconds`,
`endAudioSeconds`, `startTick`, `endTickExclusive`, `start`, `endExclusive`,
`nonEmptyIntent`, and `valid`; tick/position fields are null for a constant
seconds-only state. `setCanonicalRange()` is available only with a Score axis.
`commit()` applies the state matrix and returns false without writes when the
current status is not editable.

The existing `node._musicalAudioTransport` remains the only media clock. The
dialog receives the facade, not `audioEl`. The waveform peak loader and
pyramid remain node-local and shared; the dialog does not issue a duplicate
backend waveform contract.

`js/metronome.js` retains all delivered constant helpers and adds:

```text
enumerateMetronomeBeats(timeAxis, startAudioSeconds, endAudioSeconds)
    -> {audioSeconds, bar, beat, downbeat}[]
```

The Score implementation enumerates actual Beat boundaries through
`TimeAxis`; the constant implementation preserves current global-beat
arithmetic. The scheduler never reads `secondsPerBeat` for a Score axis.

### Time-proportional ruler

The x-axis is audio seconds. Every ruler and Section x-coordinate is computed
from `TimeAxis` audio seconds. Tempo changes therefore change bar width; meter
changes change beat count and widths; shortened bars are visibly shorter.
Equal-width Score bars are forbidden.

The UI requests all boundaries for the visible seconds range. Every visible Bar
boundary is rendered, and every visible Section boundary and band is rendered.
Bar and Section rendering is exempt from the minor-line limit. Beat candidates
must first satisfy the settled 6-CSS-pixel spacing and Subdivision candidates
the settled 4-CSS-pixel spacing. At most 2,000 combined Beat and Subdivision
lines are drawn; 2,000 is not a total rendered-line cap.

If the combined minor candidates exceed 2,000, all Subdivision candidates are
omitted before any Beat candidate is removed. If the ordered visible Beat
sequence alone has `n > 2000` candidates, retain its first and final candidates
and stride the interior deterministically. With `budget = 2000`, the positive
stride is `ceil((n - 2) / (budget - 2))`; retain interior indices
`1 + k * stride` while they remain below `n - 1`. Thus no more than 2,000 Beat
lines are drawn, and whenever any Beat lines are drawn the first and final
visible Beat candidates are retained. For `n <= 2000`, retain every Beat
candidate and include Subdivision candidates only when the combined count did
not exceed the budget.

Labels retain Bar 1 and then use the smallest positive bar stride that provides
at least 48 CSS pixels between labels. Density reduction affects drawing only;
it never changes the full boundary enumeration used by snapping, selection, or
persistence. Snapping continues to call the full adjacent candidate methods on
`TimeAxis`.

Canonical ruler labels use the plain positive decimal Bar number without a
prefix. Examples are `1`, `9`, `10`, `11`, `12`, `13`, and `14`; visual ruler
labels never use `B1`, `B9`, or `B10`. This plain number is only the visual
ruler label. Internal APIs, model fields, diagnostics, backend display strings,
and prose continue to use the term Bar. Sections render as a band beneath the
ruler from exact half-open start/end audio times. Unaligned Sections are not
moved. Pre-roll before tick zero remains ordinary waveform seconds and is
visually muted without hiding audio.

### Exact selection persistence

`selection_model.js` exports
`commitWidgetTransaction(node, writes, reason)`. It increments a node-local
callback-suppression depth, performs writes, decrements in `finally`, then
issues exactly one refresh and one graph-dirty notification. Widget callbacks
do not observe or execute a partially written authority state.

At the 5d boundary, `js/musical_audio_ui.js` adds `score_end_bar`,
`score_end_beat`, and `score_end_subdivision` to `HIDDEN_WIDGETS` only after
installing this shared transactional selection model. From that publication
onward the editor exclusively owns their atomic population and clearing; before
5d the 5c fallback widgets remain visible.

For `ready` Musical commits, the transaction order is:

1. write `score_end_bar=0` to deactivate any previous exact end;
2. write canonical start fields;
3. write deterministic compatibility duration fields;
4. write `score_end_beat` and `score_end_subdivision`;
5. write positive `score_end_bar` last, activating the new exact end.

The exact end is always backend authority. When start and end lie in one
meter-stable interval, compatibility duration fields are the nonnegative
overflow-normalized quantity under the start meter. With start meter numerator
`n`, calculate:

```text
count = ((end.bar - start.bar) * n + (end.beat - start.beat)) * spb
        + end.subdivision - start.subdivision
duration_bars, remainder = divmod(count, n * spb)
duration_beats, duration_subdivisions = divmod(remainder, spb)
```

The transaction is rejected if `count` is negative. When a meter change lies
strictly inside, all three duration fields are written as zero and the UI
labels them `Exact end authority`; they do not claim to describe the range.

For `constant`, the transaction clears end state in order end bar, end beat,
end subdivision and writes legacy start/duration fields. Entering Seconds mode
clears the exact triplet immediately in that order and then writes
`start_time`, `end_time`, and compatibility `duration`; Score exact-end state
cannot regain authority on a later mode switch.

Entering Musical mode from Seconds under `ready` quantizes both boundaries
through the current Score axis and atomically writes canonical start and
exclusive end. Under `constant`, it uses legacy conversion and keeps 0/0/0.
Loading, analyzing, error, and idle do not permit a Musical commit.

An audio change, nonblank or blank `score_file` change, or
`subdivisions_per_beat` change first clears the exact triplet synchronously,
increments the route generation, and disables Musical writes until the new
state is ready or constant. Old workflows without end widgets, new nodes, and
copied legacy nodes normalize to 0/0/0 during configure. Copied current nodes
retain a valid atomic triplet and receive their own runtime and loader.

Selections are half-open for drag, keyboard, snapping, and mode conversion. A
non-empty pointer drag that collapses to one boundary expands to the next
valid canonical boundary. An explicit zero-length action remains equal-start/
end. End-before-start is prevented. The stored exclusive end is never reduced
by one subdivision.

### State and editability matrix

| State | Ruler source | Waveform | Seconds edit | Musical edit | Bar/Beat/Sub snap | Frame snap | Widget writes | Visible state | Retry/poll | Execution expectation |
|---|---|---|---|---|---|---|---|---|---|---|
| `idle` | plain seconds scale | independent loader/no audio | enabled | disabled | disabled | enabled when FPS/duration valid | Seconds only | `Score idle` | load on valid identity | backend remains authoritative; UI claims no Score |
| `loading` | plain seconds scale | retained/current waveform | enabled | disabled | disabled | enabled | Seconds only | `Loading Score…` | one in-flight generation | execution can differ; UI labels pending |
| `ready` | `ScoreTimeAxis` | available independently | enabled | enabled | enabled | enabled | exact Score transaction or Seconds transaction | source/provider and warnings | ETag revalidation | complete Score authority after 5c |
| `constant` | `ConstantTimeAxis` | available independently | enabled | enabled | legacy constant snap | enabled | legacy fields, end 0/0/0 | `Constant timing` | revalidate on identity change | historical constant authority |
| `analyzing` | provisional labeled constant | available independently | enabled | disabled | disabled | enabled | Seconds only | `Analyzing · provisional ruler` | one bounded poll | current stub never enters; future execution not claimed |
| `error` | plain seconds scale | remains available | enabled | disabled | disabled | enabled | Seconds only | bounded diagnostic message | explicit retry or identity change | invalid Score execution fails; no silent constant |

No state mixes Score ruler authority with constant Musical widget writes.

### Route identity and external alignment preview

Each request uses the current audio widget, current `score_file`, and saved
local `downbeat_offset`. Audio, Score, local alignment, subdivision grid, node
configure, copy, and removal events invalidate the relevant generation.

When `downbeat_offset_input` is connected, the frontend cannot read its runtime
value. It sends the saved local fallback, continues showing the delivered
`External timing · preview uses local values` notice, and never claims preview
equals execution. The node's effective connected value remains authoritative.

### Dialog lifecycle

Opening the dialog acquires a consumer lease on the node runtime, subscribes to
route/axis/selection and transport snapshots, reuses an existing in-flight or
ready route load, and mounts one editor component through
`showExtensionDialog`. It creates no hidden node.

`js/waveform_editor_dialog.js` replaces
`WAVEFORM_EDITOR_SPIKE_COMPONENT` with
`MUSICAL_AUDIO_WAVEFORM_EDITOR_COMPONENT`, keeps
`openWaveformEditorDialog()` as the public adapter, and passes exactly
`nodeId`, `sourceLabel`, and `runtime` props. The component mounts from those
public props and never looks up the graph or a DOM node by identifier.

The node owns audio transport, waveform peak state, and route state. The dialog
owns its DOM, viewport, ruler canvas, selection draft, and dialog-local render
handle. The shared TimeAxis owns all conversions and snaps. The transport owns
playback time; node and dialog playheads subscribe to it.

Commit atomically writes the shared selection model and widgets. Cancel drops
only the dialog draft. Close unsubscribes, releases its polling lease, cancels
dialog animation and resize work, and leaves node transport/runtime alive.
Node removal closes the dialog, disposes loader and polling, destroys both
render consumers, then destroys playhead, metronome, waveform loader, and audio
transport once. Reopening creates no duplicate `AudioContext`, interval,
poller, subscription, or animation loop.

### Waveform editor modal visual contract

The Phase 5d waveform editor is a dark editor card over a dimmed graph overlay.
It mounts through the existing extension-dialog service and consumes the shared
per-node runtime defined above. Its vertical component order is exact:

1. Header
2. Toolbar
3. Musical ruler
4. Sections band
5. Main waveform viewport
6. Overview strip
7. Status row

No component inserts a second timing axis between these bands. The card has
rounded corners, a subtle border, a restrained elevated shadow, and an opaque
editor surface. The graph behind it is dimmed but remains recognizable. The
card retains viewport margins on every side and sizes responsively without
forcing horizontal browser-page scrolling. Its preferred desktop size is
approximately 1120 by 680 CSS pixels, bounded by the available viewport. When
space grows, the main waveform expands vertically before any fixed band. When
space contracts, it absorbs the reduction before the header, toolbar, rulers,
overview, or status row compresses.

The modal uses existing theme and accent variables. Component-specific CSS
variables are introduced only where the existing palette has no matching
semantic role.

#### Header

The header order from left to right is exact:

1. source filename;
2. persistent active-timing badge;
3. flexible spacer; and
4. close button.

The filename is visually prominent and begins at the header's normal left
padding. There is no separate decorative waveform icon and no replacement
decorative symbol in that space; waveform iconography elsewhere in the node and
application is unchanged. A representative header is:

```text
velvet-lies.wav
midi_sidecar · 120 bpm · 4/4
```

The filename truncates with an ellipsis when necessary and exposes its full
filename value in a tooltip. It never exposes a resolved path. The persistent
badge always identifies the timing authority represented by the current modal
state. Its content policy is exact:

| State and Score shape | Badge text |
|---|---|
| ready Score, one effective tempo and constant meter | `<provider descriptor> · <BPM> bpm · <numerator>/<denominator>` |
| ready Score, multiple tempos and constant meter | `<provider descriptor> · tempo map · <numerator>/<denominator>` |
| ready Score, changing meter | `<provider descriptor> · tempo map · variable meter` |
| constant | `constant · <effective BPM> bpm · <effective numerator>/<effective denominator>` |
| analyzing | `analysis · analyzing` |
| loading | `loading score` |
| error | `score error` |

Provider descriptors are exactly `explicit · <source>`, `json_sidecar`,
`midi_sidecar`, `analysis`, and `constant`; `<source>` is exactly `json`,
`midi`, or `analyzed`. The single-tempo form is used only when one effective
tempo describes the Score, and the constant-meter form is used only when one
effective meter describes it. The badge never presents one local tempo or
meter as global truth for a variable Score.

Ready and constant badges use the accent family, analyzing uses the warning
family, error uses the error family, and loading uses a muted neutral family.
The close button has an accessible label and tooltip. Escape retains its public
extension-dialog closing behavior and executes the same cleanup path.

#### Toolbar

The toolbar is one horizontal row. Its exact left-to-right grouping is:

1. jump to selection start, play/pause, and loop selection;
2. first separator;
3. zoom out, zoom in, and fit full audio;
4. second separator;
5. labeled Snap dropdown and labeled Follow dropdown; and
6. a right-aligned Layer menu.

The Snap dropdown uses exactly the delivered node vocabulary: `Off`, `Bar`,
`Beat`, `Subdivision`, and `Video Frame`. A representative visible value is
`Snap: Beat`.

The Follow dropdown contains exactly `Off`, `Page`, and `Center`. Its behavior
is binding:

- `Off`: playback never changes the viewport.
- `Page`: when the playhead exits the visible range during forward playback,
  advance by one visible-range duration and place the playhead at the left edge
  of the new page. Reverse seeking applies the equivalent preceding-page
  behavior. Page mode never continuously pans.
- `Center`: while playback is active, keep the playhead centered except where
  the audio start or end prevents centering.

A representative visible value is `Follow: Page`. Loop repeats the current
committed selection and is disabled without a valid nonempty selection.
Jump-to-start seeks to the current selection start, or audio start when no valid
selection exists. Fit shows the entire audio duration and updates the shared
overview viewport.

The Layer menu is functional in Phase 5d and independently controls visibility
of the musical ruler, Sections band, overview strip, and diagnostics indicator.
It is the future extension point for the Score Inspector, but Phase 5d contains
no dead `Score Inspector` action and claims no inspector behavior. Every
icon-only toolbar button has an accessible label and tooltip.

#### Musical ruler

The musical ruler is a narrow muted band. It displays canonical Bar numbers in
monospace, vertical Bar boundaries, and optional Beat and Subdivision
boundaries under the settled density policy. A representative visible range
shows labels such as:

```text
9  10  11  12  13  14
```

These are plain positive decimal labels without a `B` prefix. Bar 1 remains
visible when its boundary is in range; subsequent label stride, boundary
enumeration, and candidate enumeration follow the settled density policy.

Its typography is subdued relative to the waveform. Bar boundaries are
visually stronger than Beat boundaries, and Beat boundaries are stronger than
Subdivision boundaries. The ruler remains time-proportional and never spaces
Bar labels evenly merely to fill the viewport.

#### Sections band

The Sections band sits directly below the musical ruler and uses the same
audio-seconds axis. Each Section is one exact half-open colored block.
Representative colors place `Verse 2` in a teal family and `Chorus 2` in a
violet family. Labels remain legible within their blocks: text uses a dark tone
from the same family when contrast permits and otherwise uses the theme's
high-contrast foreground.

One deterministic Section palette keeps the same Section name the same color
throughout one loaded Score state. Adjacent Sections share their exact boundary
without visual overlap or gap. Unaligned Sections retain their exact audio
positions. The band is display-only in Phase 5d and implies neither Section
editing nor persistence.

#### Main waveform viewport

The main waveform is the dominant vertical region and renders the delivered
waveform-bar pattern across the visible audio range. Selection presentation has
three coordinated parts:

1. a translucent blue selection-region fill;
2. stronger blue start and exclusive-end boundary handles; and
3. a second waveform rendering pass clipped to the selection and using the blue
   selection-waveform color.

The selected waveform bars themselves therefore change color; a blue rectangle
over an otherwise unchanged waveform is insufficient. Both waveform passes use
the same source pyramid, shared viewport, and render frame, so the clipped pass
is not a second fetch, decoder, consumer loop, or viewport model. The exclusive
end handle represents the stored exclusive boundary and is never drawn one
subdivision earlier.

The playhead is a thin orange vertical line drawn over ruler-adjacent content,
Sections, and waveform where appropriate. It remains visually distinct from
the blue selection handles, derives from the one shared transport clock, and is
never a dialog-local media position. A representative design may place it near
47 percent of the visible width, but actual placement always follows transport
time. Pre-roll remains visible and muted rather than clipped away.

#### Overview strip

The overview is a shallow full-audio waveform strip on a darker surface. A
light outlined viewport rectangle represents the main visible range. Dragging
that rectangle pans the main viewport; clicking outside it recenters the
viewport around the clicked audio time; zoom changes resize it; and Fit expands
it to the full strip. Transport following moves the same rectangle without
creating a second viewport state.

The overview uses the same audio-seconds axis and source duration as the main
waveform. It performs no independent Score conversion and owns no independent
viewport range.

#### Status row

The status row uses monospace for timing values. Its left side displays the
current committed selection in German selection prose. A meter-stable
duration-authority Musical request uses exactly:

```text
Start 11 · 1 · 0 (21.000 s)   Länge 2 Takte (4.000 s)
```

The Bar quantity is `1 Takt` or `<n> Takte`. Residual musical quantities retain
the technical terms `1 Beat` or `<n> Beats` and `1 Subdivision` or
`<n> Subdivisions`. Multiple musical quantities are joined with ` + `.

An exact-end Musical request uses:

```text
Start 11 · 1 · 0 (21.000 s)   Ende 13 · 1 · 0 exklusiv (4.000 s)
```

The exact-end form never displays stale duration fields as authority. The
seconds value following its exact end is the selected duration, not the
absolute end time; the absolute end time remains available through the tooltip
or expanded status details. Seconds mode uses:

```text
Start 21.000 s   Ende 25.000 s   Länge 4.000 s
```

Spacing or responsive layout separates the complete `Start` and `Länge`/`Ende`
groups; the modal does not insert a grammatical middle-dot separator between
those groups. Technical toolbar vocabulary remains exactly `Fit`, `Snap`,
`Follow`, `Layer`, `Off`, `Bar`, `Beat`, `Subdivision`, `Video Frame`, `Page`,
and `Center`. Provider names and technical badge terms remain exactly
`explicit`, `json_sidecar`, `midi_sidecar`, `analysis`, `constant`, `tempo map`,
and `variable meter`. This German policy applies only to Phase 5d modal
selection prose and does not alter any backend `musical_position` string.

The right side displays a warning triangle for warnings or an error symbol for
errors, followed by localized singular/plural count text such as `1 Hinweis` or
`2 Hinweise`. Clicking the diagnostics indicator opens a bounded popover of the
structured diagnostic messages; diagnostics never enter the timing text. With
no diagnostics, the right side remains visually quiet and shows no zero badge.

#### Interaction and state behavior

The complete modal consumes exactly one Score loader, one `TimeAxis`, one
selection model, one waveform state, and one audio transport. It creates no
duplicate route request, waveform fetch, `AudioContext`, metronome, animation
loop, viewport state, or playhead clock.

In loading, analyzing, and error states, the layout remains stable and
unavailable controls are disabled in place instead of removed. The header badge
and status row explain the state, while waveform availability follows the
existing state matrix. In constant state, the same modal structure remains;
the Sections band is empty or hidden through the Layer menu, the ruler uses
`ConstantTimeAxis`, and the UI implies no exact-end Score authority. In ready
state, the badge names the actual provider, Score ruler and Sections align with
the waveform, and Musical selection commits persist canonical exclusive ends.

### Manual visual validation gate

Phase 5d remains uncommitted until the user confirms all of these in the
installed browser UI:

- constant 4/4;
- constant meter with several tempos;
- meter change on a bar boundary;
- shortened bar from a mid-bar meter change;
- 31/32 or another odd meter;
- negative alignment and pre-roll;
- exact Section boundary ownership;
- explicit `score_file` overriding automatic sidecars;
- invalid explicit Score;
- idle, loading, ready, constant, analyzing, and error presentation;
- Seconds to Musical and Musical to Seconds transitions;
- dialog close and reopen;
- node copy and deletion;
- workflow save and reload;
- external timing connection preview notice;
- clamped selection;
- selection at EOF;
- explicit zero-length request;
- header beginning with the filename at normal left padding and no unused
  decorative icon occupying header space;
- filename truncation with the full filename tooltip;
- accurate provider badges for single-tempo, multi-tempo, variable-meter,
  constant, loading, analyzing, and error states;
- the exact component order, toolbar grouping, and both separators;
- the representative `Snap: Beat` and `Follow: Page` controls and Page
  behavior;
- musical-ruler, Sections-band, overview-strip, and diagnostics-indicator Layer
  toggles, with no Score Inspector action;
- plain decimal Bar labels `9` through `14`, not `B9` through `B14`, in a
  suitable time-proportional viewport;
- deterministic teal/violet Verse/Chorus Section presentation;
- selected waveform bars visibly changing to blue through the clipped waveform
  pass;
- distinct blue selection handles and orange transport-owned playhead;
- overview dragging, click-to-recenter, zoom resizing, and Fit;
- meter-stable status displaying `Länge 2 Takte`;
- cross-meter exact-end status displaying `Ende … exklusiv`;
- Seconds-mode status displaying `Start`, `Ende`, and `Länge`;
- singular `1 Hinweis` and plural `2 Hinweise` indicators plus the bounded
  diagnostic popover;
- responsive modal sizing and viewport margins without page-level horizontal
  scrolling;
- browser zoom and node resize;
- keyboard focus indicators;
- Escape and close-button cleanup;
- a heavily zoomed-out range where every Bar and Section remains rendered, the
  2,000-line limit applies only to combined Beat/Subdivision lines, and reduced
  drawing density leaves snapping candidates unchanged; and
- absence of duplicate polling, audio contexts, subscriptions, or animation
  loops.

Because the delivered `AnalysisProvider` always returns `not_applicable`, the
otherwise unreachable `analyzing` row is validated with a temporary browser
development-tools network override for the Score route. The override returns
HTTP 202, `Retry-After: 1`, and exactly this schema-version-1 body:

```json
{"schema_version":1,"status":"analyzing","poll_after_ms":1000,"source":"analyzed","provider":"analysis","diagnostics":[]}
```

This validation-only override is browser-local, is not a repository file, is
never committed, does not alter `AnalysisProvider`, and is removed before
publication. While it is active, the manual gate verifies the provisional ruler
label, disabled Musical commits, enabled Seconds editing, one outstanding poll
at a time, bounded backoff, and complete request/timer cleanup on dialog close,
node removal, and audio/Score/alignment identity change.

Publication occurs only after that report.

## Import and dependency architecture

Python dependencies are binding:

```text
score/model.py
   ^       ^
   |       +-- score/serialize.py <- score/providers.py
   +---------- score/resolver.py <- score/tempo_map.py
                       ^                 ^
                       |                 |
              score/selection.py   tempo_map_contract.py
                       ^                 ^
                       |                 |
                 musical_audio_ui.py ----+
                    ^           ^
                    |           |
        score/runtime.py     audio_clip_plan.py
             ^                    no score import
             |
        score/routes.py <- audio_duration_probe.py
             |
         route_cache.py <- waveform_routes.py
```

`folder_paths` appears only in `musical_audio_ui.py` and `score/routes.py`.
Pure Score siblings use package-relative imports. The root compatibility shims
remain confined to already supported flat-import boundaries. Route import does
not register a route; root package initialization does. There is no circular
node/route/provider/resolver/plan dependency.

JavaScript dependencies are binding:

```text
musical_grid.js       score.js
       \                /
        \              /
           time_axis.js       score_loader.js
                \              /
                 selection_model.js
                    ^       ^
                    |       |
      musical_audio_ui.js   waveform_editor_dialog.js
                    |
    renderer / waveform loader / transport / playhead / metronome
```

`score.js`, `time_axis.js`, and `selection_model.js` are DOM- and ComfyUI-free.
The loader alone owns API transport. UI modules own rendering and widget
mutation. The dialog imports shared runtime components and copies no resolver,
selection, or snap mathematics. Constant `musical_grid.js` remains directly
testable.

## Failure policy

In the table, “planner” means `apply_sample_range()`. Constant fallback means
selection of `ConstantProvider`, not the one-second audio fallback.

| Condition | Route response | Frontend | Node | Diagnostic | Constant fallback | Silence fallback | Planner called |
|---|---|---|---|---|---|---|---|
| invalid route query | 400 `error` | `error`; Score edits off | unaffected until execution | `score_route_query_invalid/error` | no | no | route: no; node: independent |
| unknown annotation, forbidden path syntax, or containment escape | 400 `error` | `error`; Score edits off | node applies the same annotation vocabulary and rejects the same unsafe path class | `score_route_query_invalid/error` on route; existing node provider diagnostic on execution | no | no | no |
| safely resolved audio or explicit Score winner is missing or nonregular | 404 `error` | `error`; Score edits off | existing audio/provider error policy | `score_route_file_not_found/error` on route | no | audio policy only for missing audio at node execution | no on route |
| unresolved explicit `score_file` | 404 `error` | `error`; Score edits off | fatal provider error | `score_route_file_not_found/error` on route; `score_provider_error/error` on node | no | no | no |
| missing optional sidecars | continue provider chain; normally 200 `constant` or lower winner | resulting winner state | same ordered continuation | none | yes, only if constant wins | audio policy only | yes on successful execution |
| malformed automatic JSON | 422 `error` | `error`; Score edits off | fatal provider error | `score_provider_error/error` | no | no | no |
| malformed automatic MIDI | 422 `error` | `error`; Score edits off | fatal provider error | `score_provider_error/error` | no | no | no |
| stale edited sidecar | 200 `ready` | ready with warning | Score remains selected | `score_provider_error/warning` | no | audio policy only | yes |
| stale unedited automatic sidecar | provider becomes not applicable; lower winner response | lower winner state | same lower winner | none | yes if constant wins | audio policy only | yes on success |
| alignment divergence | 200 `ready` | ready with warning; preview value owns | Score active with effective node alignment | `score_alignment_divergence/warning` | no | audio policy only | yes |
| route schema mismatch | client-side protocol failure | `error`; Score edits off | unaffected until execution | `score_route_schema_invalid/error` | no | no | node: independent |
| analyzing state | 202 `analyzing` | provisional labeled constant ruler; Score commits off | current stub continues to constant | analysis diagnostic or none | preview only, not Score authority | audio policy only | execution follows actual provider result |
| JavaScript unsafe integer | payload rejected client-side | `error`; Score edits off | Python remains authoritative | `score_tick_out_of_safe_range/error` | no | no | node: independent |
| invalid canonical start | route may remain 200 `ready` | commit prevented when locally detected | fatal selection error | `score_selection_range_invalid/error` | no | no | no |
| malformed exact-end sentinel | route may remain 200 `ready` | transaction prevents; loaded corruption shown | fatal selection error | `score_selection_range_invalid/error` | no | no | no |
| invalid exact end | route may remain 200 `ready` | commit prevented when locally detected | fatal selection error | `score_selection_range_invalid/error` | no | no | no |
| end before start | route may remain 200 `ready` | prevented | fatal selection error | `score_selection_range_invalid/error` | no | no | no |
| duration crosses meter change without exact end | route remains 200 `ready` | ready UI writes exact end | fatal if old/manual request omits it | `score_end_position_required/error` | no | no | no |
| audio decode/probe failure | 422 `error` for preview | waveform may show its own error; Score edits off | existing one-second silence execution fallback | `score_audio_unavailable/error` route; `score_provider_error/warning` node | provider policy unchanged | yes, node only | yes against fallback audio if Score request valid |
| valid empty requested range | 200 ready/constant transport | explicit empty selection retained | accepted, then one source sample selected | none | no for active Score | no | yes |
| sample clamping | transport unaffected | returned range visibly clamped after execution metadata | existing clamp/non-empty behavior | none | no for active Score | no | yes, once |
| unexpected server error | 500 `error` | `error`; retry allowed | unexpected node defect remains visible | `score_route_internal_error/error` | no | no | no |

Provider or selection invalidity never becomes one-second silence. Silence is
reserved for audio availability/decode recovery after provider resolution.

## Compatibility and intentional changes

Compatibility guarantees:

- `ConstantTempoMap` numerical behavior remains unchanged.
- Existing constant workflows remain valid.
- Workflows without `score_end_*` load with 0/0/0 defaults.
- The first twenty outputs remain unchanged in order and type.
- Existing optional inputs remain unchanged.
- `score_file` blank, literal `none`, provider-priority, and invalid-stop
  semantics remain unchanged. A nonblank selection keeps the delivered node's
  annotation vocabulary: unannotated relative input, `[input]`, `[output]`, and
  `[temp]`; neither route nor node narrows it to input-only behavior.
- Ordinary audio selections use that same annotation vocabulary at the route
  and node boundary. Automatic sidecars remain beside the resolved audio in
  whichever accepted ComfyUI root supplied it; cwd-relative and audio-relative
  interpretation of an explicit `score_file` remain forbidden.
- Seconds trimming, sample rounding, clamping, one-sample expansion,
  final-source-sample fallback, and returned-time calculation remain unchanged.
- Protected nearest strings remain unchanged.
- Provider alignment is never added to widget alignment.
- No dependency or package-manager addition is required.
- A subphase claims no route or UI behavior before its own publication.

Intentional changes:

- after 5c the appended exact-end widgets are temporarily visible fallback
  controls, and 5d hides them only when the functional editor owns them;
- after 5c every valid Score is complete backend timing authority;
- noncanonical Score starts become fatal request errors instead of warning
  fallback;
- a cross-meter Musical request without exact end fails fatally;
- active Score metrics become local to the shared start anchor;
- exact-end Musical display uses the settled exclusive-end string; and
- after 5d the frontend persists canonical exclusive end positions.

## Test plan

All Python tests use `unittest`. All JavaScript tests use imports from
`node:assert/strict` and `node:test`. No test framework, transpiler, bundle
step, lockfile, or package script is added.

### Phase 5a tests

New tests:

- `tests/test_route_cache.py`: strong opaque ETags, weak comparison, immutable
  identities, the exact none/unresolved/missing/file audio tuples and tuple
  order, separate nested Score dependency fingerprints, LRU entry/byte limits,
  stale logical-key eviction, lock safety, and work outside the lock.
- `tests/test_audio_duration_probe.py`: first-stream decoded duration, WAV and
  FLAC parity, multiple frames, missing/empty stream, decoder error, and no
  full-track PCM/Torch accumulation.
- `tests/test_score_runtime.py`: shared provider order, explicit path state,
  automatic candidates, missing fingerprints, typed alignment, shared
  ProviderChainError conversion, format/provider identity, diagnostic order,
  message normalization, and serialization.
- `tests/test_score_routes.py`: query matrix; unannotated-input plus explicit
  `[input]`, `[output]`, and `[temp]` parity for audio and `score_file`;
  annotation-selected real-root containment; rejection of every forbidden path
  class and nonregular winner; schema key order and shapes;
  200/202/304/400/404/409/422/500 behavior; exact audio-identity-driven ETag
  invalidation; cache limits; duration rules; provider parity; path
  nondisclosure in payloads, diagnostics, logs, and ETags; automatic sidecars
  beside audio from every accepted root; and idempotent registration.

Modified tests:

- `tests/test_waveform_routes.py` proves byte-identical route behavior through
  `route_cache.py`.
- `tests/test_audio_change_detection.py`, `tests/test_load_audio_outputs.py`,
  `tests/test_score_node_integration.py`, and `tests/test_node_contract.py`
  prove the node consumes `score/runtime.py` without changing inputs, outputs,
  fingerprints, or execution behavior.

Import audits cover package, supported direct, and arbitrary-parent imports;
pure imports register nothing and emit no output.

### Phase 5b tests

New tests:

- `tests/test_score_golden.py` runs the Python resolver and selection boundary
  reference against `score_timing_golden_v1.json`.
- `tests_js/test_score_loader.mjs` covers URL encoding, exact payload
  validation, all six states, ETag/304, generation suppression, AbortController,
  one-poll ownership, bounded backoff, lease/dispose lifecycle, and malformed
  responses.
- `tests_js/test_score.mjs` covers normalization, safe integers, effective
  events, piecewise inversion, bar/meter resolution, shortening,
  extrapolation, Sections, and golden parity.
- `tests_js/test_time_axis.mjs` covers both axis kinds, complete facade shapes,
  constant compatibility, visible boundaries, local boundaries,
  seconds-distance snaps, exact ties, pre-roll, event boundaries,
  extrapolation, range conversion, exact exclusive end, and golden parity.

Commands are:

```text
python -m unittest tests.test_score_golden -v
node --test tests_js/test_score.mjs tests_js/test_time_axis.mjs tests_js/test_score_loader.mjs
```

### Phase 5c tests

New tests:

- `tests/test_score_selection.py`: canonical starts, all exact-end sentinel
  states, exact priority, meter-stable arithmetic, multiple tempo events,
  boundary meter change, internal meter change, empty range, reversed range,
  exact ticks/seconds, fatal codes, and frozen results.
- `tests/test_score_complete_node.py`: every Score shape, Seconds authority,
  Musical exact/fallback paths, fatal pre-plan behavior, one sample pass,
  one start anchor, Sections, local metrics, exact display, alignment,
  diagnostics, output order, and provider identity.

Modified tests:

- `tests/test_score_resolver.py` covers containing local bar/beat boundaries.
- `tests/test_audio_clip_plan.py` and
  `tests/test_audio_clip_plan_tempo_map.py` cover the neutral range API,
  compatibility wrapper, unchanged strings, rounding, clamp, and imports.
- `tests/test_load_audio_outputs.py`, `tests/test_node_contract.py`, and
  `tests/test_score_node_integration.py` cover appended widgets, legacy
  defaults, their visible socketless fallback status through 5c, unchanged
  outputs, provider behavior, and no uniform shape gate. Phase 5c leaves every
  JavaScript file unchanged.

Tests mock `apply_sample_range()` to prove zero calls on fatal requests and
exactly one call on every successful execution.

### Phase 5d tests

New tests:

- `tests_js/test_selection_model.mjs`: half-open ranges, expansion, empty
  selection, end rejection, exact-end/duration policy, clear order, write
  order, callback suppression, one refresh, mode switching, invalidation,
  copies, and saved-workflow defaults.
- `tests_js/test_musical_audio_score_ui.mjs`: loader-to-axis integration,
  state/editability matrix, time-proportional boundaries, Section overlays,
  Bar/Section exemption from the minor-line budget, the exact 2,000 combined
  Beat/Subdivision policy, deterministic Beat stride with endpoint retention,
  unchanged snap enumeration under a heavily zoomed-out range, plain decimal
  Bar labels with no `B` prefix, representative labels `9`, `10`, `11`, `12`,
  `13`, and `14` at unchanged time-proportional positions, unchanged label
  density and Bar-boundary enumeration,
  header badge text for single-tempo, multi-tempo, variable-meter, constant,
  loading, analyzing, and error states, stable modal component order, filename
  as the first header item with no decorative waveform icon, exact Snap and
  Follow choices, Loop disablement without a nonempty selection, Layer
  visibility, exact German duration-authority, exclusive-end, and Seconds-mode
  status strings, `Takt`/`Takte` and `Hinweis`/`Hinweise` inflection, unchanged
  technical toolbar and badge vocabulary, external-preview notice, shared
  runtime, and no duplicate consumers.

The modal UI tests assert these three strings exactly, including spacing:

```text
Start 11 · 1 · 0 (21.000 s)   Länge 2 Takte (4.000 s)
Start 11 · 1 · 0 (21.000 s)   Ende 13 · 1 · 0 exklusiv (4.000 s)
Start 21.000 s   Ende 25.000 s   Länge 4.000 s
```

Modified tests:

- `tests_js/test_musical_grid.mjs` retains constant compatibility after UI
  callers move to `TimeAxis`.
- `tests_js/test_metronome.mjs` covers Score beat enumeration across tempo and
  meter changes without global beat duration.
- `tests_js/test_waveform_editor_dialog.mjs` covers real mount/close,
  transport reuse, selection commit/cancel, toolbar grouping and separators,
  one shared main/overview viewport, one shared transport playhead, polling
  leases, reopen, and node removal.
- `tests_js/test_waveform_renderer.mjs` covers visible-range sharing and
  selection waveform clipping-pass ownership from the same source pyramid and
  render frame, overview projection, and confirms no second waveform fetch or
  render loop.
- `tests/test_node_contract.py` audits the 5c-visible/5d-hidden exact-end control
  boundary, shared module imports, lifecycle cleanup, and output compatibility.

These visual-contract additions do not change the Phase 5d file matrix.
`js/musical_audio_ui.js`, `js/waveform_editor_dialog.js`,
`js/musical_audio_ui.css`, the selection model and renderer, and their already
assigned dialog/UI tests own the work. No backend output or route field is
added.

Every Python subphase runs full `python -m unittest discover -s tests -v` and
the established PyAV-capable waveform suite in its existing environment. Every
JavaScript subphase runs `node --test tests_js/*.mjs` under PowerShell-expanded
file paths or the repository's explicit file list. Phase 5d additionally
requires the manual gate.

## Exact subphase file matrix

| Subphase | Files created | Files modified | Files explicitly protected | Targeted tests | Manual validation | Commit boundary |
|---|---|---|---|---|---|---|
| 5a | `route_cache.py`; `audio_duration_probe.py`; `score/runtime.py`; `score/routes.py`; `tests/test_route_cache.py`; `tests/test_audio_duration_probe.py`; `tests/test_score_runtime.py`; `tests/test_score_routes.py` | `__init__.py`; `waveform_routes.py`; `musical_audio_ui.py`; `tests/test_waveform_routes.py`; `tests/test_audio_change_detection.py`; `tests/test_load_audio_outputs.py`; `tests/test_score_node_integration.py`; `tests/test_node_contract.py` | `score/model.py`; `score/resolver.py`; `score/tempo_map.py`; `audio_clip_plan.py`; `musical_timing.py`; `tempo_map_contract.py`; `js/`; `package.json`; all specifications | route cache, duration, repository parity, route, waveform regression, node regression, imports; full Python and PyAV suites | secure-path smoke check across input/output/temp roots and 304 revalidation only; no acceptance gate | one Score route/transport commit |
| 5b | `js/score_loader.js`; `js/score.js`; `js/time_axis.js`; `tests/fixtures/score_timing_golden_v1.json`; `tests/test_score_golden.py`; `tests_js/test_score_loader.mjs`; `tests_js/test_score.mjs`; `tests_js/test_time_axis.mjs` | none | all Python production; `js/musical_audio_ui.js`; `js/musical_grid.js`; `js/waveform_editor_dialog.js`; CSS; `package.json`; specifications | shared corpus, loader states, resolver parity, TimeAxis, full Python/Node suites | none | one pure JavaScript resolver/TimeAxis commit |
| 5c | `score/selection.py`; `tests/test_score_selection.py`; `tests/test_score_complete_node.py` | `score/resolver.py`; `audio_clip_plan.py`; `musical_audio_ui.py`; `tests/test_score_resolver.py`; `tests/test_audio_clip_plan.py`; `tests/test_audio_clip_plan_tempo_map.py`; `tests/test_load_audio_outputs.py`; `tests/test_node_contract.py`; `tests/test_score_node_integration.py` | `score/model.py`; `score/providers.py`; `score/serialize.py`; `score/tempo_map.py`; `musical_timing.py`; `tempo_map_contract.py`; all routes; `js/`; `package.json`; specifications | pure selection, resolver intervals, neutral sample layer, complete node, regressions, imports; full Python and PyAV suites | with the three visible fallback widgets, manually execute one valid exact cross-meter end and verify settled malformed/missing-end diagnostics; no frontend acceptance gate | one complete Python planning commit |
| 5d | `js/selection_model.js`; `tests_js/test_selection_model.mjs`; `tests_js/test_musical_audio_score_ui.mjs` | `js/musical_audio_ui.js`; `js/musical_grid.js`; `js/waveform_editor_dialog.js`; `js/metronome.js`; `js/musical_audio_ui.css`; `tests_js/test_musical_grid.mjs`; `tests_js/test_metronome.mjs`; `tests_js/test_waveform_editor_dialog.mjs`; `tests_js/test_waveform_renderer.mjs`; `tests/test_node_contract.py` | all Python production except the static node-contract test; `js/score_loader.js`; `js/score.js`; `js/time_axis.js`; `js/waveform_loader.js`; `js/waveform_peaks.js`; `js/waveform_renderer.js`; `js/audio_transport.js`; `js/playhead.js`; `package.json`; specifications | selection transactions, UI state, variable ruler, metronome, dialog lifecycle, constant grid, full Python/Node suites | complete browser checklist including the temporary HTTP-202 development-tools override and heavily zoomed-out density check; publication blocked until user acceptance and override removal | one frontend commit after manual acceptance |

No subphase stages or publishes another subphase's files. Every boundary leaves
the repository runnable and testable.

## Phase 5 acceptance criteria

- [ ] Route and node select the same provider for the same resolved path state.
- [ ] Route and node accept the same unannotated-input, `[input]`, `[output]`,
      and `[temp]` vocabulary, enforce containment in the annotation-selected
      root, and expose no path through payload, diagnostic, log, or ETag.
- [ ] Route and node use the same absolute alignment and divergence policy.
- [ ] Route transport is secure, versioned, bounded, revalidatable, and
      path-opaque.
- [ ] Score route identity uses the exact ordered none/unresolved/missing/file
      audio tuple, keeps the Score dependency fingerprint nested separately,
      applies `normcase(abspath(normpath(fspath(path))))`, and caches successful
      representations only for none/file audio states.
- [ ] Python and JavaScript discrete resolver results match the golden corpus.
- [ ] Both UI surfaces consume one node-local `TimeAxis` and selection model.
- [ ] The modal preserves the exact seven-band hierarchy, truthful timing badge,
      filename-first icon-free header, shared main/overview viewport, and
      transport-owned playhead.
- [ ] The ruler uses prefix-free positive decimal Bar labels and remains
      time-proportional in audio seconds.
- [ ] Every visible Bar and Section renders outside the 2,000 combined
      Beat/Subdivision limit, and drawing reduction leaves snap candidates
      unchanged.
- [ ] Musical snapping compares actual audio-seconds distance with the settled
      tie rule.
- [ ] Musical commits persist a canonical exclusive end atomically.
- [ ] Selection recolors the clipped waveform bars themselves, and exact-end
      status uses German `Ende … exklusiv` without presenting legacy duration
      fields as authority.
- [ ] Phase 5c leaves usable visible exact-end controls; Phase 5d hides them
      only after atomic editor ownership exists.
- [ ] Multiple-tempo meter-stable duration fallback resolves in ticks.
- [ ] Cross-meter duration without an exact end fails with the normative fatal
      diagnostic.
- [ ] Every valid Score shape controls backend planning.
- [ ] No partial Score mode exists in transport, frontend, or backend.
- [ ] Section and all four local metrics use one shared start tick.
- [ ] Successful execution performs one neutral sample-planning pass.
- [ ] Constant numbers, protected strings, sample behavior, and twenty-output
      order remain compatible.
- [ ] Phase 5d visual validation, including the reproducible temporary
      analyzing override and its removal, is reported complete before
      publication.

## Forbidden work

Phase 5 contains no analyzer algorithm, Phase 6 batch design, stems, material
handling, higher-level specification amendment, second alignment convention,
equal-width Score ruler, partial Score activation, external dependency,
package-manager change, or historical line-count contract.

Settled exact-end, meter-stable-duration, time-proportional-axis, fatal-error,
shared-anchor, local-metric, and absolute-alignment decisions are not reopened
by implementation.

## Design review conclusion

The inspected delivered code supports this dependency split without changing
frozen Score model types. The complete design is consistent with the newly
amended Musical Timing and Audio Integration specifications: Score selection
is resolved in integer ticks before one neutral sample pass; clamping alone
changes the shared start anchor; local metrics integrate actual tempo
segments; and fatal Score requests never reach constant or silence fallback.

The route's preview-only audio probe, the node's waveform decode, and the
frontend's saved local alignment have explicit boundaries. None claims an
external connected value or successful runtime decode that it cannot know.
