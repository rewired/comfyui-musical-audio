# Musical Timing Specification

This document defines the musical timing model used by MusicalLoadAudioUI.

## Status

This specification is normative for version 0.1.

Audio trimming remains sample-based. Video frame values are metadata and must not determine the audio sample boundaries.

## Tempo

A BPM value is interpreted together with a tempo note unit.

Version 0.1 supports:

| tempo_unit | Length in quarter notes |
|---|---:|
| Quarter | 1.0 |
| Eighth | 0.5 |
| Dotted Quarter | 1.5 |

Default:

    tempo_unit = Quarter

The tempo unit defines which note value occurs bpm times per minute.

## Time signature

The time signature is represented by:

    beats_per_bar
    beat_unit

Examples:

    4/4:
    beats_per_bar = 4
    beat_unit = 4

    6/8:
    beats_per_bar = 6
    beat_unit = 8

beat_unit defines the note value represented by one positional beat.

## Base calculations

    seconds_per_tempo_pulse =
        60.0 / bpm

Tempo-unit lengths in quarter notes:

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

seconds_per_beat always describes one positional beat according to beat_unit.

Examples:

- In 4/4, one positional beat is a quarter note.
- In 6/8, one positional beat is an eighth note.
- In 3/2, one positional beat is a half note.

## Downbeat

downbeat_offset defines the absolute audio time of:

    Bar 1 / Beat 1 / Subdivision 0

The offset is measured in seconds.

## Musical start position

Index rules:

    start_bar          is 1-based
    start_beat         is 1-based
    start_subdivision  is 0-based

The number of beats between Bar 1 / Beat 1 and the selected start position is:

    start_beats =
        (start_bar - 1) * beats_per_bar
        + (start_beat - 1)
        + start_subdivision / subdivisions_per_beat

The absolute start time is:

    start_seconds =
        downbeat_offset
        + start_beats * seconds_per_beat

## Musical duration

Duration fields are quantities and therefore start at zero:

    duration_bars
    duration_beats
    duration_subdivisions

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

## Video frame metadata

Frame calculations use the configured fps value.

    frames_per_beat =
        fps * seconds_per_beat

    frames_per_bar =
        fps * seconds_per_bar

frames_per_beat and frames_per_bar remain floating-point values. They must not be truncated to integers.

    start_frame =
        round(start_seconds * fps)

    frame_count =
        round(duration_seconds * fps)

Frame rounding is applied only when producing discrete frame indices or frame counts.

## Audio sample boundaries

Audio trimming remains sample-based.

    start_sample =
        round(start_seconds * sample_rate)

    end_sample =
        round(end_seconds * sample_rate)

The sample indices must be clamped to the valid waveform range.

Video frame rounding must not alter the audio sample boundaries.

## Golden test

Input:

    bpm = 180
    tempo_unit = Quarter
    beats_per_bar = 4
    beat_unit = 4
    fps = 24
    downbeat_offset = 0
    duration_bars = 4

Expected results:

    seconds_per_tempo_pulse = 0.333333333333...
    seconds_per_quarter     = 0.333333333333...
    seconds_per_beat        = 0.333333333333...
    seconds_per_bar         = 1.333333333333...

    frames_per_beat = 8.0
    frames_per_bar  = 32.0

    duration_seconds = 5.333333333333...
    frame_count      = 128

## Fractional-frame test

Input:

    bpm = 174
    tempo_unit = Quarter
    beats_per_bar = 4
    beat_unit = 4
    fps = 24
    downbeat_offset = 0

Expected values include:

    seconds_per_beat =
        60 / 174
        = 0.344827586206...

    frames_per_beat =
        24 * 60 / 174
        = 8.275862068965...

frames_per_beat must remain fractional.

Rounding is applied only to discrete frame indices and frame counts.

## Compound-meter example

Input:

    bpm = 90
    tempo_unit = Dotted Quarter
    beats_per_bar = 6
    beat_unit = 8

Expected results:

    seconds_per_tempo_pulse = 0.666666666667
    seconds_per_quarter     = 0.444444444444
    seconds_per_beat        = 0.222222222222
    seconds_per_bar         = 1.333333333333

A 6/8 bar therefore contains two dotted-quarter tempo pulses.

## Version 0.1 exclusions

Version 0.1 does not include:

- automatic BPM detection
- automatic downbeat detection
- tempo maps
- changing time signatures
- swing timing
- tuplets beyond equal subdivisions
- external music-analysis dependencies