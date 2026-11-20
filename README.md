# urban-mobility-etl

![tests](https://github.com/TU_USUARIO/urban-mobility-etl/actions/workflows/tests.yml/badge.svg)

**English summary:** Personal learning project that builds an end-to-end ETL pipeline
on **synthetic** urban air-quality and traffic data. Raw JSON (with intentionally
injected errors: duplicates, nulls, mixed date formats, outliers) is ingested into
MongoDB (raw layer), cleaned and validated with Python/pandas, and loaded
idempotently into PostgreSQL using `INSERT ... ON CONFLICT DO UPDATE`.
Analytical SQL (CTEs, window functions) runs on top. Local setup with Docker Compose;
cleaning logic covered by pytest.
**Quick start:** see "Cómo ejecutarlo" below.

Pipeline ETL de práctica para datos sintéticos de **calidad del aire y tráfico urbano**:
ingesta en MongoDB (zona raw), limpieza y validación con pandas, carga idempotente en
PostgreSQL (UPSERT) y consultas analíticas en SQL.

> Proyecto personal con **datos sintéticos** generados por el propio repositorio. Su objetivo es
> demostrar fundamentos de ingeniería de datos, no representar una solución en producción.

## Arquitectura

```mermaid
flowchart LR
    A[generate_sample_data.py] -->|JSON con errores| B[(MongoDB<br/>raw / landing)]
    B -->|extract| C[transform_load_postgres.py<br/>pandas: limpiar, validar, deduplicar]
    C -->|UPSERT| D[(PostgreSQL<br/>dim_station + fact_reading)]
    C -->|filas descartadas + motivo| E[rejected_readings.csv]
    D --> F[analytics_queries.sql]
```

## Stack
Python 3.10+, pandas, pymongo, psycopg2, MongoDB 7, PostgreSQL 16, Docker Compose, pytest.

## Estructura
```
src/        generate_sample_data.py · extract_load_mongo.py · transform_load_postgres.py · config.py
sql/        schema.sql · analytics_queries.sql
tests/      test_transform.py
```

## Cómo ejecutarlo

```bash
# 1. Entorno
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# 2. Bases de datos locales
docker compose up -d        # espera unos segundos a que arranquen

# 3. Pipeline
python -m src.generate_sample_data      # crea data/raw/air_traffic_readings.json
python -m src.extract_load_mongo        # JSON -> MongoDB (raw)
python -m src.transform_load_postgres   # MongoDB -> limpieza -> PostgreSQL

# 4. Consultas analíticas
docker compose exec -T postgres psql -U postgres -d urban_mobility < sql/analytics_queries.sql
```
> **Nota:** el contenedor de PostgreSQL se expone en el puerto **5433** (no en el 5432)
> para no chocar con un PostgreSQL instalado localmente. El `.env.example` ya lo refleja.

## Cómo probarlo

**Tests unitarios de limpieza**
```bash
pytest
```

**Idempotencia** (ejecutar dos veces no duplica datos)
```bash
python -m src.extract_load_mongo        # 2ª vez: "Nuevos: 0"
python -m src.transform_load_postgres   # 2ª vez: inserted=0, updated=0, unchanged=N
docker compose exec mongo mongosh urban_mobility --eval "db.raw_readings.countDocuments()"
docker compose exec postgres psql -U postgres -d urban_mobility -c "SELECT COUNT(*) FROM fact_reading;"
```

## Resultados de ejemplo

Ejecución real con los datos sintéticos por defecto (`--days 14 --seed 42`).

**Resumen de limpieza** (2848 registros crudos, incluidos duplicados y errores):
```text
{'rows_raw': 2848, 'values_nulled_out_of_range': 163, 'rows_rejected': 37,
 'duplicates_removed': 158, 'rows_clean': 2653}
```
Motivos de rechazo: 22 por fecha inválida y 15 por `station_id` ausente
(guardadas en `data/processed/rejected_readings.csv`).

**Idempotencia.** Segunda ejecución de `transform_load_postgres` sin cambiar los datos
(no inserta ni modifica nada):
```text
 dim_station  -> {'inserted': 0, 'updated': 0, 'unchanged': 8}
  fact_reading -> {'inserted': 0, 'updated': 0, 'unchanged': 2653}
```

**Ranking de estaciones por NO2 medio:**
```text
    station_name    |  district   | avg_no2 | rank_city
--------------------+-------------+---------+-----------
 Puerta del Sol     | Centro      |   45.62 |         1
 Cuatro Caminos     | Tetuán      |   43.37 |         2
 Plaza Mayor        | Centro      |   41.10 |         3
 Nuevos Ministerios | Chamartín   |   40.04 |         4
 Calle Serrano      | Salamanca   |   38.97 |         5
 Avenida Oporto     | Carabanchel |   37.31 |         6
 Paseo Delicias     | Arganzuela  |   34.42 |         7
 Parque Retiro Sur  | Salamanca   |   21.47 |         8
```

**Hora punta vs. resto del día:**
```text
    period     | n_readings | avg_traffic | avg_no2 | corr_traffic_no2
---------------+------------+-------------+---------+------------------
 Hora punta    |        663 |        1385 |   48.93 |            0.792
 Resto del dia |       1990 |         685 |   34.02 |            0.859
```

## Reglas de calidad de datos

| Problema | Tratamiento |
|---|---|
| Fechas en 3 formatos | Parseo estricto por formato; si ninguno encaja -> fila rechazada |
| Fecha imposible (31/02) | Fila rechazada (`fecha inválida`) |
| Nulos disfrazados (`""`, `N/A`, `null`) | Convertidos a NULL |
| Decimales con coma (`"23,4"`) | Convertidos a número |
| Valores negativos / atípicos | Se anula el valor (NULL), se conserva la fila |
| Sin `station_id` o sin ninguna medida | Fila rechazada, guardada en `data/processed/rejected_readings.csv` |
| Duplicados (exactos y con otro formato) | Dedup por `(station_id, measured_at)`; se conserva la fila más completa |
| Nombres de estación inconsistentes | Gana el valor más frecuente por estación |

## Decisiones de diseño
- **MongoDB como raw**: guarda el dato tal cual llega, con trazabilidad (`_meta`), y permite reprocesar.
- **Ingesta idempotente**: `_id` determinista (huella del fichero + nº de fila) y `upsert`.
- **UPSERT en Postgres** con `ON CONFLICT DO UPDATE` y actualización solo si hay cambios.
- **Transacción única** en la carga: o entra todo o nada.
- **Modelo en estrella mínimo**: `dim_station` + `fact_reading`.
- Las fechas se asumen en **hora local sin zona horaria** (supuesto documentado).

## Limitaciones y siguientes pasos
- Datos sintéticos y volumen pequeño (miles de filas), pensados para aprender y demostrar.
- Ejecución manual: el siguiente paso sería orquestarlo con Airflow o similar.
- Carga completa en memoria con pandas: para volúmenes grandes habría que procesar por bloques.
- Posibles mejoras: tests de integración, logging a fichero, validación con Great Expectations o pandera, CI con GitHub Actions.