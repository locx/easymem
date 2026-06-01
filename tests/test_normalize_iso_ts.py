from semantic_server.config import normalize_iso_ts


def test_clamps_feb_31_in_leap_year():
    assert normalize_iso_ts("2024-02-31") == "2024-02-29"


def test_clamps_feb_31_in_non_leap_year():
    assert normalize_iso_ts("2023-02-31") == "2023-02-28"


def test_clamps_april_31():
    assert normalize_iso_ts("2024-04-31") == "2024-04-30"


def test_clamps_with_time_suffix_preserved():
    assert normalize_iso_ts("2024-04-31T10:30:00Z") == "2024-04-30T10:30:00Z"


def test_valid_leap_day_unchanged():
    assert normalize_iso_ts("2024-02-29") == "2024-02-29"


def test_valid_date_unchanged():
    assert normalize_iso_ts("2024-03-15T10:00:00Z") == "2024-03-15T10:00:00Z"
