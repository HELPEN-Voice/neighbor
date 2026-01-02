-- Migration: Create regrid_values table
-- Run this SQL against your RDS database before using the valuation feature

CREATE TABLE IF NOT EXISTS regrid_values (
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_regrid_values_run_id
    ON regrid_values(run_id);

CREATE INDEX IF NOT EXISTS idx_regrid_values_state
    ON regrid_values(state_code);

-- Comment on table
COMMENT ON TABLE regrid_values IS
    'Local cluster valuation benchmarks calculated from nearest 50 Regrid parcels. See SPEC-002.';
