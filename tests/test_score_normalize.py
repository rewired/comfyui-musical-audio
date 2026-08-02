import dataclasses
import unicodedata
import unittest

from score.bars import BarGrid, build_bar_grid
from score.model import Marker, MeterEvent, Section, TempoEvent
from score.normalize import (
    NormalizedEvents,
    OrderedMeterEvent,
    OrderedTempoEvent,
    derive_sections,
    finalize_score,
    normalize_events,
)


def simple_grid() -> BarGrid:
    return build_bar_grid(
        ticks_per_quarter=480,
        meters=(MeterEvent(0, 4, 4),),
        through_tick=4000,
    )


class ScoreNormalizationTests(unittest.TestCase):
    def test_default_tempo_and_meter_insertion(self):
        events = normalize_events(
            ticks_per_quarter=480,
            tempos=(),
            meters=(),
            markers=(),
        )
        self.assertEqual(events.tempos, (TempoEvent(0, 500_000),))
        self.assertEqual(events.meters, (MeterEvent(0, 4, 4),))

    def test_defaults_precede_later_first_events(self):
        events = normalize_events(
            ticks_per_quarter=480,
            tempos=(TempoEvent(100, 400_000),),
            meters=(MeterEvent(200, 3, 4),),
            markers=(),
        )
        self.assertEqual(events.tempos, (TempoEvent(0, 500_000), TempoEvent(100, 400_000)))
        self.assertEqual(events.meters, (MeterEvent(0, 4, 4), MeterEvent(200, 3, 4)))

    def test_same_tick_last_wins_within_track_by_event_index(self):
        events = normalize_events(
            ticks_per_quarter=480,
            tempos=(
                OrderedTempoEvent(0, 0, 2, 300_000),
                OrderedTempoEvent(0, 0, 0, 500_000),
                OrderedTempoEvent(0, 0, 1, 400_000),
            ),
            meters=(),
            markers=(),
        )
        self.assertEqual(events.tempos, (TempoEvent(0, 300_000),))

    def test_same_tick_last_wins_across_tracks(self):
        events = normalize_events(
            ticks_per_quarter=480,
            tempos=(),
            meters=(
                OrderedMeterEvent(0, 1, 0, 7, 8),
                OrderedMeterEvent(0, 0, 99, 3, 4),
            ),
            markers=(),
        )
        self.assertEqual(events.meters, (MeterEvent(0, 7, 8),))

    def test_consecutive_duplicate_tempo_and_meter_events_are_removed(self):
        events = normalize_events(
            ticks_per_quarter=480,
            tempos=(
                TempoEvent(0, 500_000),
                TempoEvent(10, 500_000),
                TempoEvent(20, 400_000),
                TempoEvent(30, 400_000),
            ),
            meters=(
                MeterEvent(0, 4, 4),
                MeterEvent(10, 4, 4),
                MeterEvent(20, 3, 4),
                MeterEvent(30, 3, 4),
            ),
            markers=(),
        )
        self.assertEqual(events.tempos, (TempoEvent(0, 500_000), TempoEvent(20, 400_000)))
        self.assertEqual(events.meters, (MeterEvent(0, 4, 4), MeterEvent(20, 3, 4)))

    def test_nonconsecutive_value_changes_remain(self):
        events = normalize_events(
            ticks_per_quarter=480,
            tempos=(TempoEvent(0, 500_000), TempoEvent(10, 400_000), TempoEvent(20, 500_000)),
            meters=(MeterEvent(0, 4, 4), MeterEvent(10, 3, 4), MeterEvent(20, 4, 4)),
            markers=(),
        )
        self.assertEqual(len(events.tempos), 3)
        self.assertEqual(len(events.meters), 3)

    def test_redundant_same_meter_inside_bar_cannot_create_change(self):
        events = normalize_events(
            ticks_per_quarter=480,
            tempos=(),
            meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 4, 4)),
            markers=(),
        )
        grid = build_bar_grid(
            ticks_per_quarter=480,
            meters=events.meters,
            through_tick=1000,
        )
        self.assertEqual(events.meters, (MeterEvent(0, 4, 4),))
        self.assertFalse(grid.has_variable_meter)
        self.assertFalse(grid.has_midbar_meter_change)

    def test_markers_use_python_tick_name_order_without_deduplication(self):
        astral = "\U00010000"
        bmp = "\ue000"
        markers = (
            Marker(10, "B"),
            Marker(0, astral),
            Marker(10, "A"),
            Marker(0, bmp),
            Marker(10, "A"),
        )
        events = normalize_events(
            ticks_per_quarter=480,
            tempos=(),
            meters=(),
            markers=markers,
        )
        self.assertEqual(
            events.markers,
            (Marker(0, bmp), Marker(0, astral), Marker(10, "A"), Marker(10, "A"), Marker(10, "B")),
        )

    def test_marker_names_are_preserved_exactly(self):
        composed = "é"
        decomposed = unicodedata.normalize("NFD", composed)
        names = ("", "  Chorus  ", composed, decomposed)
        events = normalize_events(
            ticks_per_quarter=480,
            tempos=(),
            meters=(),
            markers=tuple(Marker(0, name) for name in names),
        )
        self.assertCountEqual((marker.name for marker in events.markers), names)
        self.assertNotEqual(composed, decomposed)

    def test_normalization_is_idempotent_at_the_same_boundary(self):
        first = normalize_events(
            ticks_per_quarter=480,
            tempos=(OrderedTempoEvent(10, 0, 0, 400_000),),
            meters=(OrderedMeterEvent(10, 0, 0, 3, 4),),
            markers=(Marker(20, "B"), Marker(10, "A")),
        )
        second = normalize_events(
            ticks_per_quarter=first.ticks_per_quarter,
            tempos=first.tempos,
            meters=first.meters,
            markers=first.markers,
        )
        self.assertEqual(second, first)

    def test_raw_and_normalized_records_are_frozen(self):
        values = [
            OrderedTempoEvent(0, 0, 0, 500_000),
            OrderedMeterEvent(0, 0, 0, 4, 4),
            NormalizedEvents(480, (), (), ()),
        ]
        for value in values:
            with self.subTest(value=value), self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(value, dataclasses.fields(value)[0].name, None)

    def test_finalize_score_canonicalizes_sections_and_preserves_duplicates(self):
        events = normalize_events(ticks_per_quarter=480, tempos=(), meters=(), markers=())
        duplicate = Section("B", 20, 30, False)
        sections = (duplicate, Section("A", 10, 20, True), duplicate)
        score = finalize_score(
            events=events,
            sections=sections,
            source="midi",
            meter_estimated=False,
            bar_grid=simple_grid(),
        )
        self.assertEqual(score.sections, (Section("A", 10, 20, True), duplicate, duplicate))

    def test_finalize_score_takes_flags_only_from_bar_grid(self):
        events = normalize_events(ticks_per_quarter=480, tempos=(), meters=(), markers=())
        grid = BarGrid((0, 1), (MeterEvent(0, 4, 4),), (1,), ("x",), True, True)
        score = finalize_score(
            events=events,
            sections=(),
            source="analyzed",
            meter_estimated=True,
            bar_grid=grid,
        )
        self.assertTrue(score.has_variable_meter)
        self.assertTrue(score.has_midbar_meter_change)
        self.assertTrue(score.meter_estimated)
        self.assertNotIn("bar_grid", score.__dataclass_fields__)
        self.assertNotIn("diagnostics", score.__dataclass_fields__)

    def test_finalize_score_never_derives_sections_implicitly(self):
        events = normalize_events(
            ticks_per_quarter=480,
            tempos=(),
            meters=(),
            markers=(Marker(10, "A"),),
        )
        score = finalize_score(
            events=events,
            sections=(),
            source="midi",
            meter_estimated=False,
            bar_grid=simple_grid(),
        )
        self.assertEqual(score.markers, (Marker(10, "A"),))
        self.assertEqual(score.sections, ())


class MidiSectionDerivationTests(unittest.TestCase):
    def test_no_markers_produce_no_sections(self):
        self.assertEqual(
            derive_sections(markers=(), end_tick_exclusive=100, bar_grid=simple_grid()),
            (),
        )

    def test_first_marker_after_zero_does_not_invent_leading_section(self):
        sections = derive_sections(
            markers=(Marker(100, "Verse"),),
            end_tick_exclusive=200,
            bar_grid=simple_grid(),
        )
        self.assertEqual(sections, (Section("Verse", 100, 200, False, None),))

    def test_every_marker_creates_a_section_with_same_tick_zero_length(self):
        markers = (Marker(0, "A"), Marker(0, "B"), Marker(1920, "C"))
        sections = derive_sections(
            markers=markers,
            end_tick_exclusive=3000,
            bar_grid=simple_grid(),
        )
        self.assertEqual(len(sections), len(markers))
        self.assertIn(Section("A", 0, 0, True, None), sections)
        self.assertIn(Section("B", 0, 1920, True, None), sections)
        self.assertIn(Section("C", 1920, 3000, True, None), sections)

    def test_marker_at_end_creates_zero_length_final_section(self):
        sections = derive_sections(
            markers=(Marker(100, "End"),),
            end_tick_exclusive=100,
            bar_grid=simple_grid(),
        )
        self.assertEqual(sections, (Section("End", 100, 100, False, None),))

    def test_names_confidence_and_alignment_are_exact(self):
        composed = "é"
        decomposed = unicodedata.normalize("NFD", composed)
        markers = (
            Marker(0, ""),
            Marker(100, "  Chorus  "),
            Marker(200, decomposed),
            Marker(1920, composed),
        )
        sections = derive_sections(
            markers=markers,
            end_tick_exclusive=3000,
            bar_grid=simple_grid(),
        )
        self.assertEqual(tuple(section.name for section in sections), tuple(marker.name for marker in markers))
        self.assertTrue(all(section.confidence is None for section in sections))
        self.assertEqual(tuple(section.bar_aligned for section in sections), (True, False, False, True))
        self.assertEqual(sections[1].name, "  Chorus  ")
        self.assertEqual(sections[2].name, decomposed)


if __name__ == "__main__":
    unittest.main()
