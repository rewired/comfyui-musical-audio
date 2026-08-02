# Score-Subsystem: Tempo-Map und Marker

> **Status: Architecture frozen for implementation**
> **Revision: 1** — Stand Repo `604e5ab`, Branch
> `feature/css-theme-foundation-v0.2.0`
>
> S1–S10 sind Implementierungs- und Testvertrag, keine Roadmap. Änderungen
> daran erfordern ein ausdrückliches Amendment an dieser Spezifikation, nicht
> eine Entscheidung zur Implementierungszeit. Die Phasen und der Waveform-Strang
> bleiben planbar und dürfen umsortiert werden.

Architekturplan für `comfyui-musical-audio`.

## Grundidee

Tempo-Map und Marker sind kein zweites Feature — sie kommen aus derselben
MIDI-Datei, aus demselben Parse-Durchlauf, und beschreiben dasselbe Ding: was
die DAW über die Zeitachse weiß.

Also ein Subsystem, nicht zwei Features. Der gemeinsame Nenner heißt hier
**Score**.

```
MIDI-Datei ──parse────┐
Audioanalyse ─────────┼──▶ Score ──┬──▶ TempoMap    (Position ↔ Sekunden)
BPM + Taktart ────────┘            ├──▶ MeterMap    (Tick ↔ Takt/Beat)
                                   └──▶ Sections    (benannte Bereiche)
```

Drei Quellen, ein Datentyp. Alles hinter `Score` weiß nicht, woher die Zahlen
kommen — und darf es auch nicht wissen müssen.

Dazu quer eine zweite, unabhängige Achse: **Material**. Der Score sagt *wann*
geschnitten wird, das Material *was* geschnitten wird. Ein Mix ist eine Spur,
ein Stem-Satz sind fünf — an der Zeitachse ändert das nichts.

```
Score      ──▶ Plan (Ticks)  ──apply──▶ Segmente
Material   ──▶ Spuren (Samples) ───────┘
```

---

## Die sechs Entscheidungen, die alles andere festlegen

### 1. Ticks sind die kanonische Einheit, nicht Sekunden

MIDI denkt in Ticks. Taktgrenzen sind dort exakte Ganzzahlen — ein 31/32-Takt
ist präzise 1860 Ticks, kein gerundeter Float. Die Umrechnung nach Sekunden
passiert **einmal, an der Außengrenze**.

Konsequenz: intern rechnet alles in Ticks. Erst der Output konvertiert.

Für die Audioanalyse dreht sich das um: die liefert *nur* Sekunden. Sie muss
in Ticks zurückrechnen, bevor sie einen `Score` ausgeben darf — siehe
Fallstrick „Sekunden sind keine Ticks". Das ist Aufwand auf ihrer Seite, aber
er hält den Rest des Systems frei von Sonderfällen.

### 2. Der lineare Subdivision-Index muss weg

Das ist der tiefste Eingriff, und er ist unvermeidbar.

`musicalPositionToSubdivisionIndex` rechnet heute
`((bar-1) * beatsPerBar + (beat-1)) * subdivisionsPerBeat + subdivision`.
Das setzt voraus, dass `beatsPerBar` global konstant ist. Mit einer Meter-Map
ist es eine Funktion des Taktes — die Formel wird schlicht falsch.

Ersatz: **Position ↔ Tick ↔ Sekunden.** Der lineare Index war immer nur eine
Bequemlichkeit für gleichmäßige Raster.

**Der Index existiert nur im Frontend, aber nicht nur in einer Datei.**
`musical_timing.py` kennt ihn nicht — dort steht nur
`calculate_musical_timing`, und die rechnet bereits über Sekunden. Python
braucht in Entscheidung 2 also gar nichts.

`js/musical_audio_ui.js` importiert allerdings
`subdivisionIndexToMusicalPosition`, `subdivisionIndexToSeconds` und
`timingGrid` direkt und baut damit **das Lineal** (Zeile 1611), die
Zeitauflösung (1115) und die Selektionsgrenzen (1151/1152). Der Eingriff
liegt damit in `js/musical_grid.js` (305 Zeilen) *und* an rund einem halben
Dutzend Stellen in `js/musical_audio_ui.js` (2391 Zeilen).

Betroffen, vollständig:

| Funktion | Rolle |
|---|---|
| `gridShape` | **Wurzel des Problems** — kapselt `beatsPerBar * subdivisionsPerBeat` |
| `musicalPositionToSubdivisionIndex` | Hin |
| `subdivisionIndexToMusicalPosition` | Zurück |
| `durationFieldsToSubdivisionCount` | Dauer hin |
| `subdivisionCountToDurationFields` | Dauer zurück |
| `subdivisionIndexToSeconds` | Index → Zeit |
| `musicalPositionToSeconds` | Position → Zeit |
| `secondsToNearestSubdivision` | Zeit → Index |
| `frameToNearestSubdivision` | Frame → Index |
| `secondsRangeToMusicalSelection` | Selektion hin |
| `musicalSelectionToSecondsRange` | Selektion zurück |
| `timingGrid` | Konfigurationsobjekt, das die Shape trägt |
| `snapToBar` / `snapToBeat` / `snapToSubdivision` | Snapping über `snapRelative` |

Vierzehn Funktionen, nicht sechs. Wer `gridShape` sauber durch eine
Tick-Auflösung ersetzt, hat den Großteil davon allerdings mitbehandelt — die
meisten sind dünne Hüllen darüber.

### 3. Die Taktabelle wird vorberechnet

Das Lineal fragt bei jedem Repaint hunderte Positionen ab. Eine Auflösung
über Event-Listen pro Aufruf ist zu langsam.

Beim Parsen einmal `bar_start_ticks[]` aufbauen, danach ist Lookup O(1).

### 4. Rückwärtskompatibilität ist der Testvertrag

`ConstantTempoMap` muss **alle bestehenden Tests unverändert** bestehen. Wenn
ein Test angepasst werden muss, ist der Umbau schiefgegangen. Das ist das
Sicherheitsnetz für den ganzen Rest.

### 5. Score-Quellen sind eine Kette, kein Sonderfall

Das ist die Entscheidung, die den Analyzer später möglich macht, ohne dass
heute eine Zeile davon geschrieben wird.

Die Herkunft eines `Score` ist ein **Feld auf dem Datentyp**, und die Auswahl
der Quelle ist eine **geordnete Provider-Liste**, kein `if midi_exists:`.
Das kostet in Phase 2 fünf Minuten. Nachträglich eingezogen kostet es einen
Umbau von Discovery, Route, Node-Output und Testkorpus gleichzeitig.

```python
PROVIDERS = (
    ExplicitFileProvider,   # score_file-Input
    SidecarJsonProvider,    # <name>.score.json
    MidiSidecarProvider,    # <name>.mid
    AnalysisProvider,       # Audioanalyse       ← Stub, Phase 8
    ConstantProvider,       # BPM + Taktart      ← immer erfolgreich
)
```

Erster Provider, der einen `Score` liefert, gewinnt. `ConstantProvider` ist
Terminator, damit die Kette nie leer zurückkommt und der Fehlerpfad nicht
zweimal existiert.

### 6. Der Plan ist einspurig, das Material ist mehrspurig

`audio_clip_plan` produziert eine Schnittliste aus Ticks und Frames. Diese
Liste ist **eine**, egal wie viele Spuren daran hängen. Sie auf fünf Stems
anzuwenden ist eine Schleife am Ausgang, keine zweite Planung.

Die Regel, die das absichert: **Sampleindizes werden pro Spur aus dem Plan
abgeleitet, nicht der Plan pro Spur neu gerechnet.** Zur Wortwahl siehe S6 —
`frame` heißt in diesem Projekt Video-Frame. Sonst hat ein 48-kHz-Mix
mit einem 44,1-kHz-Stem zwei minimal verschobene Schnittlisten, und das hörst
du als Klick, nachdem du zwanzig Minuten woanders gesucht hast.

Konsequenz für die Signatur: `apply_plan(plan, track) -> Segments`, aufgerufen
je Spur. Nicht `plan_for(track)`.

---

## Datenmodell

```python
# Was der Score enthält — vs. wie er gefunden wurde. Zwei Fragen, zwei Typen.
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
    end_tick_exclusive: int    # halboffen, siehe S5
    bar_aligned: bool          # liegt der Anfang exakt auf einer Taktgrenze?
    confidence: float | None = None   # None = gesetzt statt geschätzt

@dataclass(frozen=True)
class Score:
    ticks_per_quarter: int
    tempos: tuple[TempoEvent, ...]
    meters: tuple[MeterEvent, ...]
    markers: tuple[Marker, ...]    # Rohpunkte aus der DAW
    sections: tuple[Section, ...]  # kanonische Annotation, siehe S4
    source: ScoreFormat
    meter_estimated: bool = False        # Taktart geraten statt gelesen
    has_variable_meter: bool = False     # wirklich wechselnd, siehe unten
    has_midbar_meter_change: bool = False

@dataclass(frozen=True)
class ResolvedScore:
    score: Score
    audio_seconds_at_tick_zero: float   # siehe S1
    provider: ProviderKind
```

`start_bar`, `end_bar` und `bar_count` stehen bewusst **nicht** auf `Section`
— sie sind abgeleitete Anzeigewerte und kommen aus dem Resolver. Bei einer
Section, die mitten im Takt beginnt, wäre ein gespeichertes `bar_count`
irreführend.

`bar_starts` steht ebenfalls nicht mehr auf `Score`: abgeleitet, nicht
serialisiert, Resolver-interner Cache. Siehe S8.

