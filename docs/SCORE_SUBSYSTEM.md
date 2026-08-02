# Score Subsystem: Tempo Map and Markers

> **Status: Architecture frozen for implementation**
> **Revision: 1** — Repository state `604e5ab`, branch
> `feature/css-theme-foundation-v0.2.0`
>
> S1–S10 and decisions 1–6 are an implementation and test contract, not a
> roadmap. Changes to them require an explicit amendment to this
> specification rather than an implementation-time decision. The phases and
> the Waveform Editor strand
> remain plannable and may be reordered.

Architecture plan for `comfyui-musical-audio`.

## Core idea

The tempo map and markers are not two separate features—they come from the
same MIDI file, from the same parsing pass, and describe the same thing: what
the DAW knows about the time axis.

So this is one subsystem, not two features. The common denominator here is
called **Score**.

```
MIDI file ─────parse────┐
Audio analysis ─────────┼──▶ Score ──┬──▶ TempoMap    (Position ↔ seconds)
BPM + time signature ───┘            ├──▶ MeterMap    (Tick ↔ bar/beat)
                                     └──▶ Sections    (named regions)
```

Three sources, one data type. Everything behind `Score` neither knows where
the numbers come from nor should need to know.

Crossing that is a second, independent axis: **Material**. The Score says
*when* to cut; the Material says *what* to cut. A mix is one track, a stem set
is five—the time axis does not change.

```
Score      ──▶ Plan (Ticks)    ──apply──▶ Segments
Material   ──▶ Tracks (Samples) ─────────┘
```

---

## The six decisions that determine everything else

### 1. Ticks are the canonical unit, not seconds

MIDI thinks in ticks. Bar boundaries are exact integers there—a 31/32 bar is
precisely 1860 ticks, not a rounded float. Conversion to seconds happens
**once, at the outer boundary**.

Consequence: everything operates in ticks internally. Only the output
converts.

For audio analysis, the direction is reversed: it provides *only* seconds.
It must convert them back to ticks before it may produce a `Score`—see the
pitfall “Seconds are not ticks.” That is work on its side, but it keeps the
rest of the system free of special cases.

### 2. The linear subdivision index must go

This is the deepest intervention, and it is unavoidable.

Today, `musicalPositionToSubdivisionIndex` calculates
`((bar-1) * beatsPerBar + (beat-1)) * subdivisionsPerBeat + subdivision`.
That assumes `beatsPerBar` is globally constant. With a meter map, it is a
function of the bar—the formula simply becomes wrong.

Replacement: **Position ↔ Tick ↔ Seconds.** The linear index was always only
a convenience for uniform grids.

**The index exists only in the frontend, but not only in one file.**
`musical_timing.py` does not know it—it contains only
`calculate_musical_timing`, which already calculates through seconds. Python
therefore needs nothing at all for decision 2.

However, `js/musical_audio_ui.js` directly imports
`subdivisionIndexToMusicalPosition`, `subdivisionIndexToSeconds`, and
`timingGrid` and uses them to build **the ruler** (line 1611), time resolution
(1115), and selection boundaries (1151/1152). The intervention therefore
lies in `js/musical_grid.js` (305 lines) *and* at roughly half a dozen places
in `js/musical_audio_ui.js` (2391 lines).

The complete affected set:

| Function | Role |
|---|---|
| `gridShape` | **Root of the problem**—encapsulates `beatsPerBar * subdivisionsPerBeat` |
| `musicalPositionToSubdivisionIndex` | Forward |
| `subdivisionIndexToMusicalPosition` | Reverse |
| `durationFieldsToSubdivisionCount` | Duration forward |
| `subdivisionCountToDurationFields` | Duration reverse |
| `subdivisionIndexToSeconds` | Index → time |
| `musicalPositionToSeconds` | Position → time |
| `secondsToNearestSubdivision` | Time → index |
| `frameToNearestSubdivision` | Frame → index |
| `secondsRangeToMusicalSelection` | Selection forward |
| `musicalSelectionToSecondsRange` | Selection reverse |
| `timingGrid` | Configuration object carrying the shape |
| `snapToBar` / `snapToBeat` / `snapToSubdivision` | Snapping through `snapRelative` |

Fourteen functions, not six. Anyone who cleanly replaces `gridShape` with
tick-based resolution will, however, have addressed most of them—the others
are thin wrappers around it.

### 3. The bar table is precomputed

The ruler queries hundreds of positions on every repaint. Resolving through
event lists on every call is too slow.

Build `bar_start_ticks[]` once during parsing; lookup is O(1) afterward.

### 4. Backward compatibility is the test contract

`ConstantTempoMap` must pass **all existing tests unchanged**. If a test has
to be adjusted, the refactoring has gone wrong. That is the safety net for
everything else.

### 5. Score sources form a chain, not a special case

This is the decision that makes the analyzer possible later without writing
a single line of it today.

The origin of a `Score` is a **field on the data type**, and source selection
is an **ordered provider list**, not `if midi_exists:`. This costs five
minutes in Phase 2. Retrofitting it later means rebuilding discovery, the
route, node output, and the test corpus at the same time.

```python
PROVIDERS = (
    ExplicitFileProvider,   # score_file input
    SidecarJsonProvider,    # <name>.score.json
    MidiSidecarProvider,    # <name>.mid
    AnalysisProvider,       # Audio analysis      ← Stub, Phase 8
    ConstantProvider,       # BPM + time signature ← always succeeds
)
```

The first provider that supplies a `Score` wins. `ConstantProvider` is the
terminator so that the chain never returns empty and the error path does not
exist twice.

### 6. The plan is single-track; the material is multi-track

`audio_clip_plan` produces a cut plan from ticks and frames. There is **one**
such plan, regardless of how many tracks are attached. Applying it to five
stems is an output loop, not a second planning pass.

The safeguarding rule: **Sample indices are derived from the plan per track;
the plan is not recalculated per track.** For terminology, see S6—`frame`
means video frame in this project. Otherwise, a 48-kHz mix and a 44.1-kHz
stem produce two minimally shifted cut plans, which you hear as a click after
spending twenty minutes looking elsewhere.

Consequence for the signature: `apply_plan(plan, track) -> Segments`, called
for each track. Not `plan_for(track)`.

---

## Data model

```python
# What the Score contains versus how it was found. Two questions, two types.
ScoreFormat  = Literal["json", "midi", "analyzed", "constant"]
ProviderKind = Literal["explicit", "json_sidecar", "midi_sidecar",
                       "analysis", "constant"]

@dataclass(frozen=True)
class TempoEvent:
    tick: int
    us_per_quarter: int

@dataclass(frozen=True)
class MeterEvent:
    tick: int
    numerator: int
    denominator: int      # 4, 8, 16, 32, 64 …

@dataclass(frozen=True)
class Marker:
    tick: int
    name: str

@dataclass(frozen=True)
class Section:
    name: str
    start_tick: int
    end_tick_exclusive: int    # half-open, see S5
    bar_aligned: bool          # does the start lie exactly on a bar boundary?
    confidence: float | None = None   # None = specified rather than estimated

@dataclass(frozen=True)
class Score:
    ticks_per_quarter: int
    tempos: tuple[TempoEvent, ...]
    meters: tuple[MeterEvent, ...]
    markers: tuple[Marker, ...]    # raw points from the DAW
    sections: tuple[Section, ...]  # canonical annotation, see S4
    source: ScoreFormat
    meter_estimated: bool = False        # meter guessed rather than read
    has_variable_meter: bool = False     # genuinely changing, see below
    has_midbar_meter_change: bool = False

@dataclass(frozen=True)
class ResolvedScore:
    score: Score
    audio_seconds_at_tick_zero: float   # see S1
    provider: ProviderKind
```

