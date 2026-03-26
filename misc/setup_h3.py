"""
H3 Hexagonal Spatial Index Setup

Creates a separate cluster_h3_index lookup table (does NOT alter mv_cluster_supply),
then builds pre-aggregated materialized views at resolutions 4, 5, 6.

Run once from csrag-api/:
    pip install h3 --break-system-packages
    python setup_h3.py

Or if script is in misc/:
    python misc/setup_h3.py
"""

import h3
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from config import get_db_connection


def step1_create_h3_lookup():
    """
    Create a separate lookup table: cluster_no -> h3 indexes at 3 resolutions.
    Populated from distinct cluster coordinates in mv_cluster_supply.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                DROP TABLE IF EXISTS cluster_h3_index;
                CREATE TABLE cluster_h3_index (
                    cluster_no integer PRIMARY KEY,
                    center_lat float,
                    center_lng float,
                    h3_r4 text,
                    h3_r5 text,
                    h3_r6 text
                );
            """)
            conn.commit()
            print("[OK] cluster_h3_index table created")

            print("[INFO] Fetching distinct clusters from mv_cluster_supply...")
            cur.execute("""
                SELECT DISTINCT cluster_no, center_lat, center_lng
                FROM mv_cluster_supply
            """)
            rows = cur.fetchall()
            total = len(rows)
            print(f"[INFO] {total:,} distinct clusters to index")

            batch = []
            errors = 0
            t0 = time.time()

            for i, (cno, lat, lng) in enumerate(rows):
                try:
                    r4 = h3.latlng_to_cell(float(lat), float(lng), 4)
                    r5 = h3.latlng_to_cell(float(lat), float(lng), 5)
                    r6 = h3.latlng_to_cell(float(lat), float(lng), 6)
                    batch.append((cno, float(lat), float(lng), r4, r5, r6))
                except Exception:
                    errors += 1
                    continue

                if len(batch) >= 10000:
                    cur.executemany("""
                        INSERT INTO cluster_h3_index
                        (cluster_no, center_lat, center_lng, h3_r4, h3_r5, h3_r6)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, batch)
                    conn.commit()
                    elapsed = time.time() - t0
                    rate = (i + 1) / elapsed if elapsed > 0 else 0
                    print(f"  [{i+1:,}/{total:,}] {rate:,.0f} rows/sec")
                    batch = []

            if batch:
                cur.executemany("""
                    INSERT INTO cluster_h3_index
                    (cluster_no, center_lat, center_lng, h3_r4, h3_r5, h3_r6)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, batch)
                conn.commit()

            elapsed = time.time() - t0

            print("[INFO] Creating indexes on cluster_h3_index...")
            cur.execute("""
                CREATE INDEX idx_h3_lookup_r4 ON cluster_h3_index(h3_r4);
                CREATE INDEX idx_h3_lookup_r5 ON cluster_h3_index(h3_r5);
                CREATE INDEX idx_h3_lookup_r6 ON cluster_h3_index(h3_r6);
            """)
            conn.commit()

            cur.execute("SELECT COUNT(*) FROM cluster_h3_index")
            count = cur.fetchone()[0]
            print(f"[OK] cluster_h3_index: {count:,} rows, {errors} errors, {elapsed:.0f}s")

    finally:
        conn.close()


