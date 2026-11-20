"""Configuración central: rutas y conexiones leídas de variables de entorno."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")  # si no existe .env, se usan los valores por defecto

# --- Rutas ---
RAW_JSON_PATH = BASE_DIR / "data" / "raw" / "air_traffic_readings.json"
REJECTED_CSV_PATH = BASE_DIR / "data" / "processed" / "rejected_readings.csv"
SCHEMA_SQL_PATH = BASE_DIR / "sql" / "schema.sql"

# --- MongoDB (zona raw / landing) ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "urban_mobility")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "raw_readings")

# --- PostgreSQL (capa analítica) ---
POSTGRES_PARAMS = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname": os.getenv("POSTGRES_DB", "urban_mobility"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
}