-- Modelo analítico: una dimensión (estaciones) y una tabla de hechos (lecturas).

CREATE TABLE IF NOT EXISTS dim_station (
    station_id    VARCHAR(20)  PRIMARY KEY,
    station_name  VARCHAR(100) NOT NULL,
    district      VARCHAR(100),
    updated_at    TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fact_reading (
    station_id     VARCHAR(20)   NOT NULL REFERENCES dim_station (station_id),
    measured_at    TIMESTAMP     NOT NULL,   -- hora local, sin zona horaria (supuesto documentado)
    pm10           NUMERIC(6,2)  CHECK (pm10 >= 0),
    no2            NUMERIC(6,2)  CHECK (no2 >= 0),
    temperature_c  NUMERIC(5,2),
    traffic_count  INTEGER       CHECK (traffic_count >= 0),
    avg_speed_kmh  NUMERIC(5,2)  CHECK (avg_speed_kmh >= 0),
    loaded_at      TIMESTAMP     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (station_id, measured_at)
);

CREATE INDEX IF NOT EXISTS idx_fact_reading_measured_at ON fact_reading (measured_at);