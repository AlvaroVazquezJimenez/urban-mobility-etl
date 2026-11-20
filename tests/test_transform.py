import pandas as pd

from src.transform_load_postgres import clean_readings, parse_timestamps, to_numeric_clean


def test_parse_timestamps_handles_multiple_formats_and_invalid_dates():
    series = pd.Series([
        "2026-03-01T08:00:00",
        "01/03/2026 08:00",
        "2026/03/01 08:00:00",
        "31/02/2026 10:00",   # fecha imposible
        "not-a-date",
    ])
    parsed = parse_timestamps(series)
    expected = pd.Timestamp("2026-03-01 08:00")
    assert list(parsed.iloc[:3]) == [expected] * 3
    assert parsed.isna().tolist() == [False, False, False, True, True]


def test_to_numeric_clean_handles_decimal_comma_and_fake_nulls():
    result = to_numeric_clean(pd.Series(["42,5", "N/A", "", None, "7"]))
    assert result.iloc[0] == 42.5
    assert result.iloc[1:4].isna().all()
    assert result.iloc[4] == 7


def _row(station_id, measured_at, pm10):
    return {
        "station_id": station_id, "station_name": "Plaza Mayor", "district": "Centro",
        "measured_at": measured_at, "pm10": pm10, "no2": 40,
        "temperature_c": 12.0, "traffic_count": 900, "avg_speed_kmh": 30,
    }


def test_clean_readings_end_to_end():
    raw = pd.DataFrame([
        _row("ST-001", "2026-03-01T08:00:00", "20,5"),
        _row(" st-001 ", "01/03/2026 08:00", "20,5"),     # duplicado con otro formato
        _row("ST-001", "2026-03-01T09:00:00", -5),        # PM10 negativo -> se anula
        _row(None, "2026-03-01T10:00:00", 15),            # sin estación -> rechazada
        _row("ST-001", "31/02/2026 10:00", 15),           # fecha inválida -> rechazada
    ])

    clean, rejected, stats = clean_readings(raw)

    assert len(clean) == 2
    assert len(rejected) == 2
    assert stats["duplicates_removed"] == 1
    assert stats["values_nulled_out_of_range"] == 1
    assert clean["station_id"].tolist() == ["ST-001", "ST-001"]
    assert clean.loc[clean["measured_at"] == pd.Timestamp("2026-03-01 09:00"), "pm10"].isna().all()
    assert set(rejected["reject_reason"]) == {"station_id ausente", "fecha inválida"}