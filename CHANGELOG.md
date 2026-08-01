# Changelog

This changelog uses a simple structure inspired by Keep a Changelog.

## [Unreleased]

No unreleased changes are currently documented.

## [0.1.1] - 2026-08-01

### Fixed

- Prevented the native audio player from collapsing in Musical mode under legacy ComfyUI node rendering.
- Prevented timeline dragging from progressively shrinking the custom node UI.
- Separated node-width synchronization from structural height calculation.
- Prevented high-frequency timeline pointer movement from triggering node resizing.
- Preserved stable layout behavior in both legacy node rendering and Node 2.0 mode.

## [0.1.0] - 2026-08-01

### Added

- Standalone `MusicalLoadAudioUI` fork
- Seconds and Musical editing modes
- BPM and tempo-unit model with Quarter, Eighth, and Dotted Quarter units
- Configurable meter, downbeat offset, video FPS, and subdivisions per beat
- Musical bar/beat/subdivision start and length selection
- Off, Bar, Beat, Subdivision, and Video Frame snap modes
- Adaptive musical ruler
- Draggable start and end range handles and center selection
- Twelve node outputs
- Pure Python timing and clip-planning modules
- Pure JavaScript musical-grid helpers
- Python and JavaScript unit test suites
- Audio file clamping and non-empty output handling

### Compatibility

- The first three outputs remain `audio`, `duration`, and `filename`.
- The original audio upload, drag-and-drop, player, and Seconds trimming
  behavior are retained.

### Limitations

- No automatic BPM detection
- No automatic downbeat detection
- No tempo maps
- No changing time signatures inside one audio file
- No swing timing
- No arbitrary tuplets beyond equal subdivisions
- No waveform visualization

### Attribution

Derived from the Load Audio UI node in
[WhatDreamsCost/WhatDreamsCost-ComfyUI](https://github.com/WhatDreamsCost/WhatDreamsCost-ComfyUI),
imported at revision `a3c809c8b593a74c2ddcd6c1f83ad85ebebe3c64`.
The original attribution and GNU GPLv3 license are retained. No endorsement by
the upstream author is implied.
