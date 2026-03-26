"""
Radius Estimation (§4 of CS-RAG pipeline)

Binary search for the minimum procurement radius that provides
enough biomass to meet facility demand (with buffer).

Uses mv_cluster_supply with spatial queries.
"""

from config import (
    get_db_connection, BIOMASS_BUFFER,
    RADIUS_MIN_KM, RADIUS_MAX_KM, RADIUS_STEP_KM,
)
from models import RadiusResponse


def _biomass_within_radius(
    cur, lat: float, lng: float, radius_km: float, treatmentid: int
) -> tuple[float, int]:
    """Query total biomass and cluster count within a given radius."""
    # Use native geometry with degree approximation (GIST index friendly)
    # At ~40°N: 1° lat ≈ 111km, 1° lng ≈ 85km
    # Use conservative estimate (111km) so we slightly over-fetch
    radius_deg = radius_km / 111.0
    cur.execute("""
        SELECT COALESCE(SUM(total_biomass_bdt), 0), COUNT(*)
        FROM mv_cluster_supply
        WHERE treatmentid = %s
          AND ST_DWithin(
              geom,
              ST_SetSRID(ST_MakePoint(%s, %s), 4326),
              %s
          )
    """, (treatmentid, lng, lat, radius_deg))
    biomass, count = cur.fetchone()
    return float(biomass), int(count)


def estimate_radius(
    lat: float,
    lng: float,
    demand_bdt: float,
    treatmentid: int = 1,
    buffer_multiplier: float = BIOMASS_BUFFER,
) -> RadiusResponse:
    """
    Find the minimum radius that provides demand × buffer biomass.

    Uses a coarse linear search (stepping by RADIUS_STEP_KM) followed by
    a fine binary search within the last step to find the tightest radius.

    Args:
        lat, lng: Facility coordinates
        demand_bdt: Annual biomass demand in BDT
        treatmentid: Treatment to query (affects biomass per cluster)
        buffer_multiplier: How much extra biomass to require (1.5 = 50% surplus)

    Returns:
        RadiusResponse with estimated radius, available biomass, and cluster count
    """
    target = demand_bdt * buffer_multiplier
    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            # Phase 1: coarse search (step by RADIUS_STEP_KM)
            prev_radius = RADIUS_MIN_KM
            for r in range(RADIUS_MIN_KM, RADIUS_MAX_KM + 1, RADIUS_STEP_KM):
                biomass, count = _biomass_within_radius(cur, lat, lng, r, treatmentid)
                if biomass >= target:
                    # Phase 2: binary search between prev_radius and r
                    lo, hi = prev_radius, r
                    best_r, best_b, best_c = r, biomass, count

                    while hi - lo > 1.0:  # 1km precision
                        mid = (lo + hi) / 2
                        b, c = _biomass_within_radius(cur, lat, lng, mid, treatmentid)
                        if b >= target:
                            best_r, best_b, best_c = mid, b, c
                            hi = mid
                        else:
                            lo = mid

                    return RadiusResponse(
                        radius_km=round(best_r, 1),
                        available_biomass_bdt=round(best_b, 0),
                        demand_bdt=demand_bdt,
                        n_clusters_in_radius=best_c,
                    )
                prev_radius = r

            # If we never found enough biomass, return max radius result
            biomass, count = _biomass_within_radius(
                cur, lat, lng, RADIUS_MAX_KM, treatmentid
            )
            return RadiusResponse(
                radius_km=RADIUS_MAX_KM,
                available_biomass_bdt=round(biomass, 0),
                demand_bdt=demand_bdt,
                n_clusters_in_radius=count,
            )
    finally:
        conn.close()


# ── Quick test ────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 25MW direct combustion near Quincy (~150,000 BDT/year)
    result = estimate_radius(39.94, -120.95, 150000, treatmentid=1)
    print(f"Quincy clearcut: R*={result.radius_km}km, "
          f"{result.available_biomass_bdt:,.0f} BDT available, "
          f"{result.n_clusters_in_radius} clusters")

    # Same but SDI-55 (less biomass per cluster, larger radius)
    result = estimate_radius(39.94, -120.95, 150000, treatmentid=11)
    print(f"Quincy SDI-55: R*={result.radius_km}km, "
          f"{result.available_biomass_bdt:,.0f} BDT available, "
          f"{result.n_clusters_in_radius} clusters")