"""Ingesta del JSON crudo a MongoDB (zona raw / landing) de forma idempotente.

Uso:
    python -m src.extract_load_mongo
    python -m src.extract_load_mongo --reset     # vacía la colección antes de cargar
"""
import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient, UpdateOne
from pymongo.errors import ServerSelectionTimeoutError

from src.config import MONGO_COLLECTION, MONGO_DB, MONGO_URI, RAW_JSON_PATH

logger = logging.getLogger(__name__)
CHUNK_SIZE = 1000


def file_sha256(path: Path) -> str:
    """Huella digital del fichero: cambia si cambia su contenido."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_operations(records: list[dict], batch_id: str, source_file: str, now: datetime) -> list[UpdateOne]:
    """Una operación upsert por registro, con _id determinista."""
    operations = []
    for row_number, record in enumerate(records):
        doc_id = f"{batch_id}-{row_number:06d}"
        operations.append(
            UpdateOne(
                {"_id": doc_id},
                {
                    # El dato original tal cual + metadatos de trazabilidad
                    "$set": {
                        **record,
                        "_meta.batch_id": batch_id,
                        "_meta.source_file": source_file,
                        "_meta.row_number": row_number,
                    },
                    # Solo se escribe la primera vez que se inserta
                    "$setOnInsert": {"_meta.first_ingested_at": now},
                },
                upsert=True,
            )
        )
    return operations


def load_to_mongo(collection, operations: list[UpdateOne]) -> dict:
    totals = {"inserted": 0, "matched": 0, "modified": 0}
    for i in range(0, len(operations), CHUNK_SIZE):
        result = collection.bulk_write(operations[i:i + CHUNK_SIZE], ordered=False)
        totals["inserted"] += result.upserted_count
        totals["matched"] += result.matched_count
        totals["modified"] += result.modified_count
    return totals


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Carga el JSON crudo en MongoDB (zona raw).")
    p.add_argument("--input", default=str(RAW_JSON_PATH), help="Ruta del JSON de entrada.")
    p.add_argument("--reset", action="store_true", help="Vacía la colección antes de cargar.")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    args = parse_args()

    path = Path(args.input)
    if not path.exists():
        logger.error("No existe %s. Ejecuta antes: python -m src.generate_sample_data", path)
        return 1

    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        logger.error("Se esperaba una lista de registros en el JSON.")
        return 1

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except ServerSelectionTimeoutError:
        logger.error("No se puede conectar a MongoDB en %s. ¿Está levantado (docker compose up -d)?", MONGO_URI)
        return 1

    try:
        collection = client[MONGO_DB][MONGO_COLLECTION]
        if args.reset:
            collection.drop()
            logger.info("Colección '%s' vaciada.", MONGO_COLLECTION)

        batch_id = file_sha256(path)[:12]
        now = datetime.now(timezone.utc)
        operations = build_operations(records, batch_id, path.name, now)
        totals = load_to_mongo(collection, operations)

        logger.info("Fichero: %s | lote: %s | registros leídos: %d", path.name, batch_id, len(records))
        logger.info("Nuevos: %d | ya existían: %d | modificados: %d",
                    totals["inserted"], totals["matched"], totals["modified"])
        logger.info("Total de documentos en la colección: %d", collection.count_documents({}))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())