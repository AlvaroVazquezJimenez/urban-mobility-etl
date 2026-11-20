"""Lee de MongoDB (raw), limpia/valida con pandas y hace UPSERT en PostgreSQL.

Uso:
    python -m src.transform_load_postgres
"""
import logging
import sys

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from pymongo import MongoClient

from src.config import (
    MONGO_COLLECTION, MONGO_DB, MONGO_URI, POSTGRES_PARAMS,
    REJECTED_CSV_PATH, SCHEMA_SQL_PATH,
)

logger = logging.getLogger(__name__)

EXPECTED_COLUMNS = [
    "station_id", "station_name", "district", "measured_at",
    "pm10", "no2", "temperature_c", "traffic_count", "avg_speed_kmh",
]

# Formatos de fecha observados en la fuente (se prueban uno a uno, de forma estricta)
DATE_FORMATS = ["%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M", "%Y/%m/%d %H:%M:%S"]

# Rangos físicamente plausibles (mín, máx). Fuera de rango -> se anula el valor.
MEASURE_RULES = {
    "pm10": (0, 500),
    "no2": (0, 500),
    "temperature_c": (-20, 50),
    "traffic_count": (0, 10_000),
    "avg_speed_kmh": (0, 150),
}

NULL_TOKENS = {"", "n/a", "na", "null", "none", "nan", "-"}

DIM_COLUMNS = ["station_id", "station_name", "district"]
FACT_COLUMNS = ["station_id", "measured_at", "pm10", "no2",
                "temperature_c", "traffic_count", "avg_speed_kmh"]

UPSERT_DIM_SQL = """
INSERT INTO dim_station AS d (station_id, station_name, district)
VALUES %s
ON CONFLICT (station_id) DO UPDATE
SET station_name = EXCLUDED.station_name,
    district     = EXCLUDED.district,
    updated_at   = NOW()
WHERE (d.station_name, d.district) IS DISTINCT FROM (EXCLUDED.station_name, EXCLUDED.district)
RETURNING (xmax = 0) AS inserted;
"""

UPSERT_FACT_SQL = """
INSERT INTO fact_reading AS f
    (station_id, measured_at, pm10, no2, temperature_c, traffic_count, avg_speed_kmh)
VALUES %s
ON CONFLICT (station_id, measured_at) DO UPDATE
SET pm10          = EXCLUDED.pm10,
    no2           = EXCLUDED.no2,
    temperature_c = EXCLUDED.temperature_c,
    traffic_count = EXCLUDED.traffic_count,
    avg_speed_kmh = EXCLUDED.avg_speed_kmh,
    loaded_at     = NOW()
WHERE (f.pm10, f.no2, f.temperature_c, f.traffic_count, f.avg_speed_kmh)
      IS DISTINCT FROM
      (EXCLUDED.pm10, EXCLUDED.no2, EXCLUDED.temperature_c, EXCLUDED.traffic_count, EXCLUDED.avg_speed_kmh)
RETURNING (xmax = 0) AS inserted;
"""


# ---------------------------------------------------------------- EXTRACT
def extract_raw_from_mongo() -> pd.DataFrame:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    try:
        cursor = (
            client[MONGO_DB][MONGO_COLLECTION]
            .find({}, {"_id": 0, "_meta.batch_id": 0, "_meta.source_file": 0})
            .sort([("_meta.first_ingested_at", 1), ("_meta.row_number", 1)])
        )
        docs = list(cursor)
    finally:
        client.close()

    if not docs:
        raise RuntimeError("La colección raw está vacía. Ejecuta antes: python -m src.extract_load_mongo")
    return pd.DataFrame(docs).reset_index(drop=True)


# -------------------------------------------------------------- TRANSFORM
def clean_text(series: pd.Series, upper: bool = False) -> pd.Series:
    """Quita espacios sobrantes y convierte 'nulos disfrazados' en NaN."""
    text = series.astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    if upper:
        text = text.str.upper()
    return text.mask(text.str.lower().isin(NULL_TOKENS))


def to_numeric_clean(series: pd.Series) -> pd.Series:
    """Convierte a número aceptando coma decimal y nulos disfrazados."""
    text = series.astype(str).str.strip().str.lower()
    text = text.mask(text.isin(NULL_TOKENS))
    text = text.str.replace(",", ".", regex=False)
    return pd.to_numeric(text, errors="coerce")


def parse_timestamps(series: pd.Series) -> pd.Series:
    """Prueba cada formato conocido; lo que no encaja en ninguno queda como NaT."""
    text = series.astype(str).str.strip()
    parsed = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    for fmt in DATE_FORMATS:
        attempt = pd.to_datetime(text, format=fmt, errors="coerce")
        parsed = parsed.fillna(attempt)
    return parsed


