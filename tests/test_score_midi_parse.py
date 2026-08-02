import dataclasses
import hashlib
import pathlib
import unittest
import unicodedata

from score.bars import build_bar_grid
from score.midi_parse import MidiParseError, ParsedMidiScore, parse_midi
from score.model import Marker, MeterEvent, Section, TempoEvent
from score.normalize import normalize_events
try:
    from tests.score_smf_test_utils import (
        channel_event,
        event,
        meta_event,
        smf,
        sysex_event,
        track_chunk,
    )
except ModuleNotFoundError:
    from score_smf_test_utils import (
        channel_event,
        event,
        meta_event,
        smf,
        sysex_event,
        track_chunk,
    )


ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "midi" / "velvet-lies.mid"
FIXTURE_SHA256 = "7F65AFF7B81D5BA3047E2C01D737DEB9D395157308D80398EC89A97264F14040"


def raw_smf(track_payload: bytes, *, midi_format: int = 0, tracks: int = 1, division: int = 480) -> bytes:
    header = (
        b"MThd"
        + b"\x00\x00\x00\x06"
        + midi_format.to_bytes(2, "big")
        + tracks.to_bytes(2, "big")
        + division.to_bytes(2, "big")
    )
    return header + b"MTrk" + len(track_payload).to_bytes(4, "big") + track_payload


def assert_error_code(test: unittest.TestCase, data, code: str) -> MidiParseError:
    with test.assertRaises(MidiParseError) as caught:
        parse_midi(data)
    test.assertEqual(caught.exception.code, code)
    return caught.exception


