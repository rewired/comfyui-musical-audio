# Audio Integration Contract

## Status

This specification is normative for audio loading, trimming, and output
integration. Its sample-range, clamping, and `ConstantTempoMap` compatibility
rules describe the delivered behavior. The shared Score anchor, local Score
metrics, and complete Score request-error boundary below are normative for the
complete TempoMap planning profile and do not claim that profile is already
implemented in the node.

## Edit modes

In **Seconds** mode, `start_time` and `end_time` are the trim source of truth.
An `end_time` less than or equal to zero means the end of the decoded waveform.
The `duration` widget remains present for positional workflow compatibility and
frontend synchronization, but it never determines trim boundaries. Musical
settings provide grid metadata and the nearest musical position.

In **Musical** mode, `start_time`, `end_time`, and `duration` do not affect the
trim. The active timing profile owns the requested musical range. The
delivered constant and uniform-Score paths obtain it through
`calculate_musical_timing()`; complete Score planning follows the exact-end or
meter-stable duration contract in the Musical Timing Specification.

`snap_mode` is accepted by the backend for workflow compatibility. Snapping is
implemented by the frontend Musical-mode timeline and does not directly alter
backend trimming.

## Sample-based range resolution

The available audio duration is always `sample_count / sample_rate`, using the
decoded waveform's final dimension. Requested start and end times are converted
to sample indices with `round_half_away_from_zero`, then restricted to the
inclusive index bounds from zero through `sample_count`. The end index is
exclusive when slicing the waveform.

Video frame values are metadata only. They never determine sample boundaries.
After sample resolution, all returned time values are recalculated from the
selected indices:

```text
start_seconds = start_sample / sample_rate
end_seconds = end_sample / sample_rate
duration = (end_sample - start_sample) / sample_rate
```

`start_frame` and `frame_count` are then rounded half away from zero from those
actual times. Ordinary sample quantization is not file clamping.

## Clamping and non-empty audio

The returned range always remains within the source waveform. `clamped` is true
when a requested boundary is outside the source, the range is reversed, or the
range requires adjustment to remain non-empty. When a resolved range contains
zero samples, one available source sample is selected. A range at or beyond EOF
selects the final source sample rather than adding synthetic padding.

A decoded source with no samples is treated as a decode failure and uses the
node's existing one-second silence fallback before planning.

## Shared selection-start Score anchor

For every resolved Score, define exactly one shared query anchor:

```text
selection_start_score_tick
```

Its source is determined in this order:

- For an active Musical Score selection that was not clamped, use the exact
  canonical requested `start_tick`. Do not round-trip that tick through
  seconds, samples, or returned audio time.
- For any other unclamped Score selection, convert
  `plan.requested_start_seconds` with
  `resolver.audio_seconds_to_tick(...)`.
- For every clamped Score selection, convert `plan.start_seconds` with
  `resolver.audio_seconds_to_tick(...)`.

This single definition is shared by `section_name`, `seconds_per_beat`,
`frames_per_beat`, `seconds_per_bar`, and `frames_per_bar`. Those outputs must
not define separate, slightly different Score anchors.

Beat, Bar, and Section ownership is half-open. An anchor exactly on a tick
boundary belongs to the following Beat, Bar, or Section interval.

## Start-anchored local Score metrics

Time, sample, and frame boundaries describe the actual returned range. When a
Score is active, these outputs are instead start-anchored Score metadata:

```text
seconds_per_beat
frames_per_beat
seconds_per_bar
frames_per_bar
section_name
```

They are not averages across the returned selection, summaries of the
complete selected range, global Score constants, or values derived from the
legacy BPM widget while Score timing is active.

The local bar is the actual canonical bar interval containing
`selection_start_score_tick`. The local beat is the actual canonical beat
interval containing the same anchor, clipped at the actual bar end when a
shortened bar or an inside-bar meter change ends that interval early. Exact
boundaries use the half-open ownership rule above.

Let `local_beat_start_tick` and `local_beat_end_tick` be the boundaries of
that containing beat interval, and let `local_bar_start_tick` and
`local_bar_end_tick` be the boundaries of the containing bar interval. The
metadata is:

```text
seconds_per_beat =
    tick_to_seconds(local_beat_end_tick)
    - tick_to_seconds(local_beat_start_tick)

seconds_per_bar =
    tick_to_seconds(local_bar_end_tick)
    - tick_to_seconds(local_bar_start_tick)

frames_per_beat = seconds_per_beat * effective_fps
frames_per_bar  = seconds_per_bar  * effective_fps
```

The TempoMap integrates tempo events inside either tick interval piecewise.
Frame metrics remain finite floating-point metadata and are not rounded to
integer frames.

`ConstantTempoMap` retains its existing global constant metric values and the
complete version 0.1 compatibility behavior.

## Score request error boundary

A fatal Score selection error is distinct from recoverable audio decode
failure. It does not produce one-second silence, return an alternative
selection, call the clip planner, or fall back to constant timing. The node
raises the deterministic compact diagnostic JSON defined by the Musical
Timing Specification through its `ValueError` exception.

Missing audio and failed decoding continue to use the existing one-second
silence fallback and a successful diagnostics output. That recovery does not
make a structurally invalid Score selection successful.

The Score error boundary does not change sample clamping, one-sample
expansion, final-source-sample fallback, or returned-time recalculation for a
valid planned request.

## Output compatibility

The first three outputs remain `AUDIO`, `duration`, and `filename` in their
original order. `duration` describes the actual returned waveform length.
Returned time/sample/frame boundary outputs describe the actual selected
range; theoretical unclamped boundaries are not exposed as node outputs.
Start-anchored Score metadata follows the shared anchor and local-metric rules
above rather than claiming to summarize the complete returned range. The
`bpm` and `fps` outputs report the effective execution values after optional
external overrides.