def clean_readings(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Devuelve (datos_limpios, filas_rechazadas, estadísticas)."""
    stats = {"rows_raw": len(raw)}
    df = raw.reindex(columns=EXPECTED_COLUMNS).copy()  # ignora columnas extra, crea las que falten

    # 1) Normalizar textos y claves
    df["station_id"] = clean_text(df["station_id"], upper=True)
    df["station_name"] = clean_text(df["station_name"])
    df["district"] = clean_text(df["district"])

    # 2) Fechas y números
    df["measured_at"] = parse_timestamps(df["measured_at"])
    for col in MEASURE_RULES:
        df[col] = to_numeric_clean(df[col])

    # 3) Validar rangos: valor imposible -> NaN (se conserva el resto de la fila)
    nulled = 0
    for col, (low, high) in MEASURE_RULES.items():
        out_of_range = (df[col] < low) | (df[col] > high)
        nulled += int(out_of_range.sum())
        df.loc[out_of_range, col] = np.nan
    stats["values_nulled_out_of_range"] = nulled

    # 4) Rechazar filas inservibles (se guardan aparte con el motivo)
    reason = pd.Series(
        np.select(
            [
                df["station_id"].isna(),
                df["measured_at"].isna(),
                df[list(MEASURE_RULES)].isna().all(axis=1),
            ],
            ["station_id ausente", "fecha inválida", "sin ninguna medida válida"],
            default="",
        ),
        index=df.index,
    )
    is_rejected = reason != ""
    rejected = raw.loc[is_rejected].assign(reject_reason=reason[is_rejected])
    clean = df.loc[~is_rejected].copy()
    stats["rows_rejected"] = int(is_rejected.sum())

    # 5) Deduplicar por clave natural: nos quedamos con la fila más completa
    before = len(clean)
    clean["_n_valid"] = clean[list(MEASURE_RULES)].notna().sum(axis=1)
    clean = (
        clean.sort_values("_n_valid", kind="stable")   # estable: a igualdad, manda la última ingerida
        .drop_duplicates(subset=["station_id", "measured_at"], keep="last")
        .drop(columns="_n_valid")
    )
    stats["duplicates_removed"] = before - len(clean)

    # 6) Tipos finales
    clean["traffic_count"] = clean["traffic_count"].round().astype("Int64")
    for col in ["pm10", "no2", "temperature_c", "avg_speed_kmh"]:
        clean[col] = clean[col].round(2)
    clean = clean.sort_values(["station_id", "measured_at"]).reset_index(drop=True)

    stats["rows_clean"] = len(clean)
    return clean, rejected, stats


def build_dim_station(clean: pd.DataFrame) -> pd.DataFrame:
    """Una fila por estación; ante nombres distintos, gana el más frecuente."""
    def most_common(values: pd.Series):
        values = values.dropna()
        return values.mode().iloc[0] if not values.empty else None

    dim = (
        clean.groupby("station_id")
        .agg(station_name=("station_name", most_common), district=("district", most_common))
        .reset_index()
    )
    dim["station_name"] = dim["station_name"].fillna("Desconocida")
    return dim[DIM_COLUMNS]


# ------------------------------------------------------------------- LOAD
def _to_python(value):
    """Convierte tipos de pandas/numpy a tipos nativos que psycopg2 entiende."""
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if hasattr(value, "item"):
        return value.item()
    return value


def to_rows(df: pd.DataFrame, columns: list[str]) -> list[tuple]:
    return [
        tuple(_to_python(v) for v in row)
        for row in df[columns].itertuples(index=False, name=None)
    ]


def upsert(cur, sql: str, rows: list[tuple]) -> dict:
    if not rows:
        return {"inserted": 0, "updated": 0, "unchanged": 0}
    returned = execute_values(cur, sql, rows, page_size=1000, fetch=True)
    inserted = sum(1 for (flag,) in returned if flag)
    updated = len(returned) - inserted
    return {"inserted": inserted, "updated": updated, "unchanged": len(rows) - len(returned)}


def load_to_postgres(dim: pd.DataFrame, fact: pd.DataFrame) -> None:
    conn = psycopg2.connect(**POSTGRES_PARAMS)
    try:
        with conn:  # una única transacción: o se carga todo o no se carga nada
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
                dim_result = upsert(cur, UPSERT_DIM_SQL, to_rows(dim, DIM_COLUMNS))
                fact_result = upsert(cur, UPSERT_FACT_SQL, to_rows(fact, FACT_COLUMNS))
        logger.info("dim_station  -> %s", dim_result)
        logger.info("fact_reading -> %s", fact_result)
    finally:
        conn.close()


# ------------------------------------------------------------------- MAIN
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    try:
        raw = extract_raw_from_mongo()
        logger.info("Leídos %d documentos de MongoDB.", len(raw))

        clean, rejected, stats = clean_readings(raw)
        logger.info("Resumen de limpieza: %s", stats)
        if not rejected.empty:
            REJECTED_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
            rejected.to_csv(REJECTED_CSV_PATH, index=False)
            logger.info("Motivos de rechazo:\n%s", rejected["reject_reason"].value_counts().to_string())
            logger.info("Filas rechazadas guardadas en %s", REJECTED_CSV_PATH)

        load_to_postgres(build_dim_station(clean), clean)
    except psycopg2.OperationalError as exc:
        logger.error("No se puede conectar a PostgreSQL: %s", exc)
        return 1
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())