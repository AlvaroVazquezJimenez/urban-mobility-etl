"""Genera un JSON sintético de lecturas de calidad del aire y tráfico urbano,
con fallos intencionados para practicar limpieza de datos.

Uso:
    python -m src.generate_sample_data --days 14 --seed 42
"""
import argparse
import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

from src.config import RAW_JSON_PATH

# Estaciones ficticias. Los "base_*" fijan el nivel típico de cada una.
STATIONS = [
    {"station_id": "ST-001", "station_name": "Plaza Mayor",     "district": "Centro",     "base_no2": 42, "base_pm10": 26, "base_traffic": 950},
    {"station_id": "ST-002", "station_name": "Puerta del Sol",  "district": "Centro",     "base_no2": 46, "base_pm10": 28, "base_traffic": 1100},
    {"station_id": "ST-003", "station_name": "Calle Serrano",   "district": "Salamanca",  "base_no2": 40, "base_pm10": 24, "base_traffic": 1000},
    {"station_id": "ST-004", "station_name": "Parque Retiro Sur","district": "Salamanca", "base_no2": 22, "base_pm10": 18, "base_traffic": 350},
    {"station_id": "ST-005", "station_name": "Paseo Delicias",  "district": "Arganzuela", "base_no2": 35, "base_pm10": 22, "base_traffic": 800},
    {"station_id": "ST-006", "station_name": "Cuatro Caminos",  "district": "Tetuán",     "base_no2": 44, "base_pm10": 30, "base_traffic": 1050},
    {"station_id": "ST-007", "station_name": "Avenida Oporto",  "district": "Carabanchel","base_no2": 38, "base_pm10": 34, "base_traffic": 900},
    {"station_id": "ST-008", "station_name": "Nuevos Ministerios","district": "Chamartín","base_no2": 41, "base_pm10": 25, "base_traffic": 1000},
]

# Formatos de fecha "mezclados", como pasaría con fuentes distintas.
DATE_FORMATS = ["%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M", "%Y/%m/%d %H:%M:%S"]
DATE_WEIGHTS = [70, 20, 10]

NULL_VARIANTS = [None, "", "N/A", "null"]
EPISODE_DAYS = {5, 6}  # días 6 y 7 del periodo: episodio de alta contaminación


def traffic_factor(hour: int) -> float:
    """Multiplicador de tráfico según la hora (horas punta y madrugada)."""
    if 7 <= hour <= 9:
        return 1.6
    if 18 <= hour <= 20:
        return 1.5
    if 0 <= hour <= 5:
        return 0.3
    return 1.0


def build_clean_reading(station: dict, ts: datetime, day_index: int, rng: random.Random) -> dict:
    """Crea una lectura 'perfecta' (sin errores) con relaciones realistas."""
    factor = traffic_factor(ts.hour)
    episode = 2.2 if day_index in EPISODE_DAYS else 1.0

    traffic = int(station["base_traffic"] * factor * rng.uniform(0.85, 1.15))
    no2 = station["base_no2"] * (0.5 + 0.5 * factor) * rng.uniform(0.8, 1.2)  # sube con el tráfico
    pm10 = station["base_pm10"] * episode * rng.uniform(0.7, 1.4)
    temperature = 12 + 8 * math.sin(2 * math.pi * (ts.hour - 9) / 24) + rng.gauss(0, 1)
    speed = max(8, 45 - 0.02 * traffic + rng.gauss(0, 3))  # más tráfico -> menos velocidad

    return {
        "reading_id": f"{station['station_id']}_{ts:%Y%m%dT%H%M}",
        "station_id": station["station_id"],
        "station_name": station["station_name"],
        "district": station["district"],
        "measured_at": ts,  # todavía es datetime; se convierte a texto en corrupt()
        "pm10": round(pm10, 1),
        "no2": round(no2, 1),
        "temperature_c": round(temperature, 1),
        "traffic_count": traffic,
        "avg_speed_kmh": round(speed, 1),
    }


