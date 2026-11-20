-- =====================================================================
-- Consultas analíticas sobre fact_reading y dim_station (PostgreSQL)
-- =====================================================================

-- 1) Media diaria por estación (agregaciones + GROUP BY)
SELECT
    s.district,
    s.station_name,
    r.measured_at::date                AS day,
    ROUND(AVG(r.pm10), 2)              AS avg_pm10,
    ROUND(AVG(r.no2), 2)               AS avg_no2,
    ROUND(AVG(r.traffic_count), 0)     AS avg_traffic,
    COUNT(*)                           AS n_readings
FROM fact_reading r
JOIN dim_station s USING (station_id)
GROUP BY s.district, s.station_name, r.measured_at::date
ORDER BY day, s.station_name;


-- 2) Distribución de niveles de PM10 por distrito (CASE + ventana sobre agregado)
--    Umbrales didácticos, no oficiales.
WITH classified AS (
    SELECT
        s.district,
        CASE
            WHEN r.pm10 IS NULL THEN 'Sin dato'
            WHEN r.pm10 <= 25   THEN 'Buena'
            WHEN r.pm10 <= 50   THEN 'Aceptable'
            WHEN r.pm10 <= 100  THEN 'Mala'
            ELSE 'Muy mala'
        END AS pm10_level
    FROM fact_reading r
    JOIN dim_station s USING (station_id)
)
SELECT
    district,
    pm10_level,
    COUNT(*) AS n_readings,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY district), 1) AS pct_in_district
FROM classified
GROUP BY district, pm10_level
ORDER BY district, n_readings DESC;


-- 3) Ranking de estaciones por NO2 medio (RANK, global y por distrito)
WITH station_avg AS (
    SELECT
        s.station_name,
        s.district,
        AVG(r.no2) AS avg_no2
    FROM fact_reading r
    JOIN dim_station s USING (station_id)
    GROUP BY s.station_name, s.district
)
SELECT
    station_name,
    district,
    ROUND(avg_no2, 2) AS avg_no2,
    RANK() OVER (ORDER BY avg_no2 DESC NULLS LAST)                      AS rank_city,
    RANK() OVER (PARTITION BY district ORDER BY avg_no2 DESC NULLS LAST) AS rank_in_district
FROM station_avg
ORDER BY rank_city;


-- 4) Media móvil de 6 lecturas y variación vs. lectura anterior (AVG OVER + LAG + CASE)
WITH hourly AS (
    SELECT
        r.station_id,
        r.measured_at,
        r.pm10,
        ROUND(AVG(r.pm10) OVER (
            PARTITION BY r.station_id
            ORDER BY r.measured_at
            ROWS BETWEEN 5 PRECEDING AND CURRENT ROW
        ), 2) AS pm10_moving_avg_6h,
        r.pm10 - LAG(r.pm10) OVER (
            PARTITION BY r.station_id
            ORDER BY r.measured_at
        ) AS pm10_delta_prev
    FROM fact_reading r
    WHERE r.station_id = 'ST-001'
)
SELECT
    station_id, measured_at, pm10, pm10_moving_avg_6h, pm10_delta_prev,
    CASE
        WHEN pm10_delta_prev IS NULL THEN 'Sin referencia'
        WHEN pm10_delta_prev >= 20   THEN 'Subida brusca'
        WHEN pm10_delta_prev <= -20  THEN 'Bajada brusca'
        ELSE 'Estable'
    END AS trend
FROM hourly
ORDER BY measured_at
LIMIT 72;


-- 5) Momento de máxima contaminación por estación (ROW_NUMBER)
WITH ranked AS (
    SELECT
        r.station_id,
        r.measured_at,
        r.pm10,
        ROW_NUMBER() OVER (
            PARTITION BY r.station_id
            ORDER BY r.pm10 DESC, r.measured_at
        ) AS rn
    FROM fact_reading r
    WHERE r.pm10 IS NOT NULL
)
SELECT s.station_name, s.district, ranked.measured_at AS peak_at, ranked.pm10 AS peak_pm10
FROM ranked
JOIN dim_station s USING (station_id)
WHERE ranked.rn = 1
ORDER BY ranked.pm10 DESC;


-- 6) Hora punta vs. resto del día + correlación tráfico/NO2 (CASE + CORR)
SELECT
    CASE
        WHEN EXTRACT(HOUR FROM measured_at) BETWEEN 7 AND 9
          OR EXTRACT(HOUR FROM measured_at) BETWEEN 18 AND 20 THEN 'Hora punta'
        ELSE 'Resto del día'
    END                                          AS period,
    COUNT(*)                                     AS n_readings,
    ROUND(AVG(traffic_count), 0)                 AS avg_traffic,
    ROUND(AVG(no2), 2)                           AS avg_no2,
    ROUND(AVG(avg_speed_kmh), 1)                 AS avg_speed_kmh,
    ROUND(CORR(traffic_count, no2)::numeric, 3)  AS corr_traffic_no2
FROM fact_reading
GROUP BY 1
ORDER BY avg_traffic DESC;


-- 7) Informe de calidad de datos: nulos por columna en la tabla final
SELECT
    COUNT(*)                                                          AS total_rows,
    COUNT(*) - COUNT(pm10)                                            AS null_pm10,
    ROUND(100.0 * (COUNT(*) - COUNT(pm10)) / COUNT(*), 2)             AS pct_null_pm10,
    COUNT(*) - COUNT(no2)                                             AS null_no2,
    ROUND(100.0 * (COUNT(*) - COUNT(no2)) / COUNT(*), 2)              AS pct_null_no2,
    COUNT(*) - COUNT(temperature_c)                                   AS null_temperature,
    COUNT(*) - COUNT(traffic_count)                                   AS null_traffic,
    COUNT(*) - COUNT(avg_speed_kmh)                                   AS null_speed
FROM fact_reading;