`start_bar`, `end_bar`, and `bar_count` are deliberately **not** stored on
`Section`—they are derived display values supplied by the resolver. For a
Section beginning in the middle of a bar, a stored `bar_count` would be
misleading.

`bar_starts` is likewise no longer stored on `Score`: it is derived rather
than serialized, and is an internal resolver cache. See S8.

`source` and `provider` look redundant, but are not: an explicitly selected
`score_file` can be MIDI **or** JSON. `source="midi", provider="explicit"`
is a meaningful combination; `source="explicit"` would confuse categories.

`has_variable_meter` is calculated from the **effective** signatures, not
from `len(meters)`. Two 4/4 events at tick 0 and 9600 are not a meter change;
conversely, a single event at an unusual tick can create a partial bar. That
is precisely why this is a field instead of an ad hoc check at the call site.

Two more fields than strictly necessary, both for later:

`confidence` distinguishes “the user placed a marker here” from “an
algorithm suspects a boundary here.” Here, `None` does not mean “unknown,”
but **“the question does not apply”**—with MIDI, the marker is a fact. The UI
can later react differently without having to pass `source` through.

`meter_estimated` covers the case where the meter does not come from the
file. A 31/32 bar cannot be inferred from audio, and distinguishing 3/4 from
4/4 works only with luck. Anyone displaying the value should know how
reliable it is.

### Material

Deliberately outside `Score`. The Score is a time axis and remains free of
samples—otherwise the frontend can no longer retrieve it through the route,
and the analyzer can no longer be tested as a pure function.

```python
@dataclass(frozen=True)
class Stem:
    name: str                  # "mix", "vocals", "drums", "bass", "other"
    samples: np.ndarray        # (channels, n)
    sample_rate: int

@dataclass(frozen=True)
class StemSet:
    mix: Stem
    extra: Mapping[str, Stem]  # empty = normal single-track case
```

`mix` is required and is the reference for length and alignment. Everything
in `extra` is checked against it, not against each other.

---

### Resolver API

```
tick_to_seconds(tick)            -> float
seconds_to_tick(seconds)         -> float
bar_to_tick(bar)                 -> int      # 1-based
tick_to_position(tick, spb)      -> (bar, beat, subdivision)
position_to_tick(bar, beat, sub, spb) -> int
meter_at_bar(bar)                -> (numerator, denominator)
sections()                       -> tuple[Section, ...]
```

`spb` = subdivisions_per_beat.

The API is source-independent. That is the entire point: Phase 6 (`Musical
SegmentBatch`) and the ruler call it without ever learning whether Cubase or
a self-similarity matrix is behind it.

---

## Five pitfalls that must be settled in advance

### Extrapolation beyond the final event

Cubase writes the tempo track only up to the final event. In the test export,
the file ended at bar 169 even though the track has 233 bars.

**Rule:** `bar_starts` is precomputed up to the final event and extended to
the audio duration when the resolver is created (S8). It is then
arithmetically extrapolated using the most recently valid meter and tempo.
No error, no abort—this is the normal case.

### Tempo changes within a bar

`tick_to_seconds` must integrate piecewise, not multiply by a global factor.
The current track has no tempo changes, but the parser must not fail on them.

### Markers do not necessarily fall on bar boundaries

Do not snap them. Keep the exact tick, report the containing bar as
`start_bar`, and use `bar_aligned` to expose that something does not line up.
Silently moving user data is the worse option.

### Seconds are not ticks

This affects only the analyzer, but the rule belongs here because it touches
the tick canon from decision 1.

Audio analysis supplies beat positions in seconds. Building a `Score` from
them means *inventing* a tempo that reproduces those seconds. Two approaches,
both legitimate:

- **`constant`**—median beat interval, a single `TempoEvent` at tick 0. A
  clean grid, but it drifts over the track length with live-played material.
- **`follow`**—one `TempoEvent` per detected beat. It stays attached to the
  audio but creates a tempo track with hundreds of events, visible as
  jittering bar widths in the ruler.

**Rule:** `constant` is the default, `follow` is behind a switch, and the beat
intervals are median-filtered before either. Anyone choosing `follow` has a
reason.

### Stems are not necessarily sample-aligned

Demucs output is; a stem manually bounced from the DAW with a different start
point is not. And the error is **silent**—you see it only in the finished
lipsync after rendering.

**Rule:** Check the sample count and sample rate of every stem against `mix`.
A length difference beyond a tolerance of a few milliseconds, or a differing
sample rate → reject the stem and write the reason to `diagnostics` (S7,
Phase 4). Put it on the visible error path, not in a console line. Do not
silently resample and do not silently pad.

---

## Binding semantics

Ten stipulations that must be in place before Phase 2. Every one of them is
expensive to change afterward because it is embedded in the data model or the
test corpus.

### S1 — Score and audio are separate things

At which audio second does tick 0 occur? Not automatically at `0.0`: pre-roll,
count-in, export from a locator, MIDI and audio with different start points,
negative offsets.

The alignment belongs **outside `Score`**, because the same Score should
remain usable with multiple audio exports:

```python
@dataclass(frozen=True)
class ResolvedScore:
    score: Score
    audio_seconds_at_tick_zero: float
    provider: ProviderKind
```

The equation belongs here because otherwise someone will reverse it
somewhere:

```text
audio_seconds  = score.tick_to_seconds(tick) + audio_seconds_at_tick_zero
score_seconds  = audio_seconds - audio_seconds_at_tick_zero
```

The existing `downbeat_offset` widget already plays this role, and its sign
is **exactly** correct: line 86 of `audio_clip_plan.py` calculates
`(start_seconds - downbeat_offset)`, meaning `score_seconds = audio_seconds -
downbeat_offset`. Therefore, without conversion:

```text
audio_seconds_at_tick_zero == downbeat_offset
```

The negative case is already handled there—`_nearest_grid_position` returns
“n subdivisions before Bar 1 · Beat 1” for negative values. Pre-roll and
count-in are therefore not new special cases, but existing behavior.

In the constant fallback, the value is carried over unchanged; with MIDI, it
is the starting value that the user may continue to correct.

### S2 — Ticks are integers; grid positions are rounded

`ticks_per_quarter = 480`, `subdivisions = 7` → `480/7 = 68.571…`. Not every
subdivision lands on an integer tick.

**Against rational tick positions** (`Fraction`) in the core, even though
they would be exact: the frontend cannot mirror them. `js/score.js` calculates
with `Number`, so exactness would exist precisely where nobody sees it while
the visible side drifts—contradicting the parity requirement in S10.

**Rule:** Integer ticks everywhere, plus an *identical, deterministic*
rounding rule in both languages. It already exists: `round_half_away_from_zero`
in `musical_timing.py` and `roundHalfAwayFromZero` in `js/musical_grid.js`.
The parity primitive is implemented and tested; it only needs to be used.