`source` und `provider` sehen redundant aus, sind es aber nicht: ein
explizit gesetztes `score_file` kann MIDI **oder** JSON sein. `source="midi",
provider="explicit"` ist eine sinnvolle Kombination, `source="explicit"` wäre
eine Kategorienverwechslung.

`has_variable_meter` wird aus den **wirksamen** Signaturen berechnet, nicht
aus `len(meters)`. Zwei 4/4-Events an Tick 0 und 9600 sind kein
Taktartwechsel; ein einzelnes Event an einem ungewöhnlichen Tick kann
umgekehrt einen partiellen Takt erzeugen. Genau deshalb ist es ein Feld und
keine Ad-hoc-Prüfung an der Aufrufstelle.

Zwei Felder mehr als nötig, beide für später:

`confidence` unterscheidet „der Nutzer hat hier einen Marker gesetzt" von
„ein Algorithmus vermutet hier eine Grenze". `None` ist dabei nicht
„unbekannt", sondern **„Frage stellt sich nicht"** — bei MIDI ist der Marker
Fakt. Das UI kann darauf später unterschiedlich reagieren, ohne `source`
durchreichen zu müssen.

`meter_estimated` deckt den Fall ab, dass die Taktart nicht aus der Datei
kommt. Ein 31/32-Takt ist aus Audio nicht erkennbar, ein 3/4 gegen 4/4 nur
mit Glück. Wer die Zahl anzeigt, soll wissen, wie belastbar sie ist.

### Material

Bewusst außerhalb von `Score`. Der Score ist eine Zeitachse und bleibt frei
von Samples — sonst kann ihn das Frontend nicht mehr über die Route holen und
der Analyzer nicht mehr als reine Funktion getestet werden.

```python
@dataclass(frozen=True)
class Stem:
    name: str                  # "mix", "vocals", "drums", "bass", "other"
    samples: np.ndarray        # (channels, n)
    sample_rate: int

@dataclass(frozen=True)
class StemSet:
    mix: Stem
    extra: Mapping[str, Stem]  # leer = einspuriger Normalfall
```

`mix` ist Pflicht und ist die Referenz für Länge und Ausrichtung. Alles in
`extra` wird gegen ihn geprüft, nicht gegeneinander.

---

### Resolver-API

```
tick_to_seconds(tick)            -> float
seconds_to_tick(seconds)         -> float
bar_to_tick(bar)                 -> int      # 1-basiert
tick_to_position(tick, spb)      -> (bar, beat, subdivision)
position_to_tick(bar, beat, sub, spb) -> int
meter_at_bar(bar)                -> (numerator, denominator)
sections()                       -> tuple[Section, ...]
```

`spb` = subdivisions_per_beat.

Die API ist quellenunabhängig. Das ist der ganze Punkt: Phase 6 (`Musical
SegmentBatch`) und das Lineal rufen sie auf, ohne je zu erfahren, ob dahinter
Cubase oder eine Self-Similarity-Matrix steckt.

---

## Fünf Fallstricke, die vorher festgeklopft gehören

### Extrapolation über das letzte Event hinaus

Cubase schreibt die Tempospur nur bis zum letzten Event. Beim Testexport
endete die Datei bei Takt 169, obwohl der Track 233 Takte hat.

**Regel:** `bar_starts` wird bis zum letzten Event vorberechnet und bei
Resolver-Erzeugung bis zur Audiodauer verlängert (S8). Danach
arithmetisch extrapoliert mit der zuletzt gültigen Taktart und dem zuletzt
gültigen Tempo. Kein Fehler, kein Abbruch — das ist der Normalfall.

### Tempowechsel innerhalb eines Taktes

`tick_to_seconds` muss stückweise integrieren, nicht mit einem globalen
Faktor multiplizieren. Der aktuelle Track hat keine Tempowechsel, aber der
Parser darf daran nicht scheitern.

### Marker liegen nicht zwangsläufig auf Taktgrenzen

Nicht snappen. Exakten Tick behalten, den enthaltenden Takt als `start_bar`
ausweisen, und über `bar_aligned` sichtbar machen, dass da etwas nicht
aufgeht. Stilles Verschieben von Nutzerdaten ist die schlechtere Variante.

### Sekunden sind keine Ticks

Betrifft nur den Analyzer, aber die Regel gehört hierher, weil sie die
Tick-Kanonik aus Entscheidung 1 berührt.

Audioanalyse liefert Beatpositionen in Sekunden. Daraus einen `Score` zu
bauen heißt, ein Tempo zu *erfinden*, das diese Sekunden reproduziert. Zwei
Wege, beide legitim:

- **`constant`** — Median der Beat-Abstände, ein einziges `TempoEvent` bei
  Tick 0. Sauberes Raster, driftet bei live eingespieltem Material über die
  Tracklänge auseinander.
- **`follow`** — ein `TempoEvent` pro erkanntem Beat. Bleibt am Audio kleben,
  erzeugt aber eine Tempospur mit hunderten Events, die im Lineal als
  zappelnde Taktbreiten sichtbar wird.

**Regel:** `constant` ist Default, `follow` hinter einem Schalter, und die
Beat-Abstände werden vor beidem medianfiltert. Wer `follow` wählt, hat einen
Grund.

### Stems liegen nicht zwangsläufig sample-genau

Demucs-Output tut es, ein von Hand aus der DAW gebouncter Stem mit anderem
Startpunkt nicht. Und der Fehler ist **stumm** — du siehst ihn erst am
fertigen Lipsync, nach dem Rendern.

**Regel:** Samplezahl und Samplerate jedes Stems gegen `mix` prüfen. Abweichung
in der Länge über einer Toleranz von wenigen Millisekunden oder abweichende
Samplerate → Stem verwerfen und den Grund nach `diagnostics` schreiben (S7,
Phase 4). Also in den sichtbaren Fehlerpfad, nicht in eine Konsolenzeile.
Nicht stillschweigend resampeln, nicht stillschweigend padden.

---

## Verbindliche Semantik

Zehn Festlegungen, die vor Phase 2 stehen müssen. Jede einzelne ist im Nachhinein
teuer, weil sie im Datenmodell oder im Testkorpus hängt.

### S1 — Score und Audio sind zwei Dinge

Bei welcher Audiosekunde liegt Tick 0? Nicht automatisch bei `0.0`: Vorlauf,
Count-in, Export ab Locator, MIDI und Audio mit verschiedenen Startpunkten,
negative Offsets.

Die Ausrichtung gehört **nicht in `Score`**, weil derselbe Score mit mehreren
Audioexporten benutzbar bleiben soll:

```python
@dataclass(frozen=True)
class ResolvedScore:
    score: Score
    audio_seconds_at_tick_zero: float
    provider: ProviderKind
```

Die Gleichung gehört hierher, weil sie sonst irgendwo umgedreht wird:

```text
audio_seconds  = score.tick_to_seconds(tick) + audio_seconds_at_tick_zero
score_seconds  = audio_seconds - audio_seconds_at_tick_zero
```

Das existierende `downbeat_offset`-Widget spielt diese Rolle bereits, und das
Vorzeichen stimmt **exakt**: `audio_clip_plan.py` Zeile 86 rechnet
`(start_seconds - downbeat_offset)`, also `score_seconds = audio_seconds -
downbeat_offset`. Damit gilt ohne Umrechnung

```text
audio_seconds_at_tick_zero == downbeat_offset
```

Der negative Fall ist dort bereits behandelt — `_nearest_grid_position` gibt
für negative Werte „n subdivisions before Bar 1 · Beat 1" aus. Vorlauf und
Count-in sind also kein neuer Sonderfall, sondern bestehendes Verhalten.

Im Constant-Fallback wird der Wert unverändert übernommen; bei MIDI ist er
der Startwert, den der Nutzer weiter korrigieren darf.

### S2 — Ticks sind ganzzahlig, Rasterpositionen werden gerundet

`ticks_per_quarter = 480`, `subdivisions = 7` → `480/7 = 68,571…`. Nicht jede
Unterteilung landet auf einem Integer-Tick.

**Gegen rationale Tickpositionen** (`Fraction`) im Kern, obwohl sie exakt
wären: Das Frontend kann sie nicht spiegeln. `js/score.js` rechnet mit
`Number`, und damit läge die Exaktheit genau dort, wo sie niemand sieht,
während die sichtbare Seite driftet — im Widerspruch zur Paritätsforderung
aus S10.

**Regel:** Integer-Ticks überall, plus eine *identische, deterministische*
Rundungsregel in beiden Sprachen. Die gibt es bereits: `round_half_away_from_zero`
in `musical_timing.py` und `roundHalfAwayFromZero` in `js/musical_grid.js`. Das
Paritätsprimitiv ist gebaut und getestet; es muss nur benutzt werden.

Ergänzend `ticks_per_quarter` bei synthetischen Scores auf **960** setzen —
teilbar durch 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32, 48, 60, 64, 80, 96,
120, 160, 192, 240, 320, 480. Damit ist die Rundung bei allen üblichen
Rastern ohnehin ein Nulloperation. Bei MIDI gilt die TPQ der Datei.

Typmodell benennt das ausdrücklich: `Tick = int` für Events,
`seconds_to_tick() -> float` für Abfragen, gerundet wird genau an den
Rändern zu MIDI, Sample und Video-Frame.

**Gerundet wird absolut, nie kumulativ.** Das ist die eigentliche Falle. Sieben
Subdivisions als gerundete Breite siebenmal addiert ergibt
`round(480/7) × 7 = 69 × 7 = 483` statt 480 — Drift trotz Integer-Ticks.

