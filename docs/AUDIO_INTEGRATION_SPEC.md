# Audio Integration Contract

## Edit modes

In **Seconds** mode, `start_time` and `end_time` are the trim source of truth.
An `end_time` less than or equal to zero means the end of the decoded waveform.
The `duration` widget remains present for positional workflow compatibility and
frontend synchronization, but it never determines trim boundaries. Musical
settings provide grid metadata and the nearest musical position.

In **Musical** mode, `start_time`, `end_time`, and `duration` do not affect the
trim. The requested start and duration come from `calculate_musical_timing()`.

`snap_mode` is accepted by the backend but is reserved for a later frontend
timeline implementation and currently has no trimming effect.

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

## Output compatibility

The first three outputs remain `AUDIO`, `duration`, and `filename` in their
original order. `duration` describes the actual returned waveform length.
Additional outputs describe the actual selected range and its musical/video
grid metadata; theoretical unclamped boundaries are not exposed as node
outputs.