class MidiParserHappyPathTests(unittest.TestCase):
    def test_exact_real_cubase_fixture(self):
        data = FIXTURE.read_bytes()
        self.assertEqual(len(data), 129)
        self.assertEqual(hashlib.sha256(data).hexdigest().upper(), FIXTURE_SHA256)
        self.assertEqual(data[:4], b"MThd")
        self.assertEqual(int.from_bytes(data[8:10], "big"), 1)
        self.assertEqual(int.from_bytes(data[10:12], "big"), 1)
        self.assertEqual(int.from_bytes(data[12:14], "big"), 480)
        self.assertEqual(data[14:18], b"MTrk")
        self.assertEqual(int.from_bytes(data[18:22], "big"), 107)

        result = parse_midi(data)
        self.assertIsInstance(result, ParsedMidiScore)
        self.assertEqual(result.score.ticks_per_quarter, 480)
        self.assertEqual(result.score.tempos, (TempoEvent(0, 333_333),))
        self.assertEqual(
            result.score.meters,
            (
                MeterEvent(0, 4, 4),
                MeterEvent(101760, 31, 32),
                MeterEvent(103620, 4, 4),
                MeterEvent(224580, 31, 32),
                MeterEvent(226440, 4, 4),
                MeterEvent(270600, 31, 32),
                MeterEvent(272460, 4, 4),
                MeterEvent(320460, 63, 64),
                MeterEvent(322350, 4, 4),
            ),
        )
        self.assertEqual(result.end_tick_exclusive, 322350)
        self.assertEqual(result.score.markers, ())
        self.assertEqual(result.score.sections, ())
        self.assertTrue(result.score.has_variable_meter)
        self.assertFalse(result.score.has_midbar_meter_change)
        self.assertEqual(result.diagnostics, ())

    def test_format_zero_defaults(self):
        result = parse_midi(smf((track_chunk(()),), midi_format=0))
        self.assertEqual(result.score.tempos, (TempoEvent(0, 500_000),))
        self.assertEqual(result.score.meters, (MeterEvent(0, 4, 4),))
        self.assertEqual(result.end_tick_exclusive, 0)

    def test_format_one_multiple_tracks_and_stable_same_tick_merge(self):
        track_zero = track_chunk((meta_event(0, 0x51, (500_000).to_bytes(3, "big")),))
        track_one = track_chunk((meta_event(0, 0x51, (400_000).to_bytes(3, "big")),))
        result = parse_midi(smf((track_zero, track_one), midi_format=1))
        self.assertEqual(result.score.tempos, (TempoEvent(0, 400_000),))

    def test_channel_messages_and_running_status_are_skipped(self):
        events = (
            channel_event(0, 0x90, b"\x3C\x40"),
            channel_event(10, 0x90, b"\x3D\x40", running=True),
            channel_event(10, 0xC0, b"\x05"),
            channel_event(10, 0xD0, b"\x20"),
            meta_event(0, 0x51, (600_000).to_bytes(3, "big")),
        )
        result = parse_midi(smf((track_chunk(events),)))
        self.assertEqual(result.score.tempos, (TempoEvent(0, 500_000), TempoEvent(30, 600_000)))
        self.assertEqual(result.end_tick_exclusive, 30)

    def test_sysex_escaped_sysex_and_unknown_meta_are_skipped(self):
        events = (
            sysex_event(10, b"\x01\x02"),
            sysex_event(10, b"\x03", escaped=True),
            meta_event(10, 0x01, b"ignored"),
        )
        result = parse_midi(smf((track_chunk(events),)))
        self.assertEqual(result.end_tick_exclusive, 30)
        self.assertEqual(result.diagnostics, ())

    def test_utf8_marker_and_exact_whitespace_are_preserved(self):
        name = "  Chörus 🎵  "
        result = parse_midi(
            smf((track_chunk((meta_event(0, 0x06, name.encode("utf-8")),), eot_delta=480),))
        )
        self.assertEqual(result.score.markers, (Marker(0, name),))
        self.assertEqual(result.score.sections, (Section(name, 0, 480, True, None),))
        self.assertEqual(result.diagnostics, ())

    def test_latin1_marker_fallback_is_visible_and_exact(self):
        result = parse_midi(smf((track_chunk((meta_event(0, 0x06, b"\xFF"),)),)))
        self.assertEqual(result.score.markers, (Marker(0, "ÿ"),))
        self.assertEqual(
            result.diagnostics,
            ("marker at track 0 event 0 decoded as Latin-1",),
        )

    def test_marker_unicode_is_not_normalized(self):
        decomposed = unicodedata.normalize("NFD", "é")
        result = parse_midi(
            smf((track_chunk((meta_event(0, 0x06, decomposed.encode("utf-8")),)),))
        )
        self.assertEqual(result.score.markers[0].name, decomposed)
        self.assertNotEqual(result.score.markers[0].name, unicodedata.normalize("NFC", decomposed))

    def test_marker_derived_sections_include_same_tick_and_end_cases(self):
        events = (
            meta_event(100, 0x06, b"A"),
            meta_event(0, 0x06, b"B"),
            meta_event(100, 0x06, b""),
        )
        result = parse_midi(smf((track_chunk(events),)))
        self.assertEqual(result.end_tick_exclusive, 200)
        self.assertEqual(len(result.score.sections), 3)
        self.assertIn(Section("A", 100, 100, False, None), result.score.sections)
        self.assertIn(Section("B", 100, 200, False, None), result.score.sections)
        self.assertIn(Section("", 200, 200, False, None), result.score.sections)
        self.assertTrue(all(section.confidence is None for section in result.score.sections))

    def test_end_tick_is_maximum_across_tracks(self):
        data = smf(
            (
                track_chunk((), eot_delta=100),
                track_chunk((), eot_delta=250),
            ),
            midi_format=1,
        )
        self.assertEqual(parse_midi(data).end_tick_exclusive, 250)

    def test_missing_eot_uses_last_fully_parsed_tick_and_diagnostic(self):
        track = track_chunk((channel_event(123, 0x90, b"\x3C\x40"),), include_eot=False)
        result = parse_midi(smf((track,)))
        self.assertEqual(result.end_tick_exclusive, 123)
        self.assertEqual(result.diagnostics, ("track 0 missing End of Track; using tick 123",))

    def test_empty_track_without_eot_uses_tick_zero(self):
        result = parse_midi(smf((track_chunk((), include_eot=False),)))
        self.assertEqual(result.end_tick_exclusive, 0)
        self.assertEqual(result.diagnostics, ("track 0 missing End of Track; using tick 0",))

    def test_normalized_midi_result_is_idempotent(self):
        result = parse_midi(FIXTURE.read_bytes())
        normalized = normalize_events(
            ticks_per_quarter=result.score.ticks_per_quarter,
            tempos=result.score.tempos,
            meters=result.score.meters,
            markers=result.score.markers,
        )
        self.assertEqual(normalized.tempos, result.score.tempos)
        self.assertEqual(normalized.meters, result.score.meters)
        self.assertEqual(normalized.markers, result.score.markers)

    def test_real_score_flags_match_longer_grids(self):
        result = parse_midi(FIXTURE.read_bytes())
        for through_tick in (322350, 400000, 999999):
            grid = build_bar_grid(
                ticks_per_quarter=result.score.ticks_per_quarter,
                meters=result.score.meters,
                through_tick=through_tick,
            )
            self.assertEqual(grid.has_variable_meter, result.score.has_variable_meter)
            self.assertEqual(
                grid.has_midbar_meter_change,
                result.score.has_midbar_meter_change,
            )