```python
tick = beat_start_tick + round_half_away_from_zero(
    subdivision * ticks_per_beat / subdivisions_per_beat
)
```

Dasselbe für Taktgrenzen: immer vom letzten kanonischen Meter-Event-Anker
rechnen, niemals gerundete Taktlängen wiederholt aufaddieren. Die
Rundungsfunktion ist damit an jeder Stelle **einmal** im Spiel, nie in einer
Schleife.

**Zu feine Raster werden abgelehnt.** Bei `subdivisions_per_beat >
ticks_per_beat` fallen zwei Subdivisions auf denselben Tick. Das ist kein
Rundungsproblem mehr, sondern ein ungültiges Raster — als `invalid` melden
oder hart auf `ticks_per_beat` begrenzen. Bei TPQ 960 liegt die Grenze so
hoch, dass sie praktisch nie greift; ein MIDI mit TPQ 96 erreicht sie aber.

### S3 — Meterwechsel mitten im Takt erzeugt einen verkürzten Takt

Exakt behalten, partiellen Takt erzeugen, Diagnose ausgeben, **niemals still
verschieben** — dieselbe Haltung wie bei nicht ausgerichteten Markern.

Dazu die Parserdefaults, die sonst irgendwo implizit entstehen:

| Fall | Regel |
|---|---|
| kein initiales Tempo | 120 BPM (MIDI-Default) |
| keine initiale Taktart | 4/4 |
| mehrere Events am selben Tick | letztes gewinnt |
| Events aus mehreren Tracks | alle mergen, stabil nach `(tick, track_index, event_index)` |
| SMPTE-Division statt PPQ | ablehnen, `INVALID` mit Diagnose |

### S4 — Sections sind kanonisch, Marker sind Rohdaten

`Score` bekommt **beide**: `markers` als das, was in der DAW steht, und
`sections` als die Annotation, die editiert, analysiert und serialisiert wird.

Rein abgeleitete Sections wären billiger, aber ein von Hand korrigiertes
Section-Ende, das auf keinem Marker liegt, ließe sich dann nicht verlustfrei
speichern — und genau das ist der Zweck des JSON-Sidecars.

Randfälle, die festliegen müssen: zwei Marker am selben Tick, leerer Name,
Marker vor dem ersten Takt, Marker exakt am Trackende, Section der Länge
null, Section mit `bar_aligned = false`.

`start_tick` und `end_tick_exclusive` sind exakt und kanonisch. `start_bar`,
`end_bar` und `bar_count` sind **abgeleitete Anzeigewerte** — bei einer
Section, die mitten im Takt beginnt, ist `bar_count` sonst irreführend.

### S5 — Alle Intervalle sind halboffen

```text
start_tick    end_tick_exclusive
start_sample  end_sample_exclusive
start_frame   end_frame_exclusive
```

mit `frame_count = end_frame_exclusive - start_frame`. Der neue Output aus
Phase 4 heißt entsprechend `end_frame_exclusive`, nicht `end_frame`. Kostet
sechs Zeichen und erspart die Frage, ob der letzte Frame drin ist —
verkettete Schnittlisten und leere Bereiche werden dadurch trivial.

### S6 — Vier Zeitbegriffe, keine Überladung

`frame` bedeutet in diesem Projekt bereits **Video**-Frame. Deshalb:

```text
tick          musikalische Zeit
seconds       reale Zeit
video_frame   FPS-basierte Bildposition
sample_index  Position in einer Audiospur
```

Entscheidung 6 heißt damit präzise: *Sampleindizes* werden pro Spur aus dem
Plan abgeleitet, nicht „Frames".

### S7 — Provider melden drei Zustände

„Erster Provider mit Score gewinnt" reicht für Fehler nicht. Eine kaputte,
**ausdrücklich angegebene** Datei darf nicht stillschweigend im
Constant-Fallback landen — der Nutzer sucht den Fehler sonst später im Timing.

```python
@dataclass(frozen=True)
class ProviderResult:
    status: Literal["not_applicable", "found", "invalid"]
    score: Score | None
    diagnostics: tuple[str, ...]
    provenance: Mapping[str, str]
```

`not_applicable` → weitergehen. `found` → Kette endet. `invalid` → Kette
endet **mit Fehler**, kein Fallback. Kein `.mid` vorhanden ist
`not_applicable`; ein vorhandenes, kaputtes `.mid` ist `invalid`.

### S8 — `bar_starts` wird nicht serialisiert

Es ist aus TPQ, Meter-Events und Extrapolationsziel ableitbar. Steht es
editierbar neben `meters` im JSON, können beide sich widersprechen.

JSON speichert kanonische Events und Annotationen. Der Resolver baut
`bar_starts` als internen Cache. Die Route darf es ans Frontend liefern —
dort ist es Transportoptimierung, keine Wahrheit.

Und die Vorberechnung endet nicht am letzten Event, sondern wird bei
Resolver-Erzeugung bis zur **Audiodauer** verlängert. Ein früh endender
Export ist der Normalfall, nicht die Ausnahme.

Präzisierung zu Entscheidung 3: `bar_to_tick` ist O(1), `tick_to_position`
ist eine Binärsuche über `bar_starts` und damit O(log n). Schnell genug, aber
das Versprechen gehört richtig formuliert.

### S9 — Der Cache-Fingerprint umfasst die Konfiguration

Quelldatei plus mtime reicht für Analyzer-Ergebnisse nicht. Analyzer-Version,
Algorithmusversion, `beats_per_bar`, `tempo_mode`, `target_sections` und
Schwellwerte verändern das Ergebnis genauso.

```json
{
  "schema_version": 1,
  "generator": { "name": "...", "version": "...",
                 "algorithm_version": 1, "config_fingerprint": "..." },
  "derived_from": { "filename": "...", "size": 123, "mtime_ns": 456 },
  "edited": false
}
```

Bei `edited: true` gewinnt die Datei weiterhin — aber mit Diagnose, wenn das
Audio sich seither geändert hat.

### S10 — Python und JavaScript teilen ein Golden-Korpus

`score/tempo_map.py` und `js/score.js` implementieren dieselbe Mathematik.
Das ist die klassische Driftstelle, und das Repo hat mit `tests/` und
`tests_js/` bereits zwei parallele Suiten, die genau dafür gebaut sind.

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

Beide Sprachen prüfen dieselben Fixtures auf Tick↔Sekunden, Bar↔Tick,
Position↔Tick, Snapping auf Takt/Beat/Subdivision, Extrapolation und
Video-Frame-Rundung. Dazu die Invarianten — und die müssen richtig herum
formuliert sein:

```text
tick_to_position(position_to_tick(p)) == p          für jede gültige Position
position_to_tick(tick_to_position(x))   ist die nach S2 nächstgelegene
                                        Rastergrenze, nicht zwingend x
tick_to_seconds(seconds_to_tick(x))     ≈ x
Monotonie: p1 < p2  ⟹  position_to_tick(p1) < position_to_tick(p2)
Python und JavaScript liefern für dieselbe Fixture identische Grenzwerte
```

Der Roundtrip ist nur von der **Positionsseite** verlustfrei. Ein beliebiger
MIDI-Tick zwischen zwei Rasterpunkten wird beim Umweg über eine musikalische
Position zwangsläufig quantisiert — das ist korrektes Verhalten, kein Fehler,
und ein Test, der Gleichheit fordert, würde eine richtige Implementierung
ablehnen. Die Monotonieprüfung fängt dafür die Fehler, die eine reine
Roundtrip-Prüfung durchlässt.

Deterministisch erzeugte Zufalls-Fixtures für Tempo- und Meter-Maps sind
zusätzlich sinnvoll und brauchen keine Property-Testing-Bibliothek.

---

## Dateilayout

Das Repo ist heute **flach** — alle Module liegen im Wurzelverzeichnis,
`__init__.py` importiert `MusicalLoadAudioUI` und ruft
`register_waveform_routes()` auf. `score/` und `material/` als Pakete sind
damit eine bewusste Neuerung, keine Fortsetzung. Die Alternative wären
`score_model.py`, `score_parse.py` usw. auf gleicher Ebene; bei der
absehbaren Dateizahl gewinnen Pakete, aber die Entscheidung gehört benannt
statt vorausgesetzt.

Bestand, der hier mitgedacht werden muss:

| Datei | Rolle heute |
|---|---|
| `waveform_routes.py` (318 Z.) | Route + ETag + Bounded Cache. **Vorbild für `score/routes.py`** |
| `waveform_peaks.py` (521 Z.) | Peaks-Berechnung, rein |
| `js/musical_audio_ui.js` (2308 Z.) | Node-UI |
| `js/musical_grid.js` (305 Z.) | Der Ort von Entscheidung 2 |
| `js/musical_audio_ui.css` (19 KB) | Theme, Gegenstand des aktuellen Branches |
| `js/waveform_loader.js`, `js/waveform_peaks.js`, `js/metronome.js` | unberührt |

Neu bzw. geändert:

