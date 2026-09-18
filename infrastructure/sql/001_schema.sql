-- WATTS storage schema.
--
-- Two properties are structural, not conventions:
--   * there is no column anywhere for prompt or completion text;
--   * every energy figure stores its provenance beside it.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TYPE provenance AS ENUM ('measured', 'derived', 'estimated', 'simulated');

CREATE TABLE request_telemetry (
    time             TIMESTAMPTZ      NOT NULL,
    request_id       TEXT             NOT NULL,
    tenant           TEXT             NOT NULL,
    workload         TEXT             NOT NULL,
    model            TEXT             NOT NULL,
    provider         TEXT             NOT NULL,
    task_class       TEXT             NOT NULL DEFAULT 'unspecified',
    input_tokens     INTEGER          NOT NULL CHECK (input_tokens >= 0),
    output_tokens    INTEGER          NOT NULL CHECK (output_tokens >= 0),
    latency_ms       DOUBLE PRECISION NOT NULL CHECK (latency_ms >= 0),
    batch_size       INTEGER          NOT NULL DEFAULT 1,
    context_window   INTEGER          NOT NULL DEFAULT 0,
    gpu_id           TEXT             NOT NULL,
    success          BOOLEAN          NOT NULL DEFAULT TRUE,
    cache_hit        BOOLEAN          NOT NULL DEFAULT FALSE,
    agent_step       INTEGER          NOT NULL DEFAULT 0,
    prompt_hash      TEXT,                    -- salted, truncated SHA-256. Never the text.
    prefix_hash      TEXT,
    session_hash     TEXT,
    energy_wh        DOUBLE PRECISION,
    energy_source    provenance,
    security_policy  TEXT             NOT NULL DEFAULT 'default',
    PRIMARY KEY (time, request_id)
);
SELECT create_hypertable('request_telemetry', 'time', chunk_time_interval => INTERVAL '1 day');
CREATE INDEX ON request_telemetry (tenant, time DESC);
CREATE INDEX ON request_telemetry (model, time DESC);
CREATE INDEX ON request_telemetry (prompt_hash, time DESC) WHERE prompt_hash IS NOT NULL;

CREATE TABLE gpu_telemetry (
    time              TIMESTAMPTZ      NOT NULL,
    device_id         TEXT             NOT NULL,
    node              TEXT             NOT NULL,
    utilization       REAL             NOT NULL,
    memory_used_gb    REAL             NOT NULL,
    power_w           REAL             NOT NULL,
    temperature_c     REAL             NOT NULL,
    sm_clock_mhz      REAL             NOT NULL,
    memory_bw_gbps    REAL,
    source            provenance       NOT NULL DEFAULT 'measured',
    PRIMARY KEY (time, device_id)
);
SELECT create_hypertable('gpu_telemetry', 'time', chunk_time_interval => INTERVAL '1 day');

CREATE TABLE facility_telemetry (
    time              TIMESTAMPTZ      NOT NULL PRIMARY KEY,
    it_power_w        DOUBLE PRECISION NOT NULL,
    cooling_power_w   DOUBLE PRECISION,
    facility_power_w  DOUBLE PRECISION,
    inlet_temp_c      REAL,
    pue               DOUBLE PRECISION,
    pue_source        provenance       NOT NULL DEFAULT 'estimated',
    carbon_g_per_kwh  REAL,
    carbon_source     TEXT
);
SELECT create_hypertable('facility_telemetry', 'time', chunk_time_interval => INTERVAL '1 day');

-- Per-minute rollups: raw records are short-lived, aggregates are kept for a year.
CREATE MATERIALIZED VIEW energy_per_minute
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 minute', time) AS bucket,
       tenant, model, task_class,
       count(*)                                      AS requests,
       count(*) FILTER (WHERE success)               AS successful,
       sum(input_tokens + output_tokens)             AS tokens,
       sum(energy_wh)                                AS energy_wh,
       sum(energy_wh) / NULLIF(sum(input_tokens + output_tokens) / 1000.0, 0) AS wh_per_1k_tokens,
       approx_percentile(0.95, percentile_agg(latency_ms))                    AS p95_latency_ms
FROM request_telemetry
GROUP BY bucket, tenant, model, task_class;

SELECT add_retention_policy('request_telemetry', INTERVAL '30 days');
SELECT add_retention_policy('gpu_telemetry',     INTERVAL '30 days');

CREATE TABLE optimization_audit (
    index            BIGINT           PRIMARY KEY,
    time             TIMESTAMPTZ      NOT NULL,
    actor            TEXT             NOT NULL,
    action           TEXT             NOT NULL,
    recommendation   TEXT             NOT NULL,
    policy_decision  JSONB            NOT NULL,
    change           JSONB            NOT NULL,
    outcome          TEXT             NOT NULL,
    prev_hash        TEXT             NOT NULL,
    entry_hash       TEXT             NOT NULL UNIQUE
);
-- Append-only: the chain is the record. Revoke UPDATE and DELETE from the application role.
REVOKE UPDATE, DELETE ON optimization_audit FROM PUBLIC;