In addition, set `ticks_per_quarter` for synthetic Scores to **960**—divisible
by 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32, 48, 60, 64, 80, 96, 120, 160,
192, 240, 320, 480. This makes rounding a no-op for all common grids anyway.
For MIDI, the file's TPQ applies.

The type model states this explicitly: `Tick = int` for events,
`seconds_to_tick() -> float` for queries; rounding occurs precisely at the
boundaries to MIDI, sample, and video frame.

**Rounding is absolute, never cumulative.** This is the actual trap. Adding
the rounded width of seven subdivisions seven times produces
`round(480/7) × 7 = 69 × 7 = 483` instead of 480—drift despite integer ticks.

```python
tick = beat_start_tick + round_half_away_from_zero(
    subdivision * ticks_per_beat / subdivisions_per_beat
)
```

The same applies to bar boundaries: always calculate from the last canonical
meter-event anchor; never repeatedly add rounded bar lengths. The rounding
function is therefore involved **once** at every point, never in a loop.

**Over-fine grids are rejected.** With `subdivisions_per_beat >
ticks_per_beat`, two subdivisions land on the same tick. That is no longer a
rounding problem, but an invalid grid—report it as `invalid` or hard-limit it
to `ticks_per_beat`. At TPQ 960 the limit is so high that it almost never
applies, but a MIDI file with TPQ 96 can reach it.

### S3 — A meter change inside a bar creates a shortened bar

Keep it exact, create a partial bar, emit a diagnostic, and **never move it
silently**—the same stance as for unaligned markers.

Also define the parser defaults that would otherwise arise implicitly:

| Case | Rule |
|---|---|
| no initial tempo | 120 BPM (MIDI default) |
| no initial meter | 4/4 |
| multiple events at the same tick | last event wins |
| events from multiple tracks | merge all, stably by `(tick, track_index, event_index)` |
| SMPTE division instead of PPQ | reject, `INVALID` with a diagnostic |

### S4 — Sections are canonical; markers are raw data

`Score` receives **both**: `markers` as what exists in the DAW, and
`sections` as the annotation that is edited, analyzed, and serialized.

Purely derived Sections would be cheaper, but a manually corrected Section
end that does not fall on a marker could then not be saved losslessly—and
that is precisely the purpose of the JSON sidecar.

Edge cases that must be fixed: two markers at the same tick, an empty name, a
marker before the first bar, a marker exactly at track end, a zero-length
Section, and a Section with `bar_aligned = false`.

`start_tick` and `end_tick_exclusive` are exact and canonical. `start_bar`,
`end_bar`, and `bar_count` are **derived display values**—otherwise,
`bar_count` is misleading for a Section that begins in the middle of a bar.

### S5 — All intervals are half-open

```text
start_tick    end_tick_exclusive
start_sample  end_sample_exclusive
start_frame   end_frame_exclusive
```

with `frame_count = end_frame_exclusive - start_frame`. The new output from
Phase 4 is therefore called `end_frame_exclusive`, not `end_frame`. Six extra
characters eliminate the question of whether the final frame is included—
chained cut plans and empty regions become trivial.

### S6 — Four time concepts, no overloading

In this project, `frame` already means **video** frame. Therefore:

```text
tick          musical time
seconds       real time
video_frame   FPS-based image position
sample_index  position in an audio track
```

Decision 6 therefore means precisely: *Sample indices* are derived from the
plan per track, not “frames.”

### S7 — Providers report three states

“The first provider with a Score wins” is not enough for errors. A broken,
**explicitly specified** file must not silently land in the constant fallback,
or the user will look for the problem in the timing later.

```python
@dataclass(frozen=True)
class ProviderResult:
    status: Literal["not_applicable", "found", "invalid"]
    score: Score | None
    diagnostics: tuple[str, ...]
    provenance: Mapping[str, str]
```

`not_applicable` → continue. `found` → the chain ends. `invalid` → the chain
ends **with an error**, with no fallback. No `.mid` file present is
`not_applicable`; a present but broken `.mid` file is `invalid`.

### S8 — `bar_starts` is not serialized

It can be derived from TPQ, meter events, and the extrapolation target. If it
is editable alongside `meters` in JSON, the two can contradict each other.

JSON stores canonical events and annotations. The resolver builds
`bar_starts` as an internal cache. The route may deliver it to the frontend—
there it is a transport optimization, not truth.

And precomputation does not stop at the final event; it is extended to the
**audio duration** when the resolver is created. An export that ends early is
the normal case, not the exception.

Clarification of decision 3: `bar_to_tick` is O(1); `tick_to_position` is a
binary search over `bar_starts` and therefore O(log n). Fast enough, but the
promise must be stated correctly.

### S9 — The cache fingerprint includes configuration

The source file plus mtime is not sufficient for analyzer results. Analyzer
version, algorithm version, `beats_per_bar`, `tempo_mode`, `target_sections`,
and thresholds change the result just as much.

```json
{
  "schema_version": 1,
  "generator": { "name": "...", "version": "...",
                 "algorithm_version": 1, "config_fingerprint": "..." },
  "derived_from": { "filename": "...", "size": 123, "mtime_ns": 456 },
  "edited": false
}
```

With `edited: true`, the file still wins—but with a diagnostic if the audio
has changed since.

### S10 — Python and JavaScript share a golden corpus

`score/tempo_map.py` and `js/score.js` implement the same mathematics. This
is the classic point of drift, and the repository already has two parallel
suites in `tests/` and `tests_js/` built for exactly this purpose.

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

Both languages check the same fixtures for Tick↔seconds, Bar↔Tick,
Position↔Tick, snapping to bar/beat/subdivision, extrapolation, and
video-frame rounding. They also check the invariants—which must be stated in
the correct direction:

```text
tick_to_position(position_to_tick(p)) == p          for every valid position
position_to_tick(tick_to_position(x))   is the nearest grid boundary under S2,
                                        not necessarily x
tick_to_seconds(seconds_to_tick(x))     ≈ x
Monotonicity: p1 < p2  ⟹  position_to_tick(p1) < position_to_tick(p2)
Python and JavaScript produce identical boundaries for the same fixture
```

The round trip is lossless only from the **position side**. An arbitrary
MIDI tick between two grid points is necessarily quantized when routed
through a musical position—that is correct behavior, not an error, and a
test requiring equality would reject a correct implementation. The
monotonicity check instead catches the errors that a pure round-trip check
allows through.

Deterministically generated random fixtures for tempo and meter maps are
also useful and require no property-testing library.

---

## File layout

Today, the repository is **flat**—all modules live in the root directory;
`__init__.py` imports `MusicalLoadAudioUI` and calls
`register_waveform_routes()`. Introducing `score/` and `material/` packages
is therefore a deliberate change, not a continuation. The alternative would
be `score_model.py`, `score_parse.py`, and so on at the same level. Given the
expected number of files, packages win, but the decision must be named rather
than assumed.

Existing files that must be considered here:

| File | Current role |
|---|---|
| `waveform_routes.py` (318 lines) | Route + ETag + bounded cache. **Model for `score/routes.py`** |
| `waveform_peaks.py` (521 lines) | Pure peak calculation |
| `js/musical_audio_ui.js` (2308 lines) | Node UI |
| `js/musical_grid.js` (305 lines) | The location of decision 2 |
| `js/musical_audio_ui.css` (19 KB) | Theme, subject of the current branch |
| `js/waveform_loader.js`, `js/waveform_peaks.js`, `js/metronome.js` | untouched |

New or changed:

| File | Contents | Pure? |
|---|---|---|
| `docs/MUSICAL_TIMING_SPEC.md` | moved in Phase 0.5, extended in Phase 1 | — |
| `docs/AUDIO_INTEGRATION_SPEC.md` | moved in Phase 0.5 | — |
| `score/midi_parse.py` | `bytes` → `Score`. No I/O, no ComfyUI. | yes |
| `score/model.py` | Dataclasses + resolver | yes |
| `score/tempo_map.py` | `TempoMap` protocol, `ConstantTempoMap`, `ScoreTempoMap` | yes |
| `score/serialize.py` | `Score` ↔ JSON, both directions | yes |
| `score/analyze.py` | Samples → `Score`. **Stub, Phase 8** | yes |
| `score/naming.py` | Name root, repetition counter, seed derivation | yes |
| `material/stems.py` | `Stem`/`StemSet`, alignment check | yes |
| `material/energy.py` | RMS envelopes, percentile gate, onsets. **Phase 9** | yes |
| `musical_timing.py` | receives an optional `tempo_map` parameter | yes |
| `audio_clip_plan.py` | passes `tempo_map` through, `apply_plan` per track | yes |
| `score/providers.py` | Provider chain, sidecar search, mtime cache | no |
| `material/discovery.py` | Find stem sidecars and `.stems/` directories | no |
| `musical_audio_ui.py` | Node, `IS_CHANGED`, error path, new outputs | no |
| `score/routes.py` | aiohttp route for the frontend | no |
| `js/score.js` | JS mirror of the resolver | — |
| `js/musical_grid.js` | Conversion to tick basis | — |

The pure/impure separation is already the project's pattern—it is only being
continued here. `analyze.py` falls on the pure side: samples in, `Score` out.
Model loading, caching, and file access remain in the provider.

`score/discovery.py` is now called `providers.py` because it no longer
searches; it executes a chain.

---

## Score sources

**Sidecar convention:** `velvet-lies.wav` → `velvet-lies.mid` in the same
directory. Automatic, with no UI.

**Override:** optional string input `score_file` for differing names.

**JSON sidecar:** `velvet-lies.score.json` is the serialization of `Score`.
Three roles in one file:

- Cache for analysis results (which cost seconds, not milliseconds)
- Manually editable Sections when automation misses
- Exchange format in case another tool ever supplies markers

To keep cache and manual work from colliding, the file carries `derived_from`
(source file + mtime) and `edited: bool`. If the source is newer and `edited`
is not set, it is discarded and regenerated. If `edited` is set, the JSON
always wins—someone deliberately changed it.

**No Score available:** `ConstantTempoMap` from BPM and meter. Exactly today's
behavior. No warning; this is a legitimate mode.

The active provider belongs in `score_provider` and `diagnostics` so the
graph shows which source applies—not in `musical_position`.

---

## Material sources

The same idea a second time—deliberately, because it worked for the Score and
because a second concept for the same problem would only add explanation.

**Directory convention:** `velvet-lies.wav` → `velvet-lies.stems/` beside it,
using the Demucs layout: `vocals.wav`, `drums.wav`, `bass.wav`, `other.wav`.
This is not an invented format; it is what already exists on disk when
someone runs Demucs.

**Single-file convention:** `velvet-lies.vocals.wav` beside it. This covers
the common case where only the vocal stem is needed and nobody wants the
other three lying around.

**Override:** optional string input `stems_dir`, analogous to `score_file`.

**No stem available:** a `StemSet` with empty `extra`. Everything downstream
checks for presence; nothing fails. This too is a legitimate mode and needs
no warning.

No second audio input on the node. Two file widgets next to each other
practically invite accidentally swapping the mix and stem—and this is again
a silent kind of error.

---

## Frontend transport

The ruler needs the Score at **edit time**. Node outputs are created only at
execution time, so they do not help.

Route through `PromptServer.instance.routes`:

```
GET /comfyui-musical-audio/score?audio=<name>
```

The namespace follows the existing code: `waveform_routes.py` defines
`WAVEFORM_PEAK_ROUTE = "/comfyui-musical-audio/waveform-peaks"`, mirrored in
`js/waveform_loader.js` as `WAVEFORM_PEAK_ENDPOINT`. Therefore use
`SCORE_ROUTE = "/comfyui-musical-audio/score"` and the same mirroring, not a
second prefix.

The complete payload—without `audio_seconds_at_tick_zero`, the frontend
cannot overlay the Score on the waveform:

```json
{
  "schema_version": 1,
  "ticks_per_quarter": 960,
  "audio_seconds_at_tick_zero": 0.0,
  "bar_starts": [],
  "tempos": [], "meters": [], "markers": [], "sections": [],
  "source": "midi",
  "provider": "midi_sidecar",
  "meter_estimated": false,
  "has_variable_meter": false,
  "diagnostics": []
}
```

Cache on the server—and follow **the pattern already present**.
`waveform_routes.py` already solves exactly this problem: the ETag comes from
file size and `st_mtime_ns`; `If-None-Match` is checked before every cache
access and answered with `304`; the cache is size-bounded and keyed by
`(canonical_path, mtime_ns)`; and expensive work runs through
`asyncio.to_thread`. Register analogously through `register_score_routes()`
in `__init__.py`.

No second cache concept. If implementation reveals reusable parts, that is
worth a refactoring—two parallel invalidation strategies are not.

`bar_starts` as a flat array is entirely sufficient for the ruler—the
frontend needs the event lists only for display purposes.

**Reserved for Phase 8:** The MIDI parser responds in milliseconds; audio
analysis does not. The route therefore needs a third state in addition to
“Score” and “no Score”: `202` plus `{ status: "analyzing" }`, and the frontend
polls. Handle this case in the client today—as “show the uniform grid for
now”—which costs nothing and avoids changing both ends at once later.

---

## Phases

Every phase ends in a runnable and testable state.

After Phase 4, timing in the graph is correct for **constant** meters without
touching `musical_audio_ui.js` (2391 lines). Scores with variable meter or a
meter change inside a bar remain non-editable until Phase 5 is complete—see
the feature gate in Phase 4.

### Phase 0 — Housekeeping

Independent of the Score, but due first: the refactoring relies on the test
suite, so the foundation must hold.

**Add `IS_CHANGED`.** ComfyUI caches based on inputs. The filename does not
change on a new export from the DAW, so the node does not rerun and continues
working with stale audio. The error then looks like a timing problem. Return
mtime or a hash.

**Make the silence fallback visible.** Missing file or failed decode → one
second of silence plus `print`. It scrolls out of the console; in a batch of
29 segments, the problem appears only at the end. Keep the fallback, but
also write the error to a STRING output visible in the graph. In Phase 0,
without `diagnostics`, this is still `musical_position`; Phase 4 moves it
there.