| Datei | Inhalt | Rein? |
|---|---|---|
| `docs/MUSICAL_TIMING_SPEC.md` | verschoben in Phase 0.5, erweitert in Phase 1 | — |
| `docs/AUDIO_INTEGRATION_SPEC.md` | verschoben in Phase 0.5 | — |
| `score/midi_parse.py` | `bytes` → `Score`. Kein I/O, kein ComfyUI. | ja |
| `score/model.py` | Dataclasses + Resolver | ja |
| `score/tempo_map.py` | `TempoMap`-Protokoll, `ConstantTempoMap`, `ScoreTempoMap` | ja |
| `score/serialize.py` | `Score` ↔ JSON, beide Richtungen | ja |
| `score/analyze.py` | Samples → `Score`. **Stub, Phase 8** | ja |
| `score/naming.py` | Namensstamm, Wiederholungszähler, Seed-Ableitung | ja |
| `material/stems.py` | `Stem`/`StemSet`, Ausrichtungsprüfung | ja |
| `material/energy.py` | RMS-Hüllkurven, Perzentil-Gate, Onsets. **Phase 9** | ja |
| `musical_timing.py` | bekommt optionalen `tempo_map`-Parameter | ja |
| `audio_clip_plan.py` | reicht `tempo_map` durch, `apply_plan` je Spur | ja |
| `score/providers.py` | Provider-Kette, Sidecar-Suche, mtime-Cache | nein |
| `material/discovery.py` | Stem-Sidecars und `.stems/`-Ordner finden | nein |
| `musical_audio_ui.py` | Node, `IS_CHANGED`, Fehlerpfad, neue Outputs | nein |
| `score/routes.py` | aiohttp-Route für das Frontend | nein |
| `js/score.js` | JS-Spiegel des Resolvers | — |
| `js/musical_grid.js` | Umstellung auf Tick-Basis | — |

Die Trennung rein/unrein ist bereits dein Muster — hier wird sie nur
fortgesetzt. `analyze.py` fällt dabei auf die reine Seite: Samples rein,
`Score` raus. Modellladen, Caching und Dateizugriff bleiben im Provider.

`score/discovery.py` heißt jetzt `providers.py`, weil es nicht mehr sucht,
sondern eine Kette abarbeitet.

---

## Score-Herkunft

**Sidecar-Konvention:** `velvet-lies.wav` → `velvet-lies.mid` im selben
Verzeichnis. Automatisch, keine UI.

**Override:** optionaler String-Input `score_file` für abweichende Namen.

**JSON-Sidecar:** `velvet-lies.score.json` ist die Serialisierung von `Score`.
Drei Rollen in einer Datei:

- Cache für Analyseergebnisse (die kosten Sekunden, nicht Millisekunden)
- von Hand editierbare Sections, wenn die Automatik danebenliegt
- Austauschformat, falls mal ein anderes Tool Marker liefert

Damit Cache und Handarbeit nicht kollidieren: Die Datei trägt `derived_from`
(Quelldatei + mtime) und `edited: bool`. Ist die Quelle neuer und `edited`
nicht gesetzt, wird verworfen und neu erzeugt. Ist `edited` gesetzt, gewinnt
immer die JSON — dann hat jemand bewusst Hand angelegt.

**Kein Score vorhanden:** `ConstantTempoMap` aus BPM und Taktart. Exakt das
heutige Verhalten. Kein Warnhinweis, das ist ein legitimer Modus.

Der aktive Provider gehört nach `score_provider` und `diagnostics`, damit im
Graph sichtbar ist, welche Quelle greift — nicht in `musical_position`.

---

## Material-Herkunft

Dieselbe Idee ein zweites Mal — bewusst, weil sie sich beim Score bewährt hat
und weil ein zweites Konzept für dasselbe Problem nur Erklärungsaufwand wäre.

**Ordner-Konvention:** `velvet-lies.wav` → `velvet-lies.stems/` daneben, im
Demucs-Layout: `vocals.wav`, `drums.wav`, `bass.wav`, `other.wav`. Das ist
kein erfundenes Format, sondern das, was ohnehin auf der Platte liegt, wenn
jemand Demucs laufen lässt.

**Einzeldatei-Konvention:** `velvet-lies.vocals.wav` daneben. Für den
häufigen Fall, dass nur der Vocal-Stem gebraucht wird und niemand die anderen
drei herumliegen haben will.

**Override:** optionaler String-Input `stems_dir`, analog zu `score_file`.

**Kein Stem vorhanden:** `StemSet` mit leerem `extra`. Alles Nachgelagerte
prüft auf Anwesenheit, nichts schlägt fehl. Auch das ist ein legitimer Modus
und braucht keinen Hinweis.

Kein zweiter Audio-Input am Node. Zwei Datei-Widgets nebeneinander laden
förmlich dazu ein, versehentlich Mix und Stem zu vertauschen — und dieser
Fehler ist wieder einer von der stummen Sorte.

---

## Frontend-Transport

Das Lineal braucht den Score zur **Editierzeit**. Node-Outputs entstehen erst
zur Ausführungszeit — die helfen nicht.

Route über `PromptServer.instance.routes`:

```
GET /comfyui-musical-audio/score?audio=<name>
```

Der Namespace folgt dem Bestand: `waveform_routes.py` definiert
`WAVEFORM_PEAK_ROUTE = "/comfyui-musical-audio/waveform-peaks"`, gespiegelt
in `js/waveform_loader.js` als `WAVEFORM_PEAK_ENDPOINT`. Also
`SCORE_ROUTE = "/comfyui-musical-audio/score"` und dieselbe Spiegelung, nicht
ein zweites Präfix.

Payload, vollständig — ohne `audio_seconds_at_tick_zero` kann das Frontend
den Score nicht über die Waveform legen:

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

Serverseitig cachen — und zwar **nach dem Muster, das schon dasteht**.
`waveform_routes.py` löst genau dieses Problem bereits: ETag aus Dateigröße
und `st_mtime_ns`, `If-None-Match` wird vor jedem Cache-Zugriff geprüft und
mit `304` beantwortet, der Cache ist größenbegrenzt und über
`(canonical_path, mtime_ns)` verschlüsselt, die teure Arbeit läuft über
`asyncio.to_thread`. Registrierung analog über `register_score_routes()` in
`__init__.py`.

Kein zweites Cachekonzept. Wenn beim Bauen auffällt, dass sich Teile davon
teilen lassen, ist das ein Refactoring wert — zwei nebeneinander laufende
Invalidierungsstrategien sind es nicht.

`bar_starts` als flaches Array reicht dem Lineal völlig — die Event-Listen
braucht das Frontend nur für Anzeigezwecke.

**Reserviert für Phase 8:** Der MIDI-Parser antwortet in Millisekunden, eine
Audioanalyse nicht. Die Route braucht deshalb einen dritten Zustand neben
„Score" und „kein Score": `202` plus `{ status: "analyzing" }`, und das
Frontend pollt. Diesen Fall heute schon im Client behandeln — als
„zeige vorerst das gleichmäßige Raster" — kostet nichts und erspart später
eine Änderung an zwei Enden gleichzeitig.

---

## Phasen

Jede Phase endet lauffähig und testbar.

Nach Phase 4 ist das Timing im Graph für **konstante** Taktarten korrekt,
ohne dass `musical_audio_ui.js` (2391 Zeilen) angefasst wurde. Scores mit
variabler oder mitten im Takt wechselnder Taktart bleiben bis zum Abschluss
von Phase 5 nicht editierbar — siehe das Feature-Gate in Phase 4.

### Phase 0 — Hausputz

Unabhängig vom Score, aber vorher fällig: der Umbau stützt sich auf die
Testsuite, also muss die Basis tragen.

**`IS_CHANGED` ergänzen.** ComfyUI cacht anhand der Inputs. Der Dateiname
ändert sich beim Neuexport aus der DAW nicht, also feuert der Node nicht neu
und arbeitet mit altem Audio weiter. Der Fehler sieht danach aus wie ein
Timing-Problem. mtime oder Hash zurückgeben.

**Silence-Fallback sichtbar machen.** Datei fehlt oder Decode scheitert →
eine Sekunde Stille plus `print`. In der Konsole scrollt das weg; bei einem
Batch über 29 Segmente merkt man es erst am Ende. Fallback behalten, aber den
Fehler zusätzlich in einen STRING-Output schreiben, der im Graph sichtbar ist.
In Phase 0 ist das mangels `diagnostics` noch `musical_position`; Phase 4
zieht ihn dorthin um.

**Drei nackte `except:` ersetzen.** `musical_audio_ui.py` Zeile 63 und 76 in
`INPUT_TYPES`, Zeile 208 in `load_audio` um `get_annotated_filepath`. Fangen
aktuell auch `KeyboardInterrupt` und `SystemExit`. `except Exception:` genügt.

**`VALIDATE_INPUTS` eingrenzen.** Die Signatur lautet
`VALIDATE_INPUTS(cls, audio, **kwargs)` und gibt bedingungslos `True` zurück.
Der Kommentar im Code erklärt korrekt, *warum* — der „Value not in list"-Fehler
soll umgangen werden, damit der Silence-Fallback greifen kann. Nur ist das
`**kwargs` der Fehler: ComfyUI überspringt seine eigene Prüfung für jeden
Input, den die Signatur annimmt, und `**kwargs` nimmt alle an.

Fix ist eine Zeile: `**kwargs` streichen. Dann bleibt der gewollte Effekt für
`audio` erhalten, und alle anderen Widgets werden wieder validiert, bevor
`musical_timing` mitten in der Ausführung `TypeError` wirft.

**`start_beat` begrenzen.** Deklariert als `("INT", {"default": 1, "min": 1})`
— kein Maximum. Beat 7 in einem 4/4-Takt knallt erst tief in
`musical_timing`. Gegen `beats_per_bar` clampen. Gleiches gilt für
`beats_per_bar` selbst, das ebenfalls nur ein Minimum hat.

