"""
Spatial Retrieval (§5 of CS-RAG pipeline)

Retrieves forest clusters within a radius of the facility.

Two modes:
  - Default: queries mv_cluster_supply (auto-selected best system per cluster)
  - FRREDSS mode: queries frcs_predictions with a specific harvest_system
"""

from config import get_db_connection
from models import ClusterData, RetrieveResponse


def retrieve_clusters(
    lat: float,
    lng: float,
    radius_km: float,
    treatmentid: int = 1,
    harvest_system: str = None,
    max_slope: float = None,
) -> RetrieveResponse:
    """
    Retrieve clusters within radius of facility.

    Args:
        lat, lng: Facility coordinates
        radius_km: Procurement radius in km
        treatmentid: Treatment type (1-11)
        harvest_system: If specified, forces this system (FRREDSS mode).
                       If None, uses pre-selected best system (default mode).
        max_slope: Optional slope constraint (e.g., 60 to exclude steep terrain)

    Returns:
        RetrieveResponse with list of ClusterData
    """
    # Degree-based radius approximation
    # At ~40°N: 1° ≈ 111km. Slightly over-fetches at higher latitudes, which is fine.
    radius_deg = radius_km / 111.0

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if harvest_system is None:
                # Default mode: query mv_cluster_supply (best system pre-selected)
                query = """
                    SELECT cluster_no, treatmentid, best_system,
                           harvest_cost, total_biomass_bdt,
                           center_lat, center_lng, landing_lat, landing_lng,
                           slope, burn_probability, cf_estimate, county_name, land_use
                    FROM mv_cluster_supply
                    WHERE treatmentid = %s
                      AND ST_DWithin(
                          geom,
                          ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                          %s
                      )
                """
                params = [treatmentid, lng, lat, radius_deg]

                if max_slope is not None:
                    query += " AND slope <= %s"
                    params.append(max_slope)

                cur.execute(query, params)

            else:
                # FRREDSS mode: query frcs_predictions with specific system
                query = """
                    SELECT p.cluster_no, p.treatmentid, p.harvest_system,
                           p.total_costpergt, p.total_biomass,
                           c.center_lat, c.center_lng,
                           c.landing_lat, c.landing_lng,
                           c.slope, c.burn_probability, c.cf_estimate,
                           c.county_name
                    FROM frcs_predictions p
                    JOIN clusters_with_fire_probability c
                      ON p.cluster_no = c.cluster_no::integer
                      AND c.treatmentid = p.treatmentid
                      AND c.year = 2025
                    WHERE p.treatmentid = %s
                      AND p.harvest_system = %s
                      AND p.is_feasible = true
                      AND ST_DWithin(
                          c.geom,
                          ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                          %s
                      )
                """
                params = [treatmentid, harvest_system, lng, lat, radius_deg]

                if max_slope is not None:
                    query += " AND c.slope <= %s"
                    params.append(max_slope)

                cur.execute(query, params)

            rows = cur.fetchall()
            clusters = [
                ClusterData(
                    cluster_no=r[0],
                    treatmentid=r[1],
                    best_system=r[2],
                    harvest_cost=float(r[3]),
                    total_biomass_bdt=float(r[4]),
                    center_lat=float(r[5]),
                    center_lng=float(r[6]),
                    landing_lat=float(r[7]),
                    landing_lng=float(r[8]),
                    slope=float(r[9]),
                    burn_probability=float(r[10]) if r[10] is not None else 0.0,
                    cf_estimate=float(r[11]) if r[11] is not None else 1.5,
                    county_name=r[12],
                    land_use=r[13] if len(r) > 13 else None,
                )
                for r in rows
            ]

            return RetrieveResponse(
                n_clusters=len(clusters),
                clusters=clusters,
                facility_lat=lat,
                facility_lng=lng,
            )
    finally:
        conn.close()


# Quick Test
if __name__ == "__main__":
    # Default mode: best system auto-selected
    result = retrieve_clusters(39.94, -120.95, 30, treatmentid=1)
    print(f"Default mode: {result.n_clusters} clusters within 30km of Quincy")
    if result.clusters:
        c = result.clusters[0]
        print(f"  Example: cluster {c.cluster_no}, {c.best_system}, "
              f"${c.harvest_cost:.1f}/GT, {c.total_biomass_bdt:.0f} BDT")

    # FRREDSS mode: specific system
    result = retrieve_clusters(
        39.94, -120.95, 30,
        treatmentid=1, harvest_system="Ground-Based Mech WT"
    )
    print(f"FRREDSS mode: {result.n_clusters} clusters (Ground-Based Mech WT only)")