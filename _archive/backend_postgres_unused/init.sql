-- AI估值评分系统 PostgreSQL Schema
-- 在 docker-compose 中自动执行（挂载到 /docker-entrypoint-initdb.d/）

CREATE TABLE IF NOT EXISTS user_overrides (
    id          SERIAL           PRIMARY KEY,
    ticker      VARCHAR(10)      NOT NULL,
    field_name  VARCHAR(60)      NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    updated_at  TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, field_name)
);

CREATE TABLE IF NOT EXISTS market_data_cache (
    ticker      VARCHAR(10)  PRIMARY KEY,
    data        JSONB        NOT NULL,
    fetched_at  TIMESTAMPTZ  NOT NULL
);

CREATE TABLE IF NOT EXISTS score_snapshots (
    id                    SERIAL      PRIMARY KEY,
    ticker                VARCHAR(10) NOT NULL,
    snapshot_date         DATE        NOT NULL DEFAULT CURRENT_DATE,
    final_score           REAL        NOT NULL,
    raw_score             REAL,
    valuation_score       REAL,
    growth_score          REAL,
    quality_score         REAL,
    ai_exposure_score     REAL,
    expectation_gap_score REAL,
    risk_penalty          REAL,
    rating                VARCHAR(30),
    confidence_grade      CHAR(1),
    data_source           VARCHAR(10) NOT NULL DEFAULT 'live',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_score_history
    ON score_snapshots (ticker, snapshot_date DESC);