**Totes `duration`-Widget klären — Vorsicht, es ist nicht tot.** Im Python
wird es über `_ = duration, snap_mode, audioUI` verworfen, und `"duration"`
steht gleichzeitig in `RETURN_NAMES`. Die Namenskollision ist real.

Im Frontend ist es aber **an acht Stellen** verdrahtet, darunter
`setWidgetValue("duration", …)` in der Sekundensynchronisation und ein
eigener Zweig im Widget-Callback (`musical_audio_ui.js` Zeile 2332 ff.).
Entfernen ist damit kein Aufräumen, sondern ein Eingriff in die
Seconds-Mode-Logik — und `AUDIO_INTEGRATION_SPEC.md` beschreibt dieses
Verhalten ausdrücklich als gewollt.

**Revidierte Empfehlung:** Widget stehen lassen. Der Rückbau ist ein eigener
Vorgang mit Spec-Änderung, kein Hausputz.

**Und das Umbenennen des Outputs ist auch keiner.** Ein Outputname ist
öffentlicher Vertrag, auch bei unveränderter Position — gespeicherte Workflows
und `tests/test_node_contract.py` referenzieren ihn. Deshalb nicht im
Sammelcommit „Hausputz", sondern: eigener Commit, Spec-Änderung darin,
`test_node_contract.py` bewusst angepasst, Eintrag im Changelog.

Vertretbare Alternative: den Namen für 0.2 stehen lassen und erst bei einem
Major-Sprung bereinigen. Eine unschöne Kollision ist nicht gefährlicher als
ein gebrochener Workflow.

*Warum zuerst:* Alles hier ist unabhängig vom Score und in einem Zug
erledigt. Danach ist die Testsuite ein belastbares Netz für Phase 3.

### Phase 0.5 — Ablage

Rein mechanisch, kein Verhalten, kein Test berührt. Steht hier und nicht
später, weil Phase 1 die Specs inhaltlich anfasst: **erst verschieben, dann
ändern.** Beides in einem Commit macht den Diff unlesbar, weil `git` einen
umgezogenen *und* editierten Text nicht mehr als Umzug erkennt.

**Schritt 1 — verschieben.** `docs/MUSICAL_TIMING_SPEC.md` und
`docs/AUDIO_INTEGRATION_SPEC.md`, per `git mv`, ohne eine Zeile Inhalt zu
ändern. Weder Python, JS, Tests noch `package.json` verweisen auf die
Dateinamen — geprüft, der Umzug bricht nichts.

**Schritt 2 — README verlinken.** Ein Abschnitt `## Documentation` mit beiden
Pfaden. Aktuell sind die Specs aus der README **überhaupt nicht erreichbar**;
der einzige interne Link im ganzen Dokument zeigt auf `LICENSE`.

**Schritt 3 — Rangfolge festschreiben.** Das ist der eigentliche Aufräumpunkt,
nicht der Ordner. Die README dupliziert normativen Inhalt in gleich vier
Abschnitten: „Tempo interpretation", „Outputs", „Clamping and zero-length
selections", „Compatibility". Drei Dokumente, überlappender Inhalt, nirgends
eine Aussage, welches im Konflikt gewinnt.

Zwei Sätze lösen das: In beiden Specs oben „this document is normative", in
der README „descriptive; the specs in `docs/` take precedence". Danach darf
die Duplizierung bleiben — sie ist dann Einstiegshilfe statt zweite Wahrheit.

**Schritt 4 — belegte Inkonsistenzen mitnehmen.** Drei Stück, alle im Repo
nachweisbar:

- README, „Version 0.1 limitations", Zeile 183: *No waveform visualization*.
  Es gibt `waveform_peaks.py` (521 Z.), `waveform_routes.py` (318 Z.),
  `js/waveform_loader.js`, `js/waveform_peaks.js` und zwei Testdateien dazu.
  Der Punkt ist schlicht überholt.
- README spricht durchgehend von 0.1, `CHANGELOG.md` steht auf 0.1.1, der
  Branch zielt auf 0.2.0.
- `MUSICAL_TIMING_SPEC.md` hat einen `## Status`-Abschnitt mit
  Versionsangabe, `AUDIO_INTEGRATION_SPEC.md` hat keinen. Angleichen.

Die übrigen drei Zeilen der Limitations-Liste — keine BPM-Erkennung, keine
Downbeat-Erkennung, keine Tempo-Maps, keine wechselnden Taktarten — bleiben
stehen. Sie sind korrekt und werden von den Phasen 2 bis 4 und 8 der Reihe
nach abgeräumt. Praktischerweise ist die Liste damit schon die Roadmap.

**Kopplung an Phase 0:** `AUDIO_INTEGRATION_SPEC.md` beschreibt das
`duration`-Widget als „remains present for positional workflow
compatibility". Wenn Phase 0 es entfernt oder umbenennt, wird der Satz falsch.
Diese eine Änderung gehört in den Commit von Phase 0 — an der *alten* Stelle,
das stört den späteren `git mv` nicht.

**Was ausdrücklich nicht hierher gehört:** jede inhaltliche Erweiterung der
Specs um Score, Ticks oder Extrapolation. Das ist Phase 1. Diese Phase
verschiebt, verlinkt und räumt Widersprüche weg — mehr nicht, sonst ist der
Vorteil des lesbaren Diffs wieder verspielt.

### Phase 1 — Spec

`docs/MUSICAL_TIMING_SPEC.md` um Score, Tick-Kanonik und die
Extrapolationsregel erweitern. Version auf 0.2 heben, 0.1-Verhalten als
`ConstantTempoMap` festschreiben.

Die Provider-Kette sowie `ScoreFormat` und `ProviderKind` gehören in
denselben Text, auch wenn nur
drei der fünf Provider gebaut werden. Ein Vertrag, der die leeren Plätze
benennt, ist mehr wert als einer, der später aufgebohrt wird.

*Warum vor der Implementierung:* Der Spec-Text ist der Vertrag, gegen den
gebaut wird — und er ist das, was du Claude Code mitgibst, nicht nur den Code.

### Phase 2 — Parser und Score

`midi_parse.py`, `model.py` und `serialize.py`, vollständig getestet, ohne
Anbindung. Testkorpus: die vier Sondertakte, ein Track ohne Events, ein Track
mit Tempowechsel mitten im Takt, eine abgeschnittene Datei zur Prüfung der
Extrapolation.

**Prüfstein:** Takt 55 muss −1 Frame gegen das naive Raster liefern, Takt 169
genau −3,5.

**Zweiter Prüfstein:** `Score → JSON → Score` ist verlustfrei. Kostet einen
Test, macht Phase 8 und die Handarbeit am Sidecar später zu einem Nicht-Thema.

### Phase 3 — TempoMap einziehen

Protokoll definieren, `ConstantTempoMap` bauen, `musical_timing` und
`audio_clip_plan` um den optionalen Parameter erweitern.

**Abnahmekriterium:** alle Alt-Tests grün, ohne eine Zeile Testcode zu ändern.

### Phase 4 — Node-Integration

Provider-Kette, `ResolvedScore`, neue Outputs — **ans Ende angehängt**, damit
die bestehende Reihenfolge und `tests/test_node_contract.py` unberührt
bleiben: `end_frame_exclusive`, `section_name`, `sample_rate`,
`score_format`, `score_provider`, `diagnostics`.

**`diagnostics` statt Überladung von `musical_position`.** Der frühere Plan
wollte Score-Quelle, Silence-Fallback und verworfene Stems alle in
`musical_position` schreiben. Das macht aus einem fachlichen Output einen
Systemlogkanal. Getrennt bleibt `musical_position` die musikalische Position,
und `diagnostics` sammelt Herkunft und Warnungen — später als JSON-String
strukturierbar, ohne den Node-Vertrag erneut anzufassen.

**Feature-Gate für variable Meter.** Hier lag der gefährlichste Fehler im
vorherigen Plan: „ab Phase 4 stimmt das Timing, nur das Lineal ist noch
kosmetisch falsch" ist nicht haltbar. Der lineare Subdivision-Index steuert
auch Selection, Snapping und Dauerumrechnung. Bei aktiver Meter-Map schreibt
das alte Frontend also **falsche Selektionswerte zurück** — still, und in die
Widgets, die den Node-Output bestimmen.

Regel bis Phase 5 abgeschlossen ist — **operativ, ohne Auslegungsspielraum**:

> Bei `has_variable_meter` oder `has_midbar_meter_change` beeinflusst der
> Score weder Selection noch Snapping noch die daraus erzeugten Outputs. Die
> Score-Daten dürfen angezeigt werden; scorebasierte Bearbeitung ist
> deaktiviert. Das bestehende konstante Raster bleibt der alleinige
> Bearbeitungspfad, und `diagnostics` weist aus, dass nur ein Teil der
> Score-Information wirkt.

Es gibt damit keinen halb aktiven Score-Modus: Entweder der Score steuert die
Bearbeitung vollständig, oder gar nicht. `ConstantTempoMap` läuft davon
unberührt und unbeschränkt.

Alternativ Phase 4 und 5 gemeinsam veröffentlichen. Was nicht geht: variable
Meter editierbar ausliefern und auf das Lineal als einzigen Mangel verweisen.

Ab hier stimmt das Timing im Graph für den konstanten Fall.

### Phase 5 — Frontend

Route, `js/score.js`, Umstellung von `musical_grid.js` auf Tick-Basis,
Lineal und Snapping.

