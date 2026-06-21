"""Tests for the parsing and normalization helpers.

The encoding cases use the exact mojibake vlrggapi returned for real teams, so
these assert the actual repair, not a synthetic stand-in.
"""
from valtrack.cleaning import fix_encoding, parse_date, parse_int, parse_score


def _mojibake(original):
    """Reproduce how vlrggapi mangles a UTF-8 string.

    It decodes the real UTF-8 bytes as cp1252, keeping bytes cp1252 cannot map
    as surrogate escapes. Building the test input this way avoids hand-typing
    fragile mojibake characters.
    """
    return original.encode("utf-8").decode("cp1252", "surrogateescape")


class TestFixEncoding:
    def test_accented_name_with_surrogate(self):
        # LEVIATAN: the accented A becomes U+00C3 plus a U+DC81 surrogate.
        assert fix_encoding(_mojibake("LEVIATÁN")) == "LEVIATÁN"

    def test_en_dash_in_record(self):
        # The record separator is an en dash, double-encoded as three chars.
        assert fix_encoding(_mojibake("19–9")) == "19–9"

    def test_kru_esports(self):
        assert fix_encoding(_mojibake("KRÜ Esports")) == "KRÜ Esports"

    def test_plain_ascii_unchanged(self):
        assert fix_encoding("Sentinels") == "Sentinels"

    def test_non_string_passthrough(self):
        assert fix_encoding(None) is None
        assert fix_encoding(7) == 7


class TestParseScore:
    def test_decided(self):
        assert parse_score("2:1") == (2, 1)
        assert parse_score("1:2") == (1, 2)

    def test_with_spaces(self):
        assert parse_score(" 0 : 2 ") == (0, 2)

    def test_missing(self):
        assert parse_score("") == (None, None)
        assert parse_score(None) == (None, None)

    def test_malformed(self):
        assert parse_score("TBD") == (None, None)
        assert parse_score("x:y") == (None, None)


class TestParseDate:
    def test_slash_format(self):
        assert parse_date("2026/05/31") == "2026-05-31"

    def test_already_dashed(self):
        assert parse_date("2026-05-31") == "2026-05-31"

    def test_missing(self):
        assert parse_date("") is None
        assert parse_date(None) is None

    def test_unexpected_shape(self):
        assert parse_date("May 31, 2026") is None


class TestParseInt:
    def test_plain(self):
        assert parse_int("3") == 3
        assert parse_int(5) == 5

    def test_decorated(self):
        assert parse_int("#12") == 12
        assert parse_int("rank 1") == 1

    def test_negative(self):
        assert parse_int("-4") == -4

    def test_none_and_empty(self):
        assert parse_int(None) is None
        assert parse_int("") is None
        assert parse_int("n/a") is None
