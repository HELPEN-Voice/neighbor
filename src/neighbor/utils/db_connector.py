import os
import psycopg2
import json
from datetime import datetime
from typing import Dict, Any, List


class NeighborDBConnector:
    """Handles database connections and operations for the neighbor screening system."""

    def __init__(self):
        """Initializes the database connection using environment variables."""
        try:
            self.conn = psycopg2.connect(
                dbname=os.environ.get("DB_NAME"),
                user=os.environ.get("DB_USER"),
                password=os.environ.get("DB_PASSWORD"),
                host=os.environ.get("DB_HOST"),
                port=os.environ.get("DB_PORT", "5432"),
            )
            print("✅ Successfully connected to the database.")
        except Exception as e:
            print(f"❌ Database connection failed: {e}")
            self.conn = None

    @staticmethod
    def _to_null_if_empty(value):
        """Convert empty strings and empty arrays to None for NULL in database."""
        if value == "" or value is None:
            return None
        if isinstance(value, list) and len(value) == 0:
            return None
        return value

    def save_neighbor_stakeholders(
        self,
        run_id: str,
        neighbors: List[Dict[str, Any]],
        location_context: str = None,
        location: str = None,
        pin: str = None,
        county: str = None,
        state: str = None,
        city: str = None,
        county_path: str = None,
        adjacent_pins: set = None,
    ):
        """Batch inserts neighbor stakeholders into the database and saves run metadata."""
        if not self.conn or not neighbors:
            print("⚠️ No database connection or no neighbors to save")
            return

        # First, save the run metadata to neighbor_screen_runs table
        run_sql = """
            INSERT INTO neighbor_screen_runs (
                run_id, location, county, state, city, pin, coordinates, county_path, adjacent_pins
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                location = EXCLUDED.location,
                county = EXCLUDED.county,
                state = EXCLUDED.state,
                city = EXCLUDED.city,
                pin = EXCLUDED.pin,
                coordinates = EXCLUDED.coordinates,
                county_path = EXCLUDED.county_path,
                adjacent_pins = EXCLUDED.adjacent_pins,
                created_at = CURRENT_TIMESTAMP;
        """

        # Convert adjacent_pins set to JSON array
        adjacent_pins_json = json.dumps(list(adjacent_pins)) if adjacent_pins else None

        with self.conn.cursor() as cur:
            cur.execute(
                run_sql,
                (
                    run_id,
                    f"{city}, {state}" if city and state else None,  # location field
                    county,
                    state,
                    city,
                    pin,
                    location,  # coordinates field
                    county_path,
                    adjacent_pins_json,  # adjacent_pins as JSONB
                ),
            )
            self.conn.commit()
            print(f"💾 Saved run metadata for run_id {run_id}")

        # Delete existing records for this run_id to avoid duplicates
        with self.conn.cursor() as cur:
            delete_sql = "DELETE FROM stakeholders WHERE run_id = %s AND source_type = 'neighbor';"
            cur.execute(delete_sql, (run_id,))
            deleted_count = cur.rowcount
            if deleted_count > 0:
                print(
                    f"🗑️ Removed {deleted_count} old neighbor stakeholder records for run_id {run_id}"
                )

        sql = """
            INSERT INTO stakeholders (
                run_id,
                source_id,
                source_type,
                data_source,
                name,
                role,
                affiliation,
                stance,
                notes,
                entity_type,
                entity_category,
                property_pins,
                is_adjacent_parcel,
                community_influence_level,
                influence_justification,
                engagement_motivations,
                data_confidence,
                recommended_approach
            )
            VALUES %s;
        """

        with self.conn.cursor() as cur:
            from psycopg2.extras import execute_values

            data_to_insert = []
            for n in neighbors:
                # Extract engagement motivations and engage text
                approach_recommendations = n.get("approach_recommendations") or {}
                motivations = approach_recommendations.get("motivations", [])
                engage_text = approach_recommendations.get("engage", "")

                # Convert motivations list to JSON
                motivations_json = json.dumps(motivations) if motivations else None

                # Convert pins list to JSON
                pins = n.get("pins", [])
                pins_json = json.dumps(pins) if pins else None

                # Map owns_adjacent_parcel to is_adjacent_parcel boolean
                is_adjacent = n.get("owns_adjacent_parcel", "No") == "Yes"

                data_to_insert.append(
                    (
                        run_id,
                        self._to_null_if_empty(n.get("neighbor_id")),  # source_id
                        "neighbor",  # source_type
                        "neighbor_analysis",  # data_source
                        self._to_null_if_empty(n.get("name")),
                        self._to_null_if_empty(
                            n.get("entity_type")
                        ),  # role field = entity_type
                        None,  # affiliation (not used for neighbors)
                        self._to_null_if_empty(n.get("noted_stance")),  # stance
                        self._to_null_if_empty(n.get("claims")),  # notes = claims
                        self._to_null_if_empty(n.get("entity_type")),  # entity_type
                        self._to_null_if_empty(
                            n.get("entity_category")
                        ),  # entity_category
                        pins_json,  # property_pins as JSONB
                        is_adjacent,  # is_adjacent_parcel as boolean
                        self._to_null_if_empty(
                            n.get("community_influence")
                        ),  # community_influence_level
                        self._to_null_if_empty(n.get("influence_justification")),
                        motivations_json,  # engagement_motivations as JSONB
                        self._to_null_if_empty(n.get("confidence")),  # data_confidence
                        self._to_null_if_empty(
                            engage_text
                        ),  # recommended_approach = engage text
                    )
                )

            execute_values(cur, sql, data_to_insert, page_size=100)
            self.conn.commit()
            print(
                f"💾 Saved {len(neighbors)} neighbor stakeholders to the database (run_id: {run_id})"
            )

    def close(self):
        """Closes the database connection."""
        if self.conn:
            self.conn.close()
            print("🔒 Database connection closed.")