**Vormals offene Designfrage, jetzt entschieden:** zeitproportional. Die
x-Achse ist in beiden Views die Sekundenachse einer Waveform. Gleich breite
Takte würden bei Tempowechsel oder wechselnder Taktart nicht mehr über dem
Audio liegen, das sie beschreiben — das wäre nur in einer separaten,
abstrakten Partituransicht sinnvoll, und die gibt es hier nicht.

```text
x-Achse    = Sekunden
Taktbreite = tatsächliche Dauer des Taktes
```

### Phase 6 — Sections und Batch

Neuer Node `MusicalSegmentBatch`: nimmt den Score, gibt eine Liste von
Segmenten. Zwei Modi:

- **Sections** — schneidet an den kanonischen `Section`-Grenzen (S4), nicht
  an den Rohmarkern
- **Blocks** — schneidet in feste N-Takt-Blöcke

Pro Segment: `audio`, `start_frame`, `frame_count`, `name`, `start_bar`,
`bar_count`, `seed`. Grenzen halboffen (S5).

**Seed aus dem Sectionnamen.** Der kostet fast nichts und ist der billigste
Weg zu einem Video, das zusammenhält: Namen normalisieren, hashen, mit einem
Basis-Seed mischen. Wiederkehrende Abschnitte sehen dadurch automatisch
verwandt aus, ohne dass jemand 29-mal von Hand Seeds einträgt.

**Der Hash muss stabil sein.** Nicht Pythons `hash()` — der ist für Strings
seit 3.3 pro Prozess gesalzen, das Video sähe nach jedem ComfyUI-Neustart
anders aus. BLAKE2 oder SHA-256, auf die Seed-Breite gekürzt. Normalisierung
davor: NFKC, dann `casefold()`, dann Ziffernsuffix abtrennen.

Drei Modi, weil die Zusammenfassung nicht immer gewünscht ist:

| Modus | Verhalten |
|---|---|
| `Exact` | vollständiger Name, `Chorus 1` ≠ `Chorus 2` |
| `Family` | Stamm, `Chorus 1` = `Chorus 2` — Default |
| `Unique` | jede Section eigener Seed, auch bei gleichem Namen |

Gehört nach `score/naming.py`, weil derselbe Normalisierer auch die
Analyzer-Labels aus Phase 8 bedient.

**Blockgröße aus Zieldauer.** Dritter Parameter neben festem N: eine
Zielsekundenzahl, aus der der Node die Taktzahl errechnet, die bei diesem
Tempo am nächsten dran liegt. Schnitte fallen dann auf Taktgrenzen statt
neben sie — siehe Phase 10.

### Phase 7 — Frame-Alignment (optional)

`frame_alignment` als Modus: `Off` / `8n+1` / `Custom`. Rundet `frame_count`
auf einen für das Zielmodell gültigen Wert.

Der Node ist die einzige Stelle im Stack, die das musikalisch sinnvoll
entscheiden kann, weil er weiß, wo die nächste Taktgrenze liegt.

**Konfliktstrategie, verbindlich.** Ein Segment kann nicht gleichzeitig exakt
zwischen zwei Taktgrenzen liegen *und* exakt `8n+1` Frames lang sein. Der
musikalische Plan wird deshalb **nie heimlich verschoben**. Stattdessen bleibt
beides sichtbar:

```text
musical_frame_count    aus den Taktgrenzen, wahr
aligned_frame_count    für das Zielmodell, gültig
padding_before
padding_after
```

Der Score bleibt damit die Wahrheit, und der Zielmodell-Adapter entscheidet,
wie er die Differenz erzeugt — Hold-Frames, Überlappung oder Beschnitt. Das
ist eine Entscheidung des Video-Workflows, nicht des Timings.

### Phase 8 — Audioanalyse (Stub)

Für Material ohne MIDI. Nicht für diesen Track — hier ist es der Weg, das
Ding auch auf fremdes Audio anzuwenden.

**Schnittstelle** (das ist der Teil, der jetzt schon feststeht):

```python
# score/analyze.py — rein
def analyze_structure(
    samples: np.ndarray,          # mono float32
    sample_rate: int,
    beats_per_bar: int,           # Vorgabe aus dem Node, nicht erkannt
    tempo_mode: Literal["constant", "follow"] = "constant",
    target_sections: int | None = None,
) -> Score:                       # source="analyzed", meter_estimated=True
    ...
```

**Verfahren**, in der Reihenfolge, in der es gebaut würde:

1. Beat-Grid. Downbeat-Phase separat schätzen — ein guter Beat-Tracker sagt
   *wo* die Beats sind, nicht welcher davon die Eins ist.
2. Beat-synchrone Features: Chroma plus MFCC, pro Beat medianaggregiert. Ein
   Fünf-Minuten-Track schrumpft damit auf ~600 Vektoren; ab hier ist alles
   rechnerisch billig.
3. Self-Similarity-Matrix, Kosinus, Diagonalen geglättet.
4. Grenzen über Foote-Novelty mit Checkerboard-Kernel, dann Peak-Picking.
5. Labels über Spektralclustering auf der SSM. Wiederholungserkennung, keine
   Funktionsbenennung.
6. Grenzen auf Downbeats snappen, Ticks synthetisieren, `Score` bauen.

Deps: numpy und scipy reichen. Die Eigenzerlegung über `scipy.linalg.eigh`,
k-Means über `scipy.cluster.vq` — scikit-learn ist dafür nicht nötig.

**Namen:** Das Verfahren liefert `A B A B C B`, nicht `verse chorus`. Für
`section_name` heißt das `A1`, `B1`, `A2`, `B2`, `C1`, `B3` — Clusterbuchstabe
plus Wiederholungszähler. Damit ist im Graph sowohl sichtbar, *dass* zwei
Abschnitte gleich sind, als auch *der wievielte* es ist. Funktionale Labels
(intro/verse/chorus) sind auf westliche Popstruktur trainiert und werden bei
elektronischem Material beliebig; der Verzicht ist kein Kompromiss.

**Evaluation:** Der Analyzer wird gegen `velvet-lies.mid` gemessen. Du hast
für diesen Track Ground Truth aus der DAW — Markerpositionen und Taktgrenzen,
von Hand gesetzt.

Die naheliegende Metrik „mittlere Abweichung zur nächsten echten Grenze" ist
allerdings wertlos: Ein Analyzer, der sehr viele Grenzen ausgibt, liegt damit
automatisch gut. Stattdessen das übliche MIR-Verfahren:

- eindeutiges One-to-one-Matching erkannter und echter Grenzen
- Toleranzfenster von einem halben bis einem Takt
- **Precision** (wie viele erkannte Grenzen sind echt) und **Recall** (wie
  viele echten Grenzen wurden gefunden), daraus F1
- mittlere Abweichung nur über die gematchten Grenzen
- Zahl der Über- und Untersegmentierungen getrennt ausweisen

Erst damit werden „zu viele Grenzen" und „wichtige Grenze fehlt" als zwei
verschiedene Fehler sichtbar — und genau die unterscheiden ein brauchbares
Ergebnis von einem, das nur gut aussieht.

Das ist der Grund, warum diese Phase hinten steht und trotzdem hier
dokumentiert ist: Der einzige Track, an dem sich die Automatik ehrlich prüfen
lässt, ist genau der, für den man sie nicht braucht.

**Was ausdrücklich nicht gebaut wird:** `allin1` als Backend. Liefert zwar
Downbeats und funktionale Labels direkt, hängt aber an NATTEN — einer
kompilierten Extension, die zur exakten Torch-Version passen muss. In einem
ComfyUI-Node hieße das, die Torch-Installation fremder Leute zur Geisel zu
nehmen. Dazu rund 1,5 GB Modelle beim ersten Lauf. Als optionaler Provider in
separatem venv per Subprozess denkbar, als Dependency nicht.

**Lizenzhinweis, korrigiert:** Das Repo steht bereits unter **GPL-3.0**, nicht
unter einer permissiven Lizenz. Damit ist die Lage entspannter als zunächst
angenommen — GPLv3 erlaubt in §13 ausdrücklich die Kombination mit
AGPLv3-Code, das Ergebnis bleibt verteilbar.

Der Rest der Klausel gilt trotzdem: Der AGPL-Teil behält seine
Netzwerkbedingung, und ComfyUI *ist* ein HTTP-Server. Das ist genau die
Konstellation, auf die AGPL §13 zielt. Praktische Konsequenz für jemanden,
der ComfyUI lokal fährt: keine. Für jemanden, der es gehostet anbietet:
möglicherweise doch.

Empfehlung bleibt deshalb unverändert, nur mit anderer Begründung: Essentia
als **optionales Extra mit lazy Import**, damit die Standardinstallation
schlicht GPL-3.0 bleibt und niemand über eine Bedingung stolpert, die er sich
nicht ausgesucht hat. Die Analysekette oben kommt ohnehin mit numpy und scipy
aus. (Kein Rechtsrat — wenn das Pack veröffentlicht wird, gehört das einmal
richtig geprüft.)

### Phase 9 — Stems

Setzt Phase 6 voraus, sonst nichts. Unabhängig von Phase 8 — Stems und
Analyzer haben außer dem Wort „Audio" nichts miteinander zu tun.

**Basis:** Discovery nach den beiden Konventionen, Ausrichtungsprüfung,
`apply_plan` je Spur. Pro Segment kommt neben `audio` ein `audio_vocals`
heraus, und was sonst gefunden wurde.

**Vocal-Ausgabe für Lipsync** gleich in 16 kHz mono. Das wollen die
Lipsync-Nodes ohnehin, und ein Resample-Node weniger im Graph ist ein
Handgriff weniger pro Shot.