**Replace three bare `except:` clauses.** Lines 63 and 76 of
`musical_audio_ui.py` in `INPUT_TYPES`, and line 208 in `load_audio` around
`get_annotated_filepath`. They currently also catch `KeyboardInterrupt` and
`SystemExit`. `except Exception:` is sufficient.

**Narrow `VALIDATE_INPUTS`.** Its signature is
`VALIDATE_INPUTS(cls, audio, **kwargs)` and it unconditionally returns `True`.
The code comment correctly explains *why*—the “Value not in list” error
should be bypassed so the silence fallback can apply. But `**kwargs` is the
problem: ComfyUI skips its own validation for every input accepted by the
signature, and `**kwargs` accepts all of them.

The fix is one line: remove `**kwargs`. This preserves the intended effect
for `audio`, while every other widget is validated again before
`musical_timing` throws a `TypeError` during execution.

**Bound `start_beat`.** It is declared as
`("INT", {"default": 1, "min": 1})`—with no maximum. Beat 7 in a 4/4 bar
fails only deep inside `musical_timing`. Clamp it against `beats_per_bar`.
The same applies to `beats_per_bar` itself, which also has only a minimum.

**Clarify the dead `duration` widget—careful, it is not dead.** Python
discards it through `_ = duration, snap_mode, audioUI`, while `"duration"`
also appears in `RETURN_NAMES`. The naming collision is real.

But in the frontend it is **wired in eight places**, including
`setWidgetValue("duration", …)` in Seconds synchronization and a dedicated
branch in the widget callback (`musical_audio_ui.js` line 2332 onward).
Removing it is therefore not cleanup but an intervention in Seconds-mode
logic—and `AUDIO_INTEGRATION_SPEC.md` explicitly describes this behavior as
intentional.

**Revised recommendation:** Leave the widget in place. Removing it is a
separate operation with a specification change, not housekeeping.

**And renaming the output is not housekeeping either.** An output name is a
public contract even when its position is unchanged—saved workflows and
`tests/test_node_contract.py` refer to it. Therefore, do not include it in a
general “housekeeping” commit. Instead: a dedicated commit, a specification
change in it, a deliberate update to `test_node_contract.py`, and a changelog
entry.

Acceptable alternative: leave the name in place for 0.2 and clean it up only
in a major release. An unattractive collision is no more dangerous than a
broken workflow.

*Why first:* Everything here is independent of the Score and can be done in
one pass. Afterward, the test suite is a reliable safety net for Phase 3.

### Phase 0.5 — Documentation organization

Purely mechanical: no behavior and no test touched. It appears here rather
than later because Phase 1 changes the specifications' content: **move first,
then edit.** Combining both in one commit makes the diff unreadable because
`git` no longer recognizes moved *and* edited text as a move.

**Step 1—move.** `docs/MUSICAL_TIMING_SPEC.md` and
`docs/AUDIO_INTEGRATION_SPEC.md`, using `git mv`, without changing a line of
content. Neither Python, JS, tests, nor `package.json` refers to the
filenames—verified, so the move breaks nothing.

**Step 2—link from README.** Add a `## Documentation` section with both
paths. Currently, the specifications are **not reachable at all** from the
README; the only internal link in the entire document points to `LICENSE`.

**Step 3—define precedence.** This is the real cleanup item, not the folder.
README duplicates normative content in four sections: “Tempo
interpretation,” “Outputs,” “Clamping and zero-length selections,” and
“Compatibility.” Three documents, overlapping content, and no statement of
which wins in a conflict.

Two sentences solve it: “this document is normative” at the top of both
specifications, and “descriptive; the specs in `docs/` take precedence” in
README. The duplication may then remain—it is an introduction rather than a
second truth.

**Step 4—address verified inconsistencies.** Three of them, all demonstrable
in the repository:

- README, “Version 0.1 limitations,” line 183: *No waveform visualization*.
  There are `waveform_peaks.py` (521 lines), `waveform_routes.py` (318 lines),
  `js/waveform_loader.js`, `js/waveform_peaks.js`, and two test files for it.
  The item is simply stale.
- README refers to 0.1 throughout, `CHANGELOG.md` is at 0.1.1, and the branch
  targets 0.2.0.
- `MUSICAL_TIMING_SPEC.md` has a `## Status` section with a version;
  `AUDIO_INTEGRATION_SPEC.md` does not. Align them.

The other three lines in the limitations list—no BPM detection, no downbeat
detection, no tempo maps, no changing meters—remain. They are correct and
are removed in sequence by Phases 2 through 4 and 8. Conveniently, the list
is already the roadmap.

**Coupling to Phase 0:** `AUDIO_INTEGRATION_SPEC.md` describes the `duration`
widget as “remains present for positional workflow compatibility.” If Phase
0 removes or renames it, that sentence becomes false. This one change belongs
in the Phase 0 commit—at the *old* location, which does not interfere with the
later `git mv`.

**What explicitly does not belong here:** any content expansion of the
specifications around Score, ticks, or extrapolation. That is Phase 1. This
phase moves, links, and removes contradictions—nothing more, or the benefit
of the readable diff is lost again.

### Phase 1 — Specification

Extend `docs/MUSICAL_TIMING_SPEC.md` with Score, the tick canon, and the
extrapolation rule. Raise the version to 0.2 and define 0.1 behavior as
`ConstantTempoMap`.

The provider chain, `ScoreFormat`, and `ProviderKind` belong in the same text
even though only three of the five providers are built. A contract naming the
empty slots is more valuable than one that has to be widened later.

*Why before implementation:* The specification text is the contract against
which implementation is built—and it is what you give Claude Code, not just
the code.

### Phase 2 — Parser and Score

`midi_parse.py`, `model.py`, and `serialize.py`, fully tested and without
integration. Test corpus: the four unusual meters, a track with no events, a
track with a tempo change inside a bar, and a truncated file to test
extrapolation.

**Touchstone:** Bar 55 must yield −1 frame against the naive grid; bar 169
exactly −3.5.

**Second touchstone:** `Score → JSON → Score` is lossless. It costs one test
and makes Phase 8 and later manual sidecar editing a non-issue.

### Phase 3 — Introduce TempoMap

Define the protocol, build `ConstantTempoMap`, and extend `musical_timing` and
`audio_clip_plan` with the optional parameter.

**Acceptance criterion:** all old tests pass without changing one line of
test code.

### Phase 4 — Node integration

Provider chain, `ResolvedScore`, new outputs—**appended at the end** so the
existing order and `tests/test_node_contract.py` remain untouched:
`end_frame_exclusive`, `section_name`, `sample_rate`, `score_format`,
`score_provider`, `diagnostics`.

**`diagnostics` instead of overloading `musical_position`.** The earlier plan
put the Score source, silence fallback, and rejected stems into
`musical_position`. That turns a domain output into a system log channel.
Keeping them separate leaves `musical_position` as the musical position, and
`diagnostics` collects provenance and warnings—later structurally representable
as a JSON string without changing the node contract again.

**Feature gate for variable meter.** This was the most dangerous error in the
previous plan: “from Phase 4 onward, timing is correct; only the ruler is
cosmetically wrong” is untenable. The linear subdivision index also controls
selection, snapping, and duration conversion. With an active meter map, the
old frontend therefore writes **incorrect selection values back**—silently,
and into the widgets that determine node output.

