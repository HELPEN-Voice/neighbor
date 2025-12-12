-- Migration: Create local_cluster_benchmarks table
-- Run this SQL against your RDS database before using the valuation feature

CREATE TABLE IF NOT EXISTS local_cluster_benchmarks (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(255) UNIQUE NOT NULL,
    coordinates VARCHAR(100),
    state_code VARCHAR(2),
    parcels_analyzed INTEGER,
    valid_residential_samples INTEGER,
    valid_ag_samples INTEGER,
    median_structure_value DECIMAL(15, 2),
    median_land_value_per_acre DECIMAL(15, 2),
    wealth_risk_level VARCHAR(20),
    land_risk_level VARCHAR(20),
    benchmark_json JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Foreign key to neighbor_screen_runs if it exists
    CONSTRAINT fk_run_id FOREIGN KEY (run_id)
        REFERENCES neighbor_screen_runs(run_id)
        ON DELETE CASCADE
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_local_cluster_benchmarks_run_id
    ON local_cluster_benchmarks(run_id);

CREATE INDEX IF NOT EXISTS idx_local_cluster_benchmarks_state
    ON local_cluster_benchmarks(state_code);

-- Comment on table
COMMENT ON TABLE local_cluster_benchmarks IS
    'Local cluster valuation benchmarks calculated from nearest 50 Regrid parcels. See SPEC-002.';