**Der eigentliche Gewinn ist nicht der Ton, sondern die Klassifikation.**
Frame-RMS des Vocal-Stems pro Section messen, und der Node weiß, wo gesungen
wird und wo nicht. Das ist die Entscheidung zwischen Performance-Shot und
B-Roll — bei 29 Shots eine Handarbeit, die ersatzlos entfällt. Kostet keine
ML, weil die schwere Arbeit beim Separieren schon passiert ist.

Zwei Outputs dafür:

- `has_vocals: bool` — **hohes Perzentil** der Frame-RMS gegen einen
  Schwellwert, nicht der Mittelwert. Stems haben Bleed und Hallfahnen; ein
  Gate über den Mittelwert feuert im halben Instrumentalteil.
- `vocal_onset_seconds` — erster Einsatz *innerhalb* des Segments. Sekunden,
  nicht Frames: die Umrechnung auf Video-Frames braucht die fps und gehört
  an den Rand (S6). Beginnt der
  Gesang 1,2 s nach Segmentanfang, ist das eine Kamerafahrt hinein und kein
  Schnitt darauf. Diese Zahl ist im Graph unmittelbar verwertbar.

**Drums und Bass sind Kurven, keine Schnitte** — Bass-Energie auf Zoom oder
Shake, Drum-Onsets auf IPAdapter-Gewichte. Anderer Output-Typ, anderer
Lebenszyklus, deshalb ein eigener Node `MusicalEnvelope` statt weiterer
Outputs am Batch. Nicht Teil dieser Phase, nur der Grund, warum `StemSet`
generisch über `extra` geht und nicht `vocals: Stem | None` heißt.

### Phase 10 — Manifest und Shotlist-Brücke

Der Batch weiß alles, was eine Shotlist braucht: Nummer, Name, Takt,
Taktzahl, Dauer, Vocal-Flag. Ein Manifest-Output (JSON plus Markdown) macht
daraus die Übergabe an den Prompt-Schritt.

Damit schließt sich die Kette: Ein Shotlist-Generator arbeitet in festen
Sekundenhäppchen, dein `Blocks`-Modus in Takten. Bei bekanntem Tempo ist das
dieselbe Größe in anderer Einheit — die Umrechnung aus Phase 6 lässt die
Shotlist auf die Musik fallen statt daneben.

Optional daran anschließend: Manifest plus Mood-Tags an ein lokales Modell
geben und pro Segment einen Bildprompt schreiben lassen. Das ist die Stelle,
an der ein LLM in dieser Kette tatsächlich etwas beiträgt — beim Übersetzen
der Struktur in Prompts, nicht bei der Analyse.

---

## Paralleler Strang: Waveform-Editor (v0.2.0)

Stand `604e5ab`, neunter v0.2-Commit, fünf Schritte offen. Der Strang ist vom
Score weitgehend unabhängig — bis auf zwei Berührungspunkte, die weiter unten
stehen. Er gehört hierher, weil beide Stränge dieselbe Datei anfassen.

### Was steht (am Commit verifiziert)

- `js/waveform_renderer.js` (351 Z.) mit `createWaveformRenderPlan`,
  `renderWaveformCanvas`, `clearWaveformCanvas`
- Der Renderer nimmt bereits `startSeconds`/`endSeconds` und wählt daraus die
  Pyramidenebene. **Die Annahme aus Schritt 5 stimmt** — Zoom braucht keinen
  neuen Renderer, nur veränderte Zeitgrenzen.
- `MAX_WAVEFORM_RENDER_WIDTH = 1_000_000` und
  `MAX_WAVEFORM_DEVICE_PIXEL_RATIO = 4` sind bereits Deckel im Code. Die
  Warnung vor der trackbreiten Canvas ist damit nicht nur Vorsatz.
- `node._musicalAudioWaveformState`, `node._musicalAudioWaveformRenderer` mit
  `destroyed`, `animationFrameId`, `resizeObserver`
- `node.scheduleMusicalAudioWaveformRender()` als koaleszierender Scheduler
- `onRemoved`-Hook mit `cancelAnimationFrame` und `resizeObserver.disconnect()`
- Der Rücksprung an den Selektionsstart am Selektionsende existiert bereits
- Kein Playhead, kein Modal, kein Zoom, kein `showExtensionDialog` — bestätigt

### Drei Korrekturen am Schrittpapier

**`audioEl` liegt im Closure, nicht am Node.** Zeile 560,
`const audioEl = document.createElement("audio")`. Alles andere Geteilte hängt
am Node — das Audioelement nicht. Schritt 4 verlangt „reuse the same
audioEl"; das ist heute schlicht nicht erreichbar.