Rule until Phase 5 is complete—**operational, with no room for
interpretation**:

> With `has_variable_meter` or `has_midbar_meter_change`, the Score affects
> neither selection nor snapping nor the outputs produced from them. Score
> data may be displayed; Score-based editing is disabled. The existing
> constant grid remains the sole editing path, and `diagnostics` indicates
> that only part of the Score information is active.

There is no partially active Score mode. Either the Score controls editing
completely or not at all. `ConstantTempoMap` remains unaffected and
unrestricted.

Alternatively, publish Phases 4 and 5 together. What is not acceptable is
shipping variable meter as editable and pointing to the ruler as the only
defect.

From this point onward, timing in the graph is correct for the constant case.

### Phase 5 — Frontend

Route, `js/score.js`, conversion of `musical_grid.js` to a tick basis, ruler,
and snapping.

**Formerly open design question, now decided:** time-proportional. The x-axis
in both views is the seconds axis of a waveform. Equally wide bars would no
longer align over the audio they describe after tempo or meter changes—that
would make sense only in a separate, abstract score view, which does not
exist here.

```text
x-axis    = seconds
bar width = actual duration of the bar
```

### Phase 6 — Sections and batch

New node `MusicalSegmentBatch`: accepts the Score and returns a list of
segments. Two modes:

- **Sections**—cuts at canonical `Section` boundaries (S4), not at raw
  markers
- **Blocks**—cuts into fixed N-bar blocks

Per segment: `audio`, `start_frame`, `frame_count`, `name`, `start_bar`,
`bar_count`, `seed`. Boundaries are half-open (S5).

**Seed from the Section name.** This costs almost nothing and is the cheapest
way to produce a coherent video: normalize names, hash them, and mix with a
base seed. Recurring sections then look related automatically without anyone
entering seeds manually 29 times.

**The hash must be stable.** Do not use Python's `hash()`—since 3.3 it has
been salted per process for strings, so the video would look different after
every ComfyUI restart. Use BLAKE2 or SHA-256, truncated to the seed width.
Normalization first: NFKC, then `casefold()`, then strip a numeric suffix.

Three modes, because grouping is not always desirable:

| Mode | Behavior |
|---|---|
| `Exact` | full name, `Chorus 1` ≠ `Chorus 2` |
| `Family` | root, `Chorus 1` = `Chorus 2`—default |
| `Unique` | each Section gets its own seed, even with the same name |

This belongs in `score/naming.py` because the same normalizer also serves the
analyzer labels from Phase 8.

**Block size from target duration.** A third parameter alongside fixed N: a
target number of seconds from which the node calculates the bar count that
comes closest at this tempo. Cuts then fall on bar boundaries rather than
beside them—see Phase 10.

### Phase 7 — Frame alignment (optional)

`frame_alignment` as a mode: `Off` / `8n+1` / `Custom`. Rounds `frame_count`
to a value valid for the target model.

The node is the only place in the stack that can decide this musically,
because it knows where the next bar boundary lies.

**Conflict strategy, binding.** A segment cannot lie exactly between two bar
boundaries *and* be exactly `8n+1` frames long. Therefore the musical plan is
**never shifted silently**. Instead, both remain visible:

```text
musical_frame_count    from the bar boundaries, true
aligned_frame_count    valid for the target model
padding_before
padding_after
```

The Score remains truth, and the target-model adapter decides how to create
the difference—held frames, overlap, or trimming. That is a video-workflow
decision, not a timing decision.

### Phase 8 — Audio analysis (stub)

For material without MIDI. Not for this track—here it is the path for applying
the system to unfamiliar audio too.

**Interface** (this is the part already fixed):

```python
# score/analyze.py — pure
def analyze_structure(
    samples: np.ndarray,          # mono float32
    sample_rate: int,
    beats_per_bar: int,           # supplied by the node, not detected
    tempo_mode: Literal["constant", "follow"] = "constant",
    target_sections: int | None = None,
) -> Score:                       # source="analyzed", meter_estimated=True
    ...
```

**Method**, in the order it would be built:

1. Beat grid. Estimate downbeat phase separately—a good beat tracker says
   *where* the beats are, not which one is beat one.
2. Beat-synchronous features: chroma plus MFCC, median-aggregated per beat. A
   five-minute track shrinks to ~600 vectors, making everything from here
   computationally cheap.
3. Self-similarity matrix, cosine, diagonals smoothed.
4. Boundaries using Foote novelty with a checkerboard kernel, then peak
   picking.
5. Labels using spectral clustering on the SSM. Repetition detection, not
   functional naming.
6. Snap boundaries to downbeats, synthesize ticks, build `Score`.

Dependencies: numpy and scipy are sufficient. Eigendecomposition through
`scipy.linalg.eigh`, k-means through `scipy.cluster.vq`—scikit-learn is not
needed for this.

**Names:** The method produces `A B A B C B`, not `verse chorus`. For
`section_name`, that means `A1`, `B1`, `A2`, `B2`, `C1`, `B3`—cluster letter
plus repetition counter. This makes both *that* two sections are equal and
*which occurrence* each is visible in the graph. Functional labels
(intro/verse/chorus) are trained on Western pop structure and become
arbitrary for electronic material; omitting them is not a compromise.

**Evaluation:** The analyzer is measured against `velvet-lies.mid`. You have
ground truth from the DAW for this track—manually placed marker positions and
bar boundaries.

However, the obvious metric, “mean distance to the nearest true boundary,”
is worthless: an analyzer producing many boundaries automatically scores
well. Use the standard MIR method instead:

- unique one-to-one matching of detected and true boundaries
- tolerance window of half a bar to one bar
- **Precision** (how many detected boundaries are real) and **Recall** (how
  many true boundaries were found), producing F1
- mean distance only over matched boundaries
- report over-segmentation and under-segmentation counts separately

Only then do “too many boundaries” and “an important boundary is missing”
become visibly different errors—and that distinction separates a useful
result from one that merely looks good.

That is why this phase comes late yet is documented here: the only track on
which the automation can be evaluated honestly is exactly the one for which
it is not needed.

**What is explicitly not built:** `allin1` as a backend. It supplies downbeats
and functional labels directly, but depends on NATTEN—a compiled extension
that must match the exact Torch version. In a ComfyUI node, this would make
other people's Torch installations hostage to it. It also downloads roughly
1.5 GB of models on first use. Conceivable as an optional provider in a
separate venv through a subprocess; not as a dependency.

**License note, corrected:** The repository is already under **GPL-3.0**, not
a permissive license. The situation is therefore less restrictive than first
assumed—GPLv3 §13 expressly permits combination with AGPLv3 code, and the
result remains distributable.

The rest of the clause still applies: the AGPL component retains its network
condition, and ComfyUI *is* an HTTP server. That is precisely the situation
targeted by AGPL §13. Practical consequence for someone running ComfyUI
locally: none. For someone offering it as a hosted service: possibly.

The recommendation therefore remains unchanged, only for a different reason:
Essentia as an **optional extra with lazy import**, so the standard
installation remains simply GPL-3.0 and nobody stumbles into a condition they
did not choose. The analysis chain above works with numpy and scipy anyway.
(Not legal advice—if the package is published, this should be reviewed
properly once.)

