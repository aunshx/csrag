"""
Regional Summary (Tier 2 Knowledge)

Queries mv_regional_summary for county-level context.
Used when user asks about a region before committing to a full pipeline run.
"""

from config import get_db_connection
from models import RegionalRequest, RegionalResponse


def get_regional_summary(
    county_name: str = None,
    lat: float = None,
    lng: float = None,
) -> list[RegionalResponse]:
    """
    Get regional biomass/cost/fire summary.

    If county_name is provided, returns that county.
    If lat/lng is provided, finds the nearest county by centroid.
    If neither, returns top 10 counties by cluster count.

    Returns:
        List of RegionalResponse objects
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if county_name:
                # Fuzzy match on county name
                cur.execute("""
                    SELECT county_name, n_clusters, total_biomass_bdt,
                           avg_harvest_cost, min_harvest_cost, max_harvest_cost,
                           avg_slope, avg_burn_prob, avg_cf, pct_ground_feasible
                    FROM mv_regional_summary
                    WHERE county_name ILIKE %s
                    ORDER BY n_clusters DESC
                """, (f"%{county_name}%",))

            elif lat is not None and lng is not None:
                # Find nearest county by centroid distance
                cur.execute("""
                    SELECT county_name, n_clusters, total_biomass_bdt,
                           avg_harvest_cost, min_harvest_cost, max_harvest_cost,
                           avg_slope, avg_burn_prob, avg_cf, pct_ground_feasible
                    FROM mv_regional_summary
                    WHERE county_name IS NOT NULL
                    ORDER BY (center_lat - %s)^2 + (center_lng - %s)^2
                    LIMIT 3
                """, (lat, lng))

            else:
                # Top 10 counties by forest cluster count
                cur.execute("""
                    SELECT county_name, n_clusters, total_biomass_bdt,
                           avg_harvest_cost, min_harvest_cost, max_harvest_cost,
                           avg_slope, avg_burn_prob, avg_cf, pct_ground_feasible
                    FROM mv_regional_summary
                    ORDER BY n_clusters DESC
                    LIMIT 10
                """)

            rows = cur.fetchall()
            return [
                RegionalResponse(
                    county_name=r[0],
                    n_clusters=r[1],
                    total_biomass_bdt=float(r[2]) if r[2] else 0.0,
                    avg_harvest_cost=round(float(r[3]), 2) if r[3] else 0.0,
                    min_harvest_cost=round(float(r[4]), 2) if r[4] else 0.0,
                    max_harvest_cost=round(float(r[5]), 2) if r[5] else 0.0,
                    avg_slope=round(float(r[6]), 1) if r[6] else 0.0,
                    avg_burn_prob=round(float(r[7]), 5) if r[7] else 0.0,
                    avg_cf=round(float(r[8]), 2) if r[8] else 0.0,
                    pct_ground_feasible=round(float(r[9]), 1) if r[9] else 0.0,
                )
                for r in rows
            ]
    finally:
        conn.close()


# ── Quick test ────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Test county lookup
    results = get_regional_summary(county_name="Plumas")
    for r in results:
        print(f"{r.county_name}: {r.n_clusters} clusters, "
              f"avg ${r.avg_harvest_cost}/GT, {r.pct_ground_feasible}% ground-feasible")

    # Test lat/lng lookup (Quincy area)
    results = get_regional_summary(lat=39.94, lng=-120.95)
    for r in results:
        print(f"Near Quincy: {r.county_name}, {r.n_clusters} clusters")
