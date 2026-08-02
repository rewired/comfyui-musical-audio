## Preparing your files

This node reads your music the way your DAW sees it: one timeline, one zero
point. Everything below exists to keep that true. Get it right once when you
export, and you never think about it again.

### The rule

**Everything comes from the same session and the same export range.**

That is the whole thing. The mix, the optional stems, and the MIDI file must
all start at the same point on your DAW timeline and describe the same piece
of time.

### File layout

Put the files next to each other. The node finds them by name.

```text
music/
  velvet-lies.wav              the mix — this is what you load
  velvet-lies.mid              tempo, time signatures, markers
  velvet-lies.stems/
    vocals.wav
    drums.wav
    bass.wav
    other.wav
```

If you only need the vocal stem, a single file works too:

```text
music/
  velvet-lies.wav
  velvet-lies.mid
  velvet-lies.vocals.wav
```

You load `velvet-lies.wav`. The rest is picked up automatically. There is no
second file input, on purpose — two file slots side by side are an invitation
to swap the mix and a stem, and that mistake is silent until you see the
finished render.

### Exporting the audio

Export every audio file over the **identical range**, starting at the
beginning of the project.

- **WAV.** Not MP3. MP3 encoders add padding at the start — typically around
  25 ms — so the stem no longer lines up with the mix. You will not hear it
  in isolation; you will see it in the lip sync.
- **Same sample rate** for every file.
- **Normalization off.** Per-file normalization changes the level of the
  vocal stem relative to the mix, which breaks vocal-activity detection even
  though the timing is fine.
- **No "trim silence", no "skip empty range".** Both change the start point.
- **Same range, not "selection".** If you export the mix over the full
  project and a stem over the loop range, they will not match.

In Cubase this is Channel Batch Export with the locators set once and left
alone. In Logic it is "All Tracks as Audio Files" with a fixed bounce range.
In Ableton it is "Rendered Track: All Individual Tracks" with an explicit
render start and length. In FL Studio it is "Split mixer tracks". In Bitwig
it is mixer-channel export over the arranger range. The wording differs; the
rule does not.

### Exporting the score

Export a Standard MIDI File from the same session. Format 0 or 1, PPQ
timebase. It needs to carry:

- the tempo track, including tempo changes
- the time signature track, including changes
- markers, if you want named sections

Note events are ignored. The file can be empty of music.

**Ableton Live cannot do this.** Live imports tempo maps but does not export
them. If you work in Live, skip the MIDI file and set BPM and time signature
on the node directly, or write the sidecar JSON by hand.

### Checking it worked

Two numbers have to match between the mix and every stem:

- sample count
- sample rate

That is it. If both match and the stems came out of the same session, the
alignment is correct by construction.

A stem that fails this check is **rejected, not silently used**, and the
reason appears in the node's diagnostics output. The node will never resample
or pad a mismatched stem to make it fit — a stem that is quietly stretched
into place is worse than no stem.

### If something is off

Almost every problem traces back to one of three things: a file exported over
a different range, an MP3 somewhere in the chain, or a stem separated from a
different render of the mix than the one you loaded. Re-export from the same
session and the same locators, and it will line up.
