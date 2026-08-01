# ComfyUI Musical Audio

ComfyUI Musical Audio provides the **Load Audio UI — Musical Grid** node, a
ComfyUI audio loader and trimmer with ordinary seconds-based editing and a
musical bar/beat/subdivision grid. It includes audio loading and playback,
frame-aware metadata, and a draggable custom selection timeline. Version 0.1
does not detect BPM automatically; tempo and grid alignment are configured by
the user.

## Features

- Seconds and Musical editing modes
- BPM with a configurable tempo note unit
- Quarter, Eighth, and Dotted Quarter tempo units
- Configurable time signature
- Configurable downbeat offset
- Configurable subdivisions per beat
- Configurable video FPS
- Snapping modes: Off, Bar, Beat, Subdivision, and Video Frame
- Adaptive musical ruler
- Audio playback limited to the active selection
- Drag-and-drop audio upload
- Draggable start and end handles and selection body
- Sample-based audio trimming
- Deterministic half-away-from-zero rounding
- Actual-range metadata after file clamping
- Compatibility with ComfyUI's standard `AUDIO` output

Automatic BPM and downbeat detection are not included.

## Installation

Clone the repository into the `custom_nodes` directory of your ComfyUI
installation:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/rewired/comfyui-musical-audio.git
```

Restart ComfyUI completely after installation. If the browser has cached an
older JavaScript file, refresh the ComfyUI page with <kbd>Ctrl</kbd>+<kbd>F5</kbd>.

No separate `pip` or `npm` installation is required beyond the dependencies
already supplied by ComfyUI. The node uses PyAV and Torch through the ComfyUI
environment.

## Updating

From the cloned repository, pull the latest changes:

```bash
git pull
```

Then restart ComfyUI completely and refresh the browser with
<kbd>Ctrl</kbd>+<kbd>F5</kbd> so updated frontend code is loaded.

## Usage

Add **Load Audio UI — Musical Grid** to a workflow and select or upload an
audio file. The mode control determines which fields define the active range.

### Seconds mode

- **Start** and **End** define the trim range in seconds.
- **End = 0** means the end of the file.
- **Duration** is synchronized with the active range.
- Snap can use the configured musical grid or video frame rate.

### Musical mode

Musical mode defines the range using grid and selection fields.

Grid fields:

- **BPM** — tempo value paired with the selected tempo unit
- **Tempo unit** — Quarter, Eighth, or Dotted Quarter
- **Meter numerator** — number of positional beats in each bar
- **Meter denominator** — note value represented by one positional beat
- **Downbeat offset** — audio time of the first downbeat
- **FPS** — video frame rate used for metadata and frame snapping
- **Subdivisions per beat** — equal divisions available within each beat

Selection fields:

- **Start Bar**
- **Start Beat**
- **Start Subdivision**
- **Length Bars**
- **Length Beats**
- **Length Subdivisions**

Bar and Beat indexing begins at 1. Subdivision indexing begins at 0. The
downbeat offset is the absolute audio time of **Bar 1 / Beat 1 / Subdivision
0**.

## Tempo interpretation

BPM alone is not sufficient to define the timing grid. BPM is paired with a
tempo note unit:

- **Quarter** means the quarter note occurs BPM times per minute.
- **Eighth** means the eighth note occurs BPM times per minute.
- **Dotted Quarter** means the dotted-quarter note occurs BPM times per minute.

The `beat_unit` value remains the time-signature denominator and defines the
note value represented by one positional beat.

At **180 BPM, Quarter, 4/4, and 24 FPS**:

- One beat is 0.333333 seconds.
- One beat is 8 frames.
- One bar is 1.333333 seconds.
- One bar is 32 frames.
- Four bars are 128 frames.

At **90 BPM, Dotted Quarter, and 6/8**, one 6/8 bar contains two
dotted-quarter tempo pulses and lasts 1.333333 seconds.

## Outputs

The node provides these outputs in this exact order:

1. `audio` — `AUDIO`
2. `duration` — `FLOAT`
3. `filename` — `STRING`
4. `start_seconds` — `FLOAT`
5. `end_seconds` — `FLOAT`
6. `start_frame` — `INT`
7. `frame_count` — `INT`
8. `seconds_per_beat` — `FLOAT`
9. `frames_per_beat` — `FLOAT`
10. `seconds_per_bar` — `FLOAT`
11. `frames_per_bar` — `FLOAT`
12. `musical_position` — `STRING`

`duration`, `start_seconds`, `end_seconds`, `start_frame`, and `frame_count`
describe the actual returned audio range after it has been clamped to the
file. `frames_per_beat` and `frames_per_bar` may be fractional. Video frame
metadata does not determine the audio sample boundaries; trimming remains
sample-based.

## Clamping and zero-length selections

Requested ranges are restricted to the available waveform. The node never
returns an empty `AUDIO` tensor: a zero-length or fully out-of-range request
resolves to one valid source sample where possible. Because frame values are
rounded independently as metadata, `frame_count` may be zero for a non-empty
one-sample audio result.

## Compatibility

The first three outputs preserve the `audio`, `duration`, `filename` order of
the renamed base fork. Existing workflows using the renamed
`MusicalLoadAudioUI` base node should remain loadable. The node returns
ComfyUI's standard `AUDIO` structure.

Compatibility with every ComfyUI release is not guaranteed.

## Development

Run the Python and JavaScript checks from the repository root:

```bash
python -m unittest discover -s tests -v
python -m py_compile musical_timing.py audio_clip_plan.py musical_audio_ui.py
node --test tests_js/test_musical_grid.mjs
node --check js/musical_audio_ui.js
node --check js/musical_grid.js
```

No third-party JavaScript package installation is required.

## Version 0.1 limitations

- No automatic BPM detection
- No automatic downbeat detection
- No tempo maps
- No changing time signatures inside one audio file
- No swing timing
- No arbitrary tuplets beyond equal subdivisions
- No waveform visualization

## Attribution and license

This project is derived from the Load Audio UI node in
[WhatDreamsCost/WhatDreamsCost-ComfyUI](https://github.com/WhatDreamsCost/WhatDreamsCost-ComfyUI).
The imported base revision is
`a3c809c8b593a74c2ddcd6c1f83ad85ebebe3c64`.

The original attribution and GNU GPLv3 license are retained. This repository
is licensed under the GNU General Public License version 3; refer to
[LICENSE](LICENSE) for the complete terms. This project does not imply
endorsement by the upstream author.