class MidiMetaEventTests(unittest.TestCase):
    def test_time_signature_exponent_values(self):
        cases = [
            (b"\x04\x02\x18\x08", MeterEvent(0, 4, 4)),
            (b"\x1F\x05\x18\x08", MeterEvent(0, 31, 32)),
            (b"\x3F\x06\x18\x08", MeterEvent(0, 63, 64)),
            (b"\x03\x01\x18\x08", MeterEvent(0, 3, 2)),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                result = parse_midi(smf((track_chunk((meta_event(0, 0x58, payload),)),)))
                self.assertEqual(result.score.meters, (expected,))

    def test_time_signature_discards_cc_and_bb(self):
        result = parse_midi(
            smf((track_chunk((meta_event(0, 0x58, b"\x04\x02\x7F\x00"),)),))
        )
        self.assertEqual(result.score.meters, (MeterEvent(0, 4, 4),))
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(MeterEvent)),
            ("tick", "numerator", "denominator"),
        )

    def test_invalid_time_signatures_are_rejected(self):
        for payload, code in (
            (b"\x00\x02\x18\x08", "invalid_meter"),
            (b"\x04\x07\x18\x08", "invalid_meter"),
            (b"\x04\x02\x18", "invalid_meta_length"),
        ):
            with self.subTest(payload=payload):
                assert_error_code(
                    self,
                    smf((track_chunk((meta_event(0, 0x58, payload),)),)),
                    code,
                )

    def test_tempo_is_big_endian_three_byte_value(self):
        result = parse_midi(
            smf((track_chunk((meta_event(0, 0x51, b"\x05\x16\x15"),)),))
        )
        self.assertEqual(result.score.tempos, (TempoEvent(0, 333_333),))

    def test_invalid_tempos_are_rejected(self):
        for payload, code in (
            (b"\x00\x00\x00", "invalid_tempo"),
            (b"\x01\x02", "invalid_meta_length"),
        ):
            with self.subTest(payload=payload):
                assert_error_code(
                    self,
                    smf((track_chunk((meta_event(0, 0x51, payload),)),)),
                    code,
                )

    def test_eot_payload_must_be_empty(self):
        data = raw_smf(b"\x00\xFF\x2F\x01\x00")
        assert_error_code(self, data, "invalid_meta_length")


class MidiRunningStatusTests(unittest.TestCase):
    def test_new_explicit_channel_status_after_system_events_is_valid(self):
        system_events = (
            sysex_event(0, b""),
            sysex_event(0, b"", escaped=True),
            meta_event(0, 0x01, b""),
        )
        events = [channel_event(0, 0x90, b"\x3C\x40"), channel_event(0, 0x90, b"\x3D\x40", running=True)]
        for system_event in system_events:
            events.extend((system_event, channel_event(0, 0x90, b"\x3E\x40")))
        result = parse_midi(smf((track_chunk(tuple(events)),)))
        self.assertEqual(result.end_tick_exclusive, 0)

    def test_sysex_escaped_sysex_and_meta_clear_running_status(self):
        reset_payloads = (
            b"\x00\xF0\x00",
            b"\x00\xF7\x00",
            b"\x00\xFF\x01\x00",
        )
        for reset in reset_payloads:
            payload = b"\x00\x90\x3C\x40" + reset + b"\x00\x3D\x40"
            with self.subTest(reset=reset):
                assert_error_code(self, raw_smf(payload), "invalid_running_status")