def corrupt(clean: dict, rng: random.Random) -> dict:
    """Aplica fallos 'reales' de forma aleatoria sobre una lectura limpia."""
    rec = dict(clean)

    # 1) Fecha en formato aleatorio (y a veces inválida)
    ts = rec["measured_at"]
    rec["measured_at"] = ts.strftime(rng.choices(DATE_FORMATS, weights=DATE_WEIGHTS)[0])
    if rng.random() < 0.005:
        rec["measured_at"] = rng.choice(["31/02/2026 10:00", "not-a-date"])

    # 2) PM10: nulos, coma decimal, centinelas negativos, atípicos
    roll = rng.random()
    if roll < 0.03:
        rec["pm10"] = rng.choice(NULL_VARIANTS)
    elif roll < 0.05:
        rec["pm10"] = f"{rec['pm10']:.1f}".replace(".", ",")  # "23,4" (formato español)
    elif roll < 0.06:
        rec["pm10"] = rng.choice([-1, -5, -999])
    elif roll < 0.07:
        rec["pm10"] = 9999

    # 3) NO2: nulos y negativos
    roll = rng.random()
    if roll < 0.03:
        rec["no2"] = rng.choice(NULL_VARIANTS)
    elif roll < 0.04:
        rec["no2"] = -10.0

    # 4) Temperatura, tráfico y velocidad: atípicos / nulos
    roll = rng.random()
    if roll < 0.01:
        rec["temperature_c"] = 85.0
    elif roll < 0.03:
        rec["temperature_c"] = None
    if rng.random() < 0.01:
        rec["traffic_count"] = -20
    if rng.random() < 0.01:
        rec["avg_speed_kmh"] = 320.0

    # 5) Identificadores y textos sucios
    roll = rng.random()
    if roll < 0.003:
        rec["station_id"] = None                              # falta la clave -> se rechazará
    elif roll < 0.03:
        sid = rec["station_id"]
        rec["station_id"] = rng.choice([sid.lower(), f" {sid} "])
    if rng.random() < 0.05:
        name = rec["station_name"]
        rec["station_name"] = rng.choice([name.lower(), name.upper(), f"  {name} "])
    if rng.random() < 0.03:
        d = rec["district"]
        rec["district"] = rng.choice([d.lower(), d.upper()])

    return rec


def generate(start: datetime, days: int, seed: int) -> list[dict]:
    rng = random.Random(seed)  # semilla -> resultados reproducibles

    clean_readings = []
    for station in STATIONS:
        for h in range(days * 24):
            ts = start + timedelta(hours=h)
            clean_readings.append(build_clean_reading(station, ts, h // 24, rng))

    records = [corrupt(c, rng) for c in clean_readings]

    # Duplicados exactos (mismo registro enviado dos veces)
    n_exact = int(len(records) * 0.04)
    exact_dups = [dict(r) for r in rng.sample(records, n_exact)]

    # Duplicados "casi iguales": misma lectura, otro formato de fecha y otros defectos
    n_near = int(len(clean_readings) * 0.02)
    near_dups = [corrupt(c, rng) for c in rng.sample(clean_readings, n_near)]

    records.extend(exact_dups)
    records.extend(near_dups)
    rng.shuffle(records)

    print(f"Lecturas base: {len(clean_readings)} | duplicados exactos: {n_exact} "
          f"| duplicados casi iguales: {n_near} | total en fichero: {len(records)}")
    return records


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Genera datos sintéticos de movilidad y calidad del aire.")
    p.add_argument("--days", type=int, default=14, help="Días de datos horarios (por defecto 14).")
    p.add_argument("--seed", type=int, default=42, help="Semilla aleatoria (por defecto 42).")
    p.add_argument("--start-date", default="2026-03-01", help="Fecha inicial YYYY-MM-DD.")
    p.add_argument("--output", default=str(RAW_JSON_PATH), help="Ruta del JSON de salida.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    start = datetime.strptime(args.start_date, "%Y-%m-%d")
    records = generate(start, args.days, args.seed)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Fichero generado: {out}")


if __name__ == "__main__":
    main()