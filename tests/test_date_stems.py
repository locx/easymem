from semantic_server.text import extract_date_stems


def test_dmy_with_comma():
    assert "date_2023_05_08" in extract_date_stems(
        "met on 8 May, 2023 to talk"
    )


def test_dmy_without_comma():
    assert "date_2023_05_08" in extract_date_stems("8 May 2023")


def test_mdy_with_comma():
    assert "date_2023_05_08" in extract_date_stems("May 8, 2023 meeting")


def test_iso_format():
    assert "date_2023_05_08" in extract_date_stems(
        "scheduled 2023-05-08 at 3pm"
    )


def test_formats_canonicalize_identically():
    a = extract_date_stems("8 May 2023")
    b = extract_date_stems("May 8, 2023")
    c = extract_date_stems("2023-05-08")
    assert a == b == c == ["date_2023_05_08"]


def test_empty_and_no_date():
    assert extract_date_stems("") == []
    assert extract_date_stems("no date here") == []
    assert extract_date_stems(None) == []


def test_zero_padding():
    out = extract_date_stems("1 January 2024")
    assert "date_2024_01_01" in out