def step2_create_hex_views():
    """
    Create materialized views at each H3 resolution by JOINing
    mv_cluster_supply with cluster_h3_index.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:

            # R4: coarse (~22km)
            print("[INFO] Creating mv_hex_r4...")
            t0 = time.time()
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_hex_r4 CASCADE;")
            cur.execute("""
                CREATE MATERIALIZED VIEW mv_hex_r4 AS
                SELECT
                    h.h3_r4 AS hex_id,
                    4 AS resolution,
                    AVG(m.center_lat) AS centroid_lat,
                    AVG(m.center_lng) AS centroid_lng,
                    COUNT(DISTINCT m.cluster_no) AS n_clusters,
                    SUM(m.total_biomass_bdt) AS total_biomass,
                    AVG(m.harvest_cost) AS avg_cost,
                    MIN(m.harvest_cost) AS min_cost,
                    MAX(m.harvest_cost) AS max_cost,
                    AVG(m.burn_probability) AS avg_fire,
                    MAX(m.burn_probability) AS max_fire,
                    AVG(m.slope) AS avg_slope,
                    COUNT(*) FILTER (WHERE m.slope < 40) * 100.0
                        / NULLIF(COUNT(*), 0) AS pct_ground,
                    AVG(m.cf_estimate) AS avg_cf,
                    MODE() WITHIN GROUP (ORDER BY m.county_name) AS county_name
                FROM mv_cluster_supply m
                JOIN cluster_h3_index h ON h.cluster_no = m.cluster_no
                WHERE m.treatmentid = 1
                GROUP BY h.h3_r4;
            """)
            cur.execute("""
                CREATE INDEX idx_hex_r4_biomass ON mv_hex_r4(total_biomass DESC);
                CREATE INDEX idx_hex_r4_cost ON mv_hex_r4(avg_cost ASC);
                CREATE INDEX idx_hex_r4_fire ON mv_hex_r4(avg_fire DESC);
                CREATE UNIQUE INDEX idx_hex_r4_id ON mv_hex_r4(hex_id);
            """)
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM mv_hex_r4")
            count = cur.fetchone()[0]
            print(f"[OK] mv_hex_r4: {count:,} hexes ({time.time()-t0:.1f}s)")

            # R5: medium (~8km)
            print("[INFO] Creating mv_hex_r5...")
            t0 = time.time()
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_hex_r5 CASCADE;")
            cur.execute("""
                CREATE MATERIALIZED VIEW mv_hex_r5 AS
                SELECT
                    h.h3_r5 AS hex_id,
                    5 AS resolution,
                    h.h3_r4 AS parent_hex,
                    AVG(m.center_lat) AS centroid_lat,
                    AVG(m.center_lng) AS centroid_lng,
                    COUNT(DISTINCT m.cluster_no) AS n_clusters,
                    SUM(m.total_biomass_bdt) AS total_biomass,
                    AVG(m.harvest_cost) AS avg_cost,
                    MIN(m.harvest_cost) AS min_cost,
                    MAX(m.harvest_cost) AS max_cost,
                    AVG(m.burn_probability) AS avg_fire,
                    MAX(m.burn_probability) AS max_fire,
                    AVG(m.slope) AS avg_slope,
                    COUNT(*) FILTER (WHERE m.slope < 40) * 100.0
                        / NULLIF(COUNT(*), 0) AS pct_ground,
                    AVG(m.cf_estimate) AS avg_cf,
                    MODE() WITHIN GROUP (ORDER BY m.county_name) AS county_name
                FROM mv_cluster_supply m
                JOIN cluster_h3_index h ON h.cluster_no = m.cluster_no
                WHERE m.treatmentid = 1
                GROUP BY h.h3_r5, h.h3_r4;
            """)
            cur.execute("""
                CREATE INDEX idx_hex_r5_parent ON mv_hex_r5(parent_hex);
                CREATE INDEX idx_hex_r5_biomass ON mv_hex_r5(total_biomass DESC);
                CREATE UNIQUE INDEX idx_hex_r5_id ON mv_hex_r5(hex_id);
            """)
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM mv_hex_r5")
            count = cur.fetchone()[0]
            print(f"[OK] mv_hex_r5: {count:,} hexes ({time.time()-t0:.1f}s)")

            # R6: fine (~3km)
            print("[INFO] Creating mv_hex_r6...")
            t0 = time.time()
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_hex_r6 CASCADE;")
            cur.execute("""
                CREATE MATERIALIZED VIEW mv_hex_r6 AS
                SELECT
                    h.h3_r6 AS hex_id,
                    6 AS resolution,
                    h.h3_r5 AS parent_hex,
                    AVG(m.center_lat) AS centroid_lat,
                    AVG(m.center_lng) AS centroid_lng,
                    COUNT(DISTINCT m.cluster_no) AS n_clusters,
                    SUM(m.total_biomass_bdt) AS total_biomass,
                    AVG(m.harvest_cost) AS avg_cost,
                    MIN(m.harvest_cost) AS min_cost,
                    MAX(m.harvest_cost) AS max_cost,
                    AVG(m.burn_probability) AS avg_fire,
                    MAX(m.burn_probability) AS max_fire,
                    AVG(m.slope) AS avg_slope,
                    COUNT(*) FILTER (WHERE m.slope < 40) * 100.0
                        / NULLIF(COUNT(*), 0) AS pct_ground,
                    AVG(m.cf_estimate) AS avg_cf,
                    MODE() WITHIN GROUP (ORDER BY m.county_name) AS county_name
                FROM mv_cluster_supply m
                JOIN cluster_h3_index h ON h.cluster_no = m.cluster_no
                WHERE m.treatmentid = 1
                GROUP BY h.h3_r6, h.h3_r5;
            """)
            cur.execute("""
                CREATE INDEX idx_hex_r6_parent ON mv_hex_r6(parent_hex);
                CREATE INDEX idx_hex_r6_biomass ON mv_hex_r6(total_biomass DESC);
                CREATE UNIQUE INDEX idx_hex_r6_id ON mv_hex_r6(hex_id);
            """)
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM mv_hex_r6")
            count = cur.fetchone()[0]
            print(f"[OK] mv_hex_r6: {count:,} hexes ({time.time()-t0:.1f}s)")

    finally:
        conn.close()


def step3_verify():
    """Quick verification of the hex hierarchy and query speed."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r4.hex_id, r4.county_name,
                       round(r4.total_biomass::numeric, 0) AS biomass,
                       COUNT(r5.hex_id) AS n_children
                FROM mv_hex_r4 r4
                LEFT JOIN mv_hex_r5 r5 ON r5.parent_hex = r4.hex_id
                GROUP BY r4.hex_id, r4.county_name, r4.total_biomass
                ORDER BY r4.total_biomass DESC
                LIMIT 5
            """)
            print("\n=== Top 5 R4 hexes by biomass ===")
            for row in cur.fetchall():
                print(f"  {row[0]}: {row[1]}, "
                      f"{row[2]:,} BDT, "
                      f"{row[3]} R5 children")

            # Speed: statewide top-3
            t0 = time.time()
            cur.execute("""
                SELECT hex_id, centroid_lat, centroid_lng,
                       total_biomass, avg_cost, county_name
                FROM mv_hex_r4
                ORDER BY total_biomass DESC
                LIMIT 3
            """)
            rows = cur.fetchall()
            t1 = time.time()
            print(f"\n=== Statewide top 3 ({(t1-t0)*1000:.2f}ms) ===")
            for r in rows:
                print(f"  {r[5]}: ({float(r[1]):.2f}, {float(r[2]):.2f}), "
                      f"{float(r[3]):,.0f} BDT, ${float(r[4]):.1f}/GT")

            # Speed: drill-down
            parent = rows[0][0]
            t0 = time.time()
            cur.execute("""
                SELECT hex_id, centroid_lat, centroid_lng,
                       total_biomass, avg_cost, county_name
                FROM mv_hex_r5
                WHERE parent_hex = %s
                ORDER BY total_biomass DESC
            """, (parent,))
            children = cur.fetchall()
            t1 = time.time()
            print(f"\n=== R5 drill-down ({(t1-t0)*1000:.2f}ms, {len(children)} children) ===")
            for r in children:
                print(f"  {r[5]}: {float(r[3]):,.0f} BDT, ${float(r[4]):.1f}/GT")

    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("H3 Hexagonal Spatial Index Setup")
    print("=" * 60)

    print("\n--- Step 1: Create H3 lookup table ---")
    step1_create_h3_lookup()

    print("\n--- Step 2: Create hex materialized views ---")
    step2_create_hex_views()

    print("\n--- Step 3: Verify ---")
    step3_verify()

    print("\n" + "=" * 60)
    print("Done! H3 spatial index ready.")
    print("=" * 60)