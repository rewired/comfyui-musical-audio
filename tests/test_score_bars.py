import random
import unittest

from score.bars import build_bar_grid
from score.model import MeterEvent
from score.normalize import normalize_events


REAL_METERS = (
    MeterEvent(0, 4, 4),
    MeterEvent(101760, 31, 32),
    MeterEvent(103620, 4, 4),
    MeterEvent(224580, 31, 32),
    MeterEvent(226440, 4, 4),
    MeterEvent(270600, 31, 32),
    MeterEvent(272460, 4, 4),
    MeterEvent(320460, 63, 64),
    MeterEvent(322350, 4, 4),
)


class ScoreBarGridTests(unittest.TestCase):
    def test_constant_four_four_boundaries(self):
        grid = build_bar_grid(
            ticks_per_quarter=480,
            meters=(MeterEvent(0, 4, 4),),
            through_tick=2000,
        )
        self.assertEqual(grid.boundaries, (0, 1920, 3840))
        self.assertEqual(grid.meters_by_bar, (MeterEvent(0, 4, 4),) * 2)

    def test_through_tick_on_boundary_includes_following_bar(self):
        grid = build_bar_grid(
            ticks_per_quarter=480,
            meters=(MeterEvent(0, 4, 4),),
            through_tick=1920,
        )
        self.assertEqual(grid.boundaries, (0, 1920, 3840))

    def test_boundary_and_meter_count_invariants(self):
        grid = build_bar_grid(
            ticks_per_quarter=480,
            meters=(MeterEvent(0, 4, 4),),
            through_tick=20_000,
        )
        self.assertEqual(len(grid.boundaries), len(grid.meters_by_bar) + 1)
        self.assertEqual(grid.boundaries[0], 0)
        self.assertGreater(grid.boundaries[-1], 20_000)
        self.assertTrue(all(a < b for a, b in zip(grid.boundaries, grid.boundaries[1:])))

    def test_odd_meter_lengths_at_480_tpq(self):
        grid_31 = build_bar_grid(
            ticks_per_quarter=480,
            meters=(MeterEvent(0, 31, 32),),
            through_tick=0,
        )
        grid_63 = build_bar_grid(
            ticks_per_quarter=480,
            meters=(MeterEvent(0, 63, 64),),
            through_tick=0,
        )
        self.assertEqual(grid_31.boundaries, (0, 1860))
        self.assertEqual(grid_63.boundaries, (0, 1890))

    def test_meter_change_on_boundary_starts_following_bar(self):
        meters = (MeterEvent(0, 4, 4), MeterEvent(1920, 3, 4))
        grid = build_bar_grid(ticks_per_quarter=480, meters=meters, through_tick=1920)
        self.assertEqual(grid.boundaries, (0, 1920, 3360))
        self.assertEqual(grid.meters_by_bar, meters)
        self.assertFalse(grid.has_midbar_meter_change)
        self.assertEqual(grid.midbar_change_ticks, ())
        self.assertEqual(grid.diagnostics, ())

    def test_midbar_change_shortens_bar_at_exact_tick(self):
        meters = (MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4))
        grid = build_bar_grid(ticks_per_quarter=480, meters=meters, through_tick=1000)
        self.assertEqual(grid.boundaries, (0, 1000, 2440))
        self.assertEqual(grid.midbar_change_ticks, (1000,))
        self.assertTrue(grid.has_midbar_meter_change)
        self.assertEqual(
            grid.diagnostics,
            ("meter change at tick 1000 shortened a bar",),
        )

    def test_distinct_effective_signatures_set_variable_meter(self):
        grid = build_bar_grid(
            ticks_per_quarter=480,
            meters=(MeterEvent(0, 4, 4), MeterEvent(1920, 4, 8)),
            through_tick=1920,
        )
        self.assertTrue(grid.has_variable_meter)

    def test_repeated_equivalent_meters_are_removed_before_grid(self):
        events = normalize_events(
            ticks_per_quarter=480,
            tempos=(),
            meters=(MeterEvent(0, 4, 4), MeterEvent(960, 4, 4)),
            markers=(),
        )
        self.assertEqual(events.meters, (MeterEvent(0, 4, 4),))
        grid = build_bar_grid(
            ticks_per_quarter=480,
            meters=events.meters,
            through_tick=960,
        )
        self.assertFalse(grid.has_variable_meter)
        self.assertFalse(grid.has_midbar_meter_change)

    def test_absolute_half_tick_rounding_does_not_drift(self):
        grid = build_bar_grid(
            ticks_per_quarter=100,
            meters=(MeterEvent(0, 1, 32),),
            through_tick=38,
        )
        self.assertEqual(grid.boundaries, (0, 13, 25, 38, 50))

    def test_unrepresentable_nonincreasing_boundary_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "increasing boundaries"):
            build_bar_grid(
                ticks_per_quarter=1,
                meters=(MeterEvent(0, 1, 64),),
                through_tick=0,
            )

    def test_invalid_preconditions_are_rejected(self):
        valid = (MeterEvent(0, 4, 4),)
        cases = [
            dict(ticks_per_quarter=True, meters=valid, through_tick=0),
            dict(ticks_per_quarter=0, meters=valid, through_tick=0),
            dict(ticks_per_quarter=480, meters=valid, through_tick=-1),
            dict(ticks_per_quarter=480, meters=(), through_tick=0),
            dict(ticks_per_quarter=480, meters=(MeterEvent(1, 4, 4),), through_tick=1),
            dict(
                ticks_per_quarter=480,
                meters=(MeterEvent(0, 4, 4), MeterEvent(10, 3, 4)),
                through_tick=9,
            ),
            dict(
                ticks_per_quarter=480,
                meters=(MeterEvent(0, 4, 4), MeterEvent(0, 3, 4)),
                through_tick=0,
            ),
            dict(
                ticks_per_quarter=480,
                meters=(MeterEvent(0, 4, 4), MeterEvent(10, 4, 4)),
                through_tick=10,
            ),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises((TypeError, ValueError)):
                build_bar_grid(**case)

    def test_real_cubase_boundary_checkpoints_and_flags(self):
        grid = build_bar_grid(
            ticks_per_quarter=480,
            meters=REAL_METERS,
            through_tick=322350,
        )
        checkpoints = {
            0,
            101760,
            103620,
            224580,
            226440,
            270600,
            272460,
            320460,
            322350,
            324270,
        }
        self.assertTrue(checkpoints.issubset(grid.boundaries))
        self.assertEqual(grid.boundaries[-1], 324270)
        self.assertTrue(grid.has_variable_meter)
        self.assertFalse(grid.has_midbar_meter_change)
        self.assertEqual(grid.diagnostics, ())

    def test_flags_are_invariant_across_deterministic_through_ticks(self):
        fixtures = [
            (MeterEvent(0, 4, 4),),
            (MeterEvent(0, 4, 4), MeterEvent(1920, 3, 4)),
            (MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
            REAL_METERS,
        ]
        randomizer = random.Random(8675309)
        for meters in fixtures:
            construction = build_bar_grid(
                ticks_per_quarter=480,
                meters=meters,
                through_tick=meters[-1].tick,
            )
            for _ in range(20):
                through_tick = meters[-1].tick + randomizer.randrange(0, 100_000)
                grid = build_bar_grid(
                    ticks_per_quarter=480,
                    meters=meters,
                    through_tick=through_tick,
                )
                self.assertEqual(grid.has_variable_meter, construction.has_variable_meter)
                self.assertEqual(
                    grid.has_midbar_meter_change,
                    construction.has_midbar_meter_change,
                )


if __name__ == "__main__":
    unittest.main()