### Phase 9 — Stems

Requires Phase 6 and nothing else. Independent of Phase 8—stems and the
analyzer have nothing in common except the word “audio.”

**Foundation:** Discovery according to the two conventions, alignment check,
and `apply_plan` for each track. Alongside `audio`, each segment emits
`audio_vocals` and whatever else was found.

**Vocal output for lipsync** directly as 16 kHz mono. The lipsync nodes need
that anyway, and one fewer resample node in the graph is one fewer manual step
per shot.

**The real gain is not the sound but the classification.** Measure frame RMS
of the vocal stem per Section, and the node knows where singing occurs and
where it does not. That is the choice between a performance shot and B-roll—
for 29 shots, a manual task that disappears entirely. It requires no ML
because the heavy work already occurred during separation.

Two outputs for this:

- `has_vocals: bool`—a **high percentile** of frame RMS against a threshold,
  not the mean. Stems contain bleed and reverb tails; a gate over the mean
  triggers in half of the instrumental part.
- `vocal_onset_seconds`—the first entry *within* the segment. Seconds, not
  frames: conversion to video frames needs fps and belongs at the boundary
  (S6). If vocals begin 1.2 s after the segment start, that suggests a camera
  move into the shot, not a cut onto it. This value is directly useful in the
  graph.

**Drums and bass are curves, not cuts**—bass energy for zoom or shake, drum
onsets for IPAdapter weights. Different output type, different lifecycle, so
use a separate `MusicalEnvelope` node instead of adding outputs to the batch.
Not part of this phase; it is only why `StemSet` is generic over `extra`
rather than named `vocals: Stem | None`.

### Phase 10 — Manifest and shot-list bridge

The batch knows everything a shot list needs: number, name, bar, bar count,
duration, vocal flag. A manifest output (JSON plus Markdown) turns this into
the handoff to the prompting step.

This closes the chain: a shot-list generator works in fixed chunks of
seconds; the `Blocks` mode works in bars. At a known tempo, these are the same
size in different units—the conversion from Phase 6 makes the shot list land
on the music rather than beside it.

Optionally afterward: send the manifest plus mood tags to a local model and
have it write one image prompt per segment. This is where an LLM actually
contributes to this chain—translating structure into prompts, not analyzing
it.

---

## Parallel strand: Waveform Editor (v0.2.0)

State at `604e5ab`, ninth v0.2 commit, five steps open. The strand is largely
independent of Score except for two points of contact described below. It
belongs here because both strands touch the same file.

### What exists (verified at the commit)

- `js/waveform_renderer.js` (351 lines) with `createWaveformRenderPlan`,
  `renderWaveformCanvas`, `clearWaveformCanvas`
- The renderer already accepts `startSeconds`/`endSeconds` and selects the
  pyramid level from them. **The assumption from Step 5 is correct**—zoom
  needs no new renderer, only changed time boundaries.
- `MAX_WAVEFORM_RENDER_WIDTH = 1_000_000` and
  `MAX_WAVEFORM_DEVICE_PIXEL_RATIO = 4` are already caps in the code. The
  warning against a track-wide canvas is therefore more than an intention.
- `node._musicalAudioWaveformState`, `node._musicalAudioWaveformRenderer`
  with `destroyed`, `animationFrameId`, `resizeObserver`
- `node.scheduleMusicalAudioWaveformRender()` as a coalescing scheduler
- `onRemoved` hook with `cancelAnimationFrame` and
  `resizeObserver.disconnect()`
- The return to selection start at selection end already exists
- No playhead, no modal, no zoom, no `showExtensionDialog`—confirmed

### Three corrections to the step plan

**`audioEl` is in the closure, not on the node.** Line 560:
`const audioEl = document.createElement("audio")`. Everything else shared is
attached to the node—the audio element is not. Step 4 requires “reuse the
same audioEl”; today, it is simply unreachable.

Proposal: **do not expose the element**. Instead, expose a transport facade
`node._musicalAudioTransport` with `getCurrentTime()`, `seek(seconds)`,
`isPlaying()`, `subscribe(fn)`. Anyone receiving the raw element can attach
listeners directly, after which cleanup when closing the modal is no longer
controlled in one place. The facade takes fifteen lines and makes Step 6
(“two views, one clock”) enforceable in the first place.

This preparation belongs after **Step 2**, not after Step 4—the playhead is
its first consumer, and it is still inexpensive there.

**The playhead needs a second rAF loop—and that is correct.**
`scheduleMusicalAudioWaveformRender` is a *one-shot* scheduler: at most one
frame in flight, then `animationFrameId = null`. The playhead needs a
*continuous* loop during playback. Different form, separate handle.

The trap: the new handle must be stored in the same `runtime` object. Today,
`onRemoved` cancels exactly one `runtime.animationFrameId`. A loop not
registered there continues after the node is deleted—with a closure over dead
DOM. That is the most likely bug in Step 2 and the hardest one to find.

**`app.extensionManager` is already in use** (`setting.get`, line 102). The
spike from Step 3 therefore needs to clarify only `dialog.showExtensionDialog`
and Vue mounting, not access to the manager itself. This makes the spike
materially smaller.

### Two points of contact with the Score

**Otherwise the ruler is built twice.** Step 4 calls for a “large ruler area”
in the modal. The node ruler currently depends on `subdivisionIndexToSeconds`
(line 1611)—the very index removed by decision 2. Building the modal ruler on
the same index writes it twice.

The solution is **not** “wait for Phase 3,” as previously stated here. The
same facade logic as for the transport solves it better:

```js
const timeAxis = {
    secondsToPosition(seconds),
    positionToSeconds(position),
    ticksForVisibleRange(start, end),
    barsForVisibleRange(start, end),
};
```

Use `ConstantTimeAxis` today and `ScoreTimeAxis` later. This allows the modal
shell, playhead, zoom, and scrolling to be built immediately, with only the
ruler backend replaced later. The ordering constraint shrinks to one rule:

> The modal ruler must not be built directly on the linear subdivision index.

**Sections belong in the ruler, not in a separate track.** Once Phase 4
exists, the modal has something to display that did not exist before: named
regions. The toolbar area reserved in Step 4 is the wrong place—Sections are
time-bound and belong as a band beneath the ruler, on the same time axis.

Over time, this becomes a **Score inspector**: switchable layers for tempo,
meter, Sections, markers, and vocal activity from Phase 9. Do not show
everything at once; use layer selection. The modal then grows into a musical
inspector without bloating the node itself.

Three small functions become almost free once Sections are in the modal:
jump to previous/next Section, zoom to the current Section, and set selection
to Section boundaries. Add `Follow playhead` with `Off` / `Page` / `Center`,
where `Page` is usually more pleasant during editing than constant scrolling.

Long-term, but worth mentioning here because it explains S4: move, rename,
split, and save markers and Sections directly in the modal as `.score.json`.
That is precisely why Sections must be canonical instead of derived from
markers.

### Interleaved order