Vorschlag: **nicht das Element exponieren**, sondern eine Transportfassade
`node._musicalAudioTransport` mit `getCurrentTime()`, `seek(seconds)`,
`isPlaying()`, `subscribe(fn)`. Wer das rohe Element bekommt, hängt Listener
direkt daran, und dann ist das Aufräumen beim Schließen des Modals nicht mehr
an einer Stelle kontrollierbar. Die Fassade sind fünfzehn Zeilen und machen
Schritt 6 („zwei Views, eine Uhr") überhaupt erst durchsetzbar.

Diese Vorarbeit gehört nach **Schritt 2**, nicht nach Schritt 4 — der
Playhead ist ihr erster Konsument, und dort ist sie noch billig.

**Der Playhead braucht eine zweite rAF-Schleife — und das ist korrekt.**
`scheduleMusicalAudioWaveformRender` ist ein *einmaliger* Scheduler: maximal
ein Frame in Flight, danach `animationFrameId = null`. Der Playhead braucht
eine *laufende* Schleife während der Wiedergabe. Andere Form, eigener Handle.

Die Falle: Der neue Handle muss ins selbe `runtime`-Objekt. In `onRemoved`
wird heute genau ein `runtime.animationFrameId` gecancelt. Eine Schleife, die
dort nicht registriert ist, läuft nach dem Löschen des Nodes weiter — mit
einer Closure auf totes DOM. Das ist der wahrscheinlichste Fehler in
Schritt 2 und der am schwersten zu findende.

**`app.extensionManager` ist bereits im Einsatz** (`setting.get`, Zeile 102).
Der Spike aus Schritt 3 muss deshalb nur noch `dialog.showExtensionDialog`
und das Vue-Mounting klären, nicht den Zugriff auf den Manager selbst. Das
verkleinert den Spike spürbar.

### Zwei Berührungspunkte mit dem Score

**Das Lineal wird sonst zweimal gebaut.** Schritt 4 sieht eine „large ruler
area" im Modal vor. Das Node-Lineal hängt heute an `subdivisionIndexToSeconds`
(Zeile 1611) — also an genau dem Index, den Entscheidung 2 entfernt. Wer das
Modal-Lineal auf demselben Index baut, schreibt es zweimal.

Die Auflösung ist aber **nicht** „Phase 3 abwarten", wie hier zuvor stand.
Dieselbe Fassadenlogik wie beim Transport löst es besser:

```js
const timeAxis = {
    secondsToPosition(seconds),
    positionToSeconds(position),
    ticksForVisibleRange(start, end),
    barsForVisibleRange(start, end),
};
```

Heute `ConstantTimeAxis`, später `ScoreTimeAxis`. Damit lassen sich
Modal-Shell, Playhead, Zoom und Scrollen sofort bauen, und nur das Backend
des Lineals wird ausgetauscht. Der Reihenfolgezwang schrumpft auf eine Regel:

> Das Modal-Lineal darf nicht direkt auf dem linearen Subdivision-Index
> aufsetzen.

**Sections gehören ins Lineal, nicht in eine eigene Spur.** Sobald Phase 4
steht, hat das Modal etwas anzuzeigen, das es vorher nicht gab: benannte
Bereiche. Die in Schritt 4 reservierte Toolbar-Fläche ist der falsche Ort —
Sections sind zeitgebunden und gehören als Band unter das Lineal, in dieselbe
Zeitachse.

Daraus wird auf Dauer ein **Score-Inspector**: schaltbare Layer für Tempo,
Taktart, Sections, Marker und Vocal-Aktivität aus Phase 9. Nicht alles
gleichzeitig sichtbar, sondern als Layer-Auswahl — dann wächst das Modal zum
musikalischen Inspektor, ohne den Node selbst aufzublähen.

Drei kleine Funktionen, die davon fast geschenkt abfallen, sobald Sections im
Modal liegen: Sprung zur vorigen/nächsten Section, Zoom auf die aktuelle
Section, Selektion auf Sectiongrenzen setzen. Dazu `Follow playhead` mit
`Off` / `Page` / `Center`, wobei `Page` beim Arbeiten meist angenehmer ist als
dauerndes Mitscrollen.

Langfristig, aber hier schon erwähnenswert, weil es die Begründung für S4
liefert: Marker und Sections direkt im Modal verschieben, umbenennen, teilen
und als `.score.json` speichern. Genau dafür müssen Sections kanonisch sein
und nicht aus Markern abgeleitet.

### Verzahnte Reihenfolge

| Reihe | Warum hier |
|---|---|
| Schritt 2 + Transportfassade | unabhängig, und die Fassade ist später teurer |
| Phase 0, Phase 0.5 | unabhängig, kein Risiko |
| Schritt 3 (Spike) | klein, klärt das größte Unbekannte früh |
| Phase 1–3 | Score-Kern, unabhängig vom Modal |
| Schritt 4–6 | Modal, Lineal gleich tickbasiert |
| Phase 4 | Score im Graph, Sections für das Modal |
| Phase 5 | Tick-Umstellung in beiden Views auf einmal |
| Phase 6 ff. | Batch, Stems, Manifest |

Harte Zwänge gibt es darin nur zwei: variable Meter-Maps bleiben bis zum
Abschluss von Phase 5 read-only (Feature-Gate in Phase 4), und das
Modal-Lineal darf nicht auf dem linearen Subdivision-Index aufsetzen. Alles
andere lässt sich tauschen.

---

## Was den Aufwand treibt

Nicht der Parser. Der ist überschaubar.

Der Kostenpunkt ist **Entscheidung 2** — den linearen Index aus dem Frontend
zu operieren. Das berührt Snapping, Selection, Lineal und die
Sekunden-Rückrechnung. Wenn beim Planen etwas ausführlich durchdacht gehört,
dann das.

Phase 8 ist davon unabhängig teuer, aber in anderer Währung: der Code ist
kurz, das Tuning ist lang. Novelty-Threshold, Kernelgröße, Clusteranzahl —
das kalibriert man gegen Ohr und Ground Truth, nicht gegen einen Unit-Test.
Deshalb Stub und nicht Ticket.

Phase 9 ist umgekehrt billiger, als sie aussieht. Das Schneiden ist eine
Schleife über eine Liste, die es schon gibt. Der Aufwand steckt vollständig
in Discovery und Ausrichtungsprüfung — also in den unreinen Rändern, nicht
im Kern.

---

## Reihenfolge, falls die Zeit knapp wird

Phase 0 allein lohnt sich schon: `IS_CHANGED` behebt das Cache-Problem, und
der sichtbare Fehlerpfad erspart dir die Suche nach stillen Segmenten. Ein
Abend, kein Risiko.

Phase 0.5 kostet eine Stunde und ist die einzige Phase ohne jedes Risiko —
kein Code, keine Tests, nur `git mv` und Text. Wenn sie nicht direkt nach
Phase 0 kommt, kommt sie nie, weil sie ab Phase 1 mit inhaltlichen Änderungen
verschmilzt und dann keine eigene Phase mehr ist.

Phase 4 liefert danach den größten Nutzen pro Aufwand: ab da ist das Timing
korrekt und du kannst mit dem Video-Workflow anfangen.

Phase 6 ist der eigentliche Hebel für dein Projekt — 29 Shots aus einem
Knoten statt 29 Handgriffe.

Phase 5 ist für konstante Taktarten aufschiebbar und hat den höchsten
Aufwand. Für variable Taktarten ist sie dagegen zwingend: Bis sie steht,
bleiben solche Scores read-only. „Kosmetik" wäre sie nur ohne das
Feature-Gate — mit ihm ist sie die Freischaltung eines halben Features.

Phase 9 gehört direkt hinter 6, wenn Lipsync im Plan steht. Sie ist die
einzige der späten Phasen mit gutem Aufwand-Nutzen-Verhältnis, weil sie auf
einer fertigen Schnittliste aufsetzt und nur an den Rändern arbeitet.

Phase 8 ist danach, oder nie. Solange du aus Cubase exportierst, ist sie
reine Kür — und wenn sie kommt, ist durch Entscheidung 5 der Platz dafür
schon freigehalten.

Phase 10 ist eine Serialisierung von Daten, die alle schon vorliegen. Ein
Nachmittag, sobald 6 und 9 stehen.

---

## Was diese Revision ergänzt

Zwei Erweiterungen, beide so geschnitten, dass sie in den frühen Phasen
nichts kosten.

**Score-Quellen** (im Dienst von Phase 8):

- Entscheidung 5: Provider-Kette statt Fallunterscheidung
- `Score.source`, `Score.meter_estimated`, `Section.confidence`
- Fallstrick „Sekunden sind keine Ticks" samt `constant`/`follow`-Regel
- `score/serialize.py` und das JSON-Sidecar mit `derived_from`/`edited`
- `discovery.py` → `providers.py`
- `202 analyzing` als reservierter Zustand der Route
- Phase 8 als Stub mit fixierter Schnittstelle und Evaluationsplan

**Mehrspuriges Material** (im Dienst von Phase 9):

- Entscheidung 6: ein Plan, N Spuren — `apply_plan(plan, track)`
- `Stem`/`StemSet` außerhalb von `Score`, damit der Score serialisierbar
  und der Analyzer rein testbar bleibt
- Fallstrick „Stems liegen nicht zwangsläufig sample-genau"
- Material-Herkunft nach Sidecar-Muster, ausdrücklich **ohne** zweiten
  Audio-Input am Node
- `score/naming.py` mit Seed-Ableitung, schon in Phase 6 nutzbar

**Abgleich mit dem Repo** (im Dienst der Genauigkeit), Stand Branch
`feature/css-theme-foundation-v0.2.0`:

- Entscheidung 2 betrifft **vierzehn** Funktionen, nicht sechs. Die
  mathematische Wurzel liegt in `js/musical_grid.js`; die Integration
  betrifft zusätzlich mehrere Stellen in `js/musical_audio_ui.js`. Python ist
  nicht betroffen.
- Route und Cache folgen `waveform_routes.py` statt eigenem Konzept
- Lizenz ist GPL-3.0, damit ist der Essentia-Punkt entschärft, aber nicht
  gegenstandslos
- `VALIDATE_INPUTS`: das `**kwargs` ist der Fehler, nicht das `return True`
- Zeilennummern der drei `except:` (63, 76, 208), Frontend 2308 statt 2274
- Repo ist flach; `score/` und `material/` als Pakete sind eine benannte
  Entscheidung, keine Fortsetzung des Bestands

**Verifiziert und unverändert übernommen:** kein `IS_CHANGED` vorhanden;
Silence-Fallback an zwei Stellen, beide nur mit `print`; `duration` sowohl
Widget als auch `RETURN_NAMES`-Eintrag; `start_beat` und `beats_per_bar` ohne
Maximum.

**Waveform-Editor** (Strang B, neu, Stand `604e5ab`):

- fünf offene Schritte als paralleler Strang, verzahnt statt eingereiht
- Transportfassade `node._musicalAudioTransport` statt rohem `audioEl`,
  vorgezogen nach Schritt 2
- Registrierung der Playhead-rAF-Schleife im bestehenden `runtime`-Objekt
- `TimeAxis`-Fassade statt Wartezwang auf Phase 3
- Score-Inspector, Section-Navigation und `Follow playhead` als Ausblick

**Verbindliche Semantik** (S1–S10, neues Kapitel vor dem Dateilayout):

- S1 `ResolvedScore` mit `audio_seconds_at_tick_zero`; `downbeat_offset` füllt
  die Rolle bereits
- S2 Integer-Ticks plus geteilte Rundungsregel statt `Fraction`
- S3 Meterwechsel im Takt, plus Parserdefaults für Tempo, Taktart,
  Doppel-Events, Multi-Track-Merge und SMPTE
- S4 Sections kanonisch neben Markern; Taktangaben nur abgeleitet
- S5 halboffene Intervalle, `end_frame_exclusive`
- S6 `sample_index` statt „Frame" für Audiopositionen
- S7 `ProviderResult` mit `not_applicable` / `found` / `invalid`
- S8 `bar_starts` abgeleitet, bis zur Audiodauer, `tick_to_position` O(log n)
- S9 Cache-Fingerprint über Konfiguration, nicht nur mtime
- S10 sprachübergreifendes Golden-Korpus

**Präzisierungen nach dem zweiten Review:**

- S1 Vorzeichengleichung, am Repo verifiziert: `audio_seconds_at_tick_zero
  == downbeat_offset`
- S2 absolute statt kumulative Rundung; Ablehnung zu feiner Raster
- S10 Roundtrip nur von der Positionsseite, plus Monotonie
- `ScoreFormat` und `ProviderKind` getrennt
- `has_variable_meter` als berechnetes Feld, Feature-Gate hängt daran
- Route auf `/comfyui-musical-audio/score` mit vollständigem Payload
- Lineal-Designfrage geschlossen: zeitproportional
- Analyzer-Evaluation mit Precision/Recall/F1 statt mittlerer Distanz
- Output-Umbenennung als Vertragsänderung mit eigenem Commit
- redaktionelle Reste bereinigt: `musical_position` → `diagnostics`,
  Frames → Sampleindizes, Marker → Sectiongrenzen

**Dritte Selbstkorrektur:** Phase 4 war als „ab hier stimmt das Timing"
beschrieben. Bei aktiver Meter-Map schreibt das alte Frontend falsche
Selektionswerte zurück — das ist nicht kosmetisch. Feature-Gate ergänzt.
- Sections als Band unter dem Lineal, nicht in der Toolbar

**Zwei Korrekturen an der vorherigen Revision** — beide waren zu optimistisch:

- Entscheidung 2 betrifft vierzehn Funktionen, nicht sechs. Die mathematische
  Wurzel liegt in `js/musical_grid.js`; die Integration betrifft zusätzlich
  mehrere Stellen in `js/musical_audio_ui.js`. Python ist nicht betroffen.
- Das `duration`-Widget ist nicht tot. Acht Fundstellen im Frontend, davon
  eine in der Seconds-Synchronisation. In Phase 0 nur den Output umbenennen.

- `docs/` mit beiden Specs, per `git mv`, ohne Inhaltsänderung
- README-Links auf beide — die gibt es bisher nicht
- Rangfolge normativ/beschreibend zwischen Spec und README festgeschrieben
- drei belegte Widersprüche: veraltete Waveform-Limitation, uneinheitliche
  Versionsangaben, fehlender Status-Header in `AUDIO_INTEGRATION_SPEC.md`
