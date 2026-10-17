"""Boundary units for time, exact counters and retry identity."""

from decimal import Decimal

import pytest

from backend.domain import parse_meter, parse_timestamp, start_fingerprint


@pytest.mark.parametrize(
    "value", ["NaN", "Infinity", "1e999999999", "1e-999999999", "0.0000001", "1_000", True]
)
def test_invalid_meter_numbers_are_bounded_value_errors(value):
    with pytest.raises(ValueError):
        parse_meter(value)


def test_maximum_exact_meter_precision_does_not_round_before_validation():
    value = "999999999999999999999999.999999"
    assert parse_meter(value) == Decimal(value)
    assert parse_meter("-1000") == Decimal("-1000")


def test_retry_identity_normalizes_offsets_and_accepts_unknown_empty_tags():
    utc = parse_timestamp("2026-10-17T10:00:00Z")
    offset = parse_timestamp("2026-10-17T12:00:00+02:00")
    assert utc == offset
    assert start_fingerprint("CP-1", 1, "", utc, parse_meter(1000)) == start_fingerprint(
        "CP-1", 1, "", offset, parse_meter("1000.0")
    )
    with pytest.raises(ValueError):
        parse_timestamp("2026-10-17T10:00:00")