| Sequence | Why here |
|---|---|
| Step 2 + transport facade | independent, and the facade is more expensive later |
| Phase 0, Phase 0.5 | independent, no risk |
| Step 3 (spike) | small, resolves the largest unknown early |
| Phase 1–3 | Score core, independent of the modal |
| Step 4–6 | modal, ruler tick-based from the start |
| Phase 4 | Score in the graph, Sections for the modal |
| Phase 5 | tick conversion in both views at once |
| Phase 6 onward | batch, stems, manifest |

There are only two hard constraints: variable meter maps remain read-only
until Phase 5 is complete (feature gate in Phase 4), and the modal ruler must
not use the linear subdivision index. Everything else can be reordered.

---

## What drives the effort

Not the parser. It is manageable.

The cost center is **decision 2**—removing the linear index from the
frontend. It affects snapping, selection, the ruler, and conversion back to
seconds. If anything deserves detailed thought during planning, it is this.

Phase 8 is independently expensive, but in another currency: the code is
short, the tuning is long. Novelty threshold, kernel size, cluster count—
calibrate them against hearing and ground truth, not against a unit test.
That is why it is a stub rather than a ticket.

Conversely, Phase 9 is cheaper than it looks. Cutting is a loop over a list
that already exists. All the effort lies in discovery and alignment checks—
the impure edges, not the core.

---

## Order when time is limited

Phase 0 is worthwhile by itself: `IS_CHANGED` fixes the caching problem, and
the visible error path saves the search for silent segments. One evening, no
risk.

Phase 0.5 costs an hour and is the only phase with no risk at all—no code, no
tests, only `git mv` and text. If it does not happen directly after Phase 0,
it never will, because from Phase 1 onward it merges with content changes and
ceases to be a separate phase.

Phase 4 then offers the greatest benefit per unit of effort: from there,
timing is correct and work on the video workflow can begin.

Phase 6 is the actual leverage for this project—29 shots from one node instead
of 29 manual steps.

Phase 5 can be deferred for constant meters and has the highest cost. For
variable meters, however, it is mandatory: such Scores remain read-only until
it is done. It would be “cosmetic” only without the feature gate—with it,
Phase 5 enables half a feature.

Phase 9 belongs directly after 6 if lipsync is planned. It is the only late
phase with a strong cost-benefit ratio because it builds on a finished cut
plan and works only at the edges.

Phase 8 comes afterward, or never. As long as exports come from Cubase, it is
purely optional—and if it arrives, decision 5 has already reserved a place
for it.

Phase 10 serializes data that already exists. One afternoon once 6 and 9 are
in place.

---

## What this revision adds

Two extensions, both shaped so they cost nothing in the early phases.

**Score sources** (in service of Phase 8):

- Decision 5: provider chain instead of branching
- `Score.source`, `Score.meter_estimated`, `Section.confidence`
- The “Seconds are not ticks” pitfall, including the `constant`/`follow` rule
- `score/serialize.py` and the JSON sidecar with `derived_from`/`edited`
- `discovery.py` → `providers.py`
- `202 analyzing` as a reserved route state
- Phase 8 as a stub with a fixed interface and evaluation plan

**Multi-track Material** (in service of Phase 9):

- Decision 6: one plan, N tracks—`apply_plan(plan, track)`
- `Stem`/`StemSet` outside `Score`, keeping the Score serializable and the
  analyzer purely testable
- The “Stems are not necessarily sample-aligned” pitfall
- Material sources following the sidecar pattern, explicitly **without** a
  second audio input on the node
- `score/naming.py` with seed derivation, already usable in Phase 6

**Reconciliation with the repository** (in service of accuracy), branch
state `feature/css-theme-foundation-v0.2.0`:

- Decision 2 affects **fourteen** functions, not six. The mathematical root
  lies in `js/musical_grid.js`; integration additionally affects several
  places in `js/musical_audio_ui.js`. Python is unaffected.
- Route and cache follow `waveform_routes.py` rather than a separate concept
- The license is GPL-3.0; this reduces, but does not eliminate, the Essentia
  concern
- `VALIDATE_INPUTS`: `**kwargs` is the problem, not `return True`
- Line numbers of the three `except:` clauses (63, 76, 208), frontend 2308
  rather than 2274
- The repository is flat; introducing `score/` and `material/` packages is a
  named decision, not a continuation of the existing layout

**Verified and carried over unchanged:** no `IS_CHANGED` exists; there are
two silence fallbacks, both using only `print`; `duration` is both a widget
and a `RETURN_NAMES` entry; `start_beat` and `beats_per_bar` have no maximum.

**Waveform Editor** (strand B, new, state `604e5ab`):

- Five open steps as a parallel strand, interleaved rather than appended
- Transport facade `node._musicalAudioTransport` instead of raw `audioEl`,
  moved forward to after Step 2
- Registration of the playhead rAF loop in the existing `runtime` object
- `TimeAxis` facade rather than being forced to wait for Phase 3
- Score inspector, Section navigation, and `Follow playhead` as an outlook

**Binding semantics** (S1–S10, new chapter before the file layout):

- S1 `ResolvedScore` with `audio_seconds_at_tick_zero`; `downbeat_offset`
  already fills the role
- S2 integer ticks plus a shared rounding rule instead of `Fraction`
- S3 meter changes inside a bar, plus parser defaults for tempo, meter,
  duplicate events, multi-track merge, and SMPTE
- S4 canonical Sections alongside markers; bar values derived only
- S5 half-open intervals, `end_frame_exclusive`
- S6 `sample_index` instead of “frame” for audio positions
- S7 `ProviderResult` with `not_applicable` / `found` / `invalid`
- S8 derived `bar_starts`, extended to audio duration,
  `tick_to_position` O(log n)
- S9 cache fingerprint over configuration, not only mtime
- S10 cross-language golden corpus

**Clarifications after the second review:**

- S1 sign equation, verified against the repository:
  `audio_seconds_at_tick_zero == downbeat_offset`
- S2 absolute rather than cumulative rounding; rejection of over-fine grids
- S10 round trip only from the position side, plus monotonicity
- `ScoreFormat` and `ProviderKind` separated
- `has_variable_meter` as a calculated field on which the feature gate depends
- Route at `/comfyui-musical-audio/score` with the complete payload
- Ruler design question closed: time-proportional
- Analyzer evaluation with Precision/Recall/F1 rather than mean distance
- Output rename as a contract change with its own commit
- Editorial remnants cleaned up: `musical_position` → `diagnostics`, frames →
  sample indices, markers → Section boundaries

**Third self-correction:** Phase 4 was described as “from here onward, timing
is correct.” With an active meter map, the old frontend writes incorrect
selection values back—that is not cosmetic. The feature gate was added.
- Sections as a band beneath the ruler, not in the toolbar

**Two corrections to the previous revision**—both were too optimistic:

- Decision 2 affects fourteen functions, not six. The mathematical root is in
  `js/musical_grid.js`; integration additionally affects several places in
  `js/musical_audio_ui.js`. Python is unaffected.
- The `duration` widget is not dead. Eight occurrences in the frontend, one
  of them in Seconds synchronization. In Phase 0, rename only the output.

- `docs/` with both specifications, using `git mv`, without content changes
- README links to both—they do not exist yet
- Normative/descriptive precedence between specification and README defined
- Three verified contradictions: stale waveform limitation, inconsistent
  version references, missing status header in `AUDIO_INTEGRATION_SPEC.md`