class MidiStructuralFailureTests(unittest.TestCase):
    def test_non_bytes_input(self):
        error = assert_error_code(self, bytearray(), "invalid_type")
        self.assertEqual(error.byte_offset, 0)
        self.assertIsNone(error.track_index)
        self.assertIsNone(error.event_index)
        self.assertIn("byte 0", str(error))

    def test_truncated_and_invalid_headers(self):
        for data, code in (
            (b"", "truncated_header"),
            (b"MThd\x00\x00", "truncated_header"),
            (b"NOPE" + b"\x00" * 10, "invalid_header"),
            (b"MThd\x00\x00\x00\x05" + b"\x00" * 6, "invalid_header"),
            (b"MThd\x00\x00\x00\x06" + b"\x00" * 5, "truncated_header"),
        ):
            with self.subTest(data=data):
                assert_error_code(self, data, code)

    def test_unsupported_format_and_smpte_division(self):
        valid = bytearray(smf((track_chunk(()),)))
        format_two = bytearray(valid)
        format_two[8:10] = (2).to_bytes(2, "big")
        assert_error_code(self, bytes(format_two), "unsupported_format")
        smpte = bytearray(valid)
        smpte[12:14] = (0xE728).to_bytes(2, "big")
        assert_error_code(self, bytes(smpte), "smpte_division")

    def test_declared_track_count_disagreement_and_missing_mtrk(self):
        valid = bytearray(smf((track_chunk(()),)))
        too_many = bytearray(valid)
        too_many[10:12] = (2).to_bytes(2, "big")
        assert_error_code(self, bytes(too_many), "track_count_mismatch")
        missing = bytearray(valid)
        missing[14:18] = b"JUNK"
        assert_error_code(self, bytes(missing), "track_count_mismatch")

    def test_truncated_track_header_and_payload(self):
        header = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xE0"
        assert_error_code(self, header + b"MTr", "truncated_track")
        valid = smf((track_chunk(()),))
        assert_error_code(self, valid[:-1], "truncated_track")

    def test_invalid_and_overlong_vlq(self):
        assert_error_code(self, raw_smf(b"\x81"), "invalid_vlq")
        assert_error_code(self, raw_smf(b"\x81\x80\x80\x80\x00"), "invalid_vlq")

    def test_data_without_status_and_unsupported_system_status(self):
        assert_error_code(self, raw_smf(b"\x00\x40"), "invalid_running_status")
        assert_error_code(self, raw_smf(b"\x00\xF1"), "unsupported_status")

    def test_read_beyond_event_payload(self):
        assert_error_code(self, raw_smf(b"\x00\xFF\x01\x02A"), "truncated_track")
        assert_error_code(self, raw_smf(b"\x00\xF0\x02A"), "truncated_track")

    def test_event_after_eot_is_rejected(self):
        payload = b"\x00\xFF\x2F\x00\x00\x90\x3C\x40"
        assert_error_code(self, raw_smf(payload), "event_after_eot")

    def test_unexpected_trailing_bytes_or_chunks_are_rejected(self):
        valid = smf((track_chunk(()),))
        assert_error_code(self, valid + b"X", "trailing_data")
        assert_error_code(self, valid + track_chunk(()), "track_count_mismatch")

    def test_every_strict_real_fixture_prefix_raises_only_public_error(self):
        data = FIXTURE.read_bytes()
        for length in range(len(data)):
            with self.subTest(length=length), self.assertRaises(MidiParseError):
                parse_midi(data[:length])


if __name__ == "__main__":
    unittest.main()
