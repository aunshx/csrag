"""
Location Recommendation with H3 Hierarchical Spatial Index

Uses pre-computed H3 hexagonal summaries at three resolutions:
  R4 (~22km): Statewide region scouting, ~150 hexes, <2ms
  R5 (~8km):  Sub-region refinement, ~5-8 per parent, <1ms
  R6 (~3km):  Precise facility siting, ~7-10 per parent, <1ms

The agent navigates this hierarchy based on the decision stage.
Each resolution trades spatial precision for query speed.

Also supports returning ALL hexes at a resolution for map overlay.
"""

import h3
import math
from config import get_db_connection
from pydantic import BaseModel
from typing import Optional


class HexSummary(BaseModel):
    hex_id: str
    resolution: int
    lat: float
    lng: float
    n_clusters: int
    total_biomass: float
    avg_cost: float
    min_cost: float
    avg_fire: float
    avg_slope: float
    pct_ground: float
    avg_cf: float
    county_name: str
    score: float
    highlighted: bool = False
    # H3 boundary for frontend rendering
    boundary: list[list[float]] = []


class LocationResult(BaseModel):
    """Response from find_best_locations."""
    resolution: int
    n_hexes: int
    top_candidates: list[HexSummary]
    all_hexes: list[HexSummary] 
    parent_hex: Optional[str] = None


def _score_hex(hex_data: dict, priority: str) -> float:
    """Score a hex based on user priority."""
    biomass = float(hex_data["total_biomass"])
    cost = float(hex_data["avg_cost"])
    fire = float(hex_data["avg_fire"])
    pct_ground = float(hex_data["pct_ground"])

    if priority == "cost":
        return 1.0 / max(cost, 0.01)
    elif priority == "fire":
        return fire * 1000
    elif priority == "biomass":
        return biomass / 100000.0
    else:  # balanced
        return (
            (biomass / 100000.0) *
            (1.0 / max(cost, 0.01)) *
            (1.0 + fire * 100) *
            (pct_ground / 100.0 + 0.3)
        )


def _hex_boundary(hex_id: str) -> list[list[float]]:
    """Get hex polygon boundary as [[lat, lng], ...] for frontend."""
    try:
        boundary = h3.cell_to_boundary(hex_id)
        return [[round(lat, 5), round(lng, 5)] for lat, lng in boundary]
    except Exception:
        return []


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _query_hex_view(
    resolution: int,
    priority: str,
    parent_hex: str = None,
    min_biomass: float = 0,
    n_top: int = 3,
    region_lat: float = None,
    region_lng: float = None,
    region_radius_km: float = 150,
) -> LocationResult:
    """
    Query a hex resolution view, score, rank, and return results.
    Pass region_lat/lng to filter hexes within region_radius_km.
    """
    view = f"mv_hex_r{resolution}"

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if parent_hex:
                cur.execute(f"""
                    SELECT hex_id, centroid_lat, centroid_lng,
                           n_clusters, total_biomass, avg_cost, min_cost,
                           avg_fire, avg_slope, pct_ground, avg_cf, county_name
                    FROM {view}
                    WHERE parent_hex = %s
                      AND total_biomass > %s
                    ORDER BY total_biomass DESC
                """, (parent_hex, min_biomass))
            else:
                cur.execute(f"""
                    SELECT hex_id, centroid_lat, centroid_lng,
                           n_clusters, total_biomass, avg_cost, min_cost,
                           avg_fire, avg_slope, pct_ground, avg_cf, county_name
                    FROM {view}
                    WHERE total_biomass > %s
                    ORDER BY total_biomass DESC
                """, (min_biomass,))

            rows = cur.fetchall()

            all_hexes = []
            for r in rows:
                if region_lat is not None and region_lng is not None:
                    if _haversine_km(region_lat, region_lng, float(r[1]), float(r[2])) > region_radius_km:
                        continue
                data = {
                    "hex_id": r[0],
                    "lat": float(r[1]),
                    "lng": float(r[2]),
                    "n_clusters": int(r[3]),
                    "total_biomass": float(r[4]),
                    "avg_cost": round(float(r[5]), 2),
                    "min_cost": round(float(r[6]), 2),
                    "avg_fire": round(float(r[7]), 6),
                    "avg_slope": round(float(r[8]), 1),
                    "pct_ground": round(float(r[9]), 1),
                    "avg_cf": round(float(r[10]), 2) if r[10] else 1.5,
                    "county_name": r[11] or "Unknown",
                }
                data["score"] = round(_score_hex(data, priority), 4)
                all_hexes.append(data)

            # Sort by score descending
            all_hexes.sort(key=lambda x: x["score"], reverse=True)

            # Mark top N as highlighted
            top_ids = set()
            for i, h in enumerate(all_hexes[:n_top]):
                h["highlighted"] = True
                top_ids.add(h["hex_id"])

            # Build response
            hex_summaries = []
            top_candidates = []
            for h in all_hexes:
                summary = HexSummary(
                    hex_id=h["hex_id"],
                    resolution=resolution,
                    lat=h["lat"],
                    lng=h["lng"],
                    n_clusters=h["n_clusters"],
                    total_biomass=round(h["total_biomass"], 0),
                    avg_cost=h["avg_cost"],
                    min_cost=h["min_cost"],
                    avg_fire=h["avg_fire"],
                    avg_slope=h["avg_slope"],
                    pct_ground=h["pct_ground"],
                    avg_cf=h["avg_cf"],
                    county_name=h["county_name"],
                    score=h["score"],
                    highlighted=h.get("highlighted", False),
                    boundary=_hex_boundary(h["hex_id"]),
                )
                hex_summaries.append(summary)
                if h["hex_id"] in top_ids:
                    top_candidates.append(summary)

            return LocationResult(
                resolution=resolution,
                n_hexes=len(hex_summaries),
                top_candidates=top_candidates,
                all_hexes=hex_summaries,
                parent_hex=parent_hex,
            )
    finally:
        conn.close()


def find_best_locations(
    priority: str = "balanced",
    resolution: int = 4,
    parent_hex: str = None,
    min_capacity_mw: float = 25,
    technology: str = "direct_combustion",
    n_results: int = 3,
    region_lat: float = None,
    region_lng: float = None,
    region_radius_km: float = 150,
) -> LocationResult:
    """
    Find optimal facility locations using H3 hex hierarchy.
    Pass region_lat/lng to bias results to a specific county or area.
    """
    # Minimum biomass threshold based on capacity
    efficiency = {"direct_combustion": 0.25, "gasification": 0.35, "pyrolysis": 0.30}
    cf = {"direct_combustion": 0.85, "gasification": 0.80, "pyrolysis": 0.75}
    eta = efficiency.get(technology, 0.25)
    cap_factor = cf.get(technology, 0.85)
    demand_bdt = (min_capacity_mw * 1000 * cap_factor * 8760 * 0.0036) / (eta * 18.0)

    # At R4 (~22km), a hex covers enough area that we want at least
    # a fraction of demand locally. At finer resolutions, lower the bar.
    min_biomass = {4: demand_bdt * 0.3, 5: demand_bdt * 0.1, 6: demand_bdt * 0.05}
    threshold = min_biomass.get(resolution, 0)

    return _query_hex_view(
        resolution=resolution,
        priority=priority,
        parent_hex=parent_hex,
        min_biomass=threshold,
        n_top=n_results,
        region_lat=region_lat,
        region_lng=region_lng,
        region_radius_km=region_radius_km,
    )


if __name__ == "__main__":
    import time

    print("=== R4 statewide (cost priority) ===")
    t0 = time.time()
    result = find_best_locations(priority="cost", resolution=4, n_results=3)
    t1 = time.time()
    print(f"  {result.n_hexes} hexes, {(t1-t0)*1000:.1f}ms")
    for c in result.top_candidates:
        print(f"  ★ {c.county_name}: ({c.lat:.2f}, {c.lng:.2f}) "
              f"${c.avg_cost}/GT, {c.total_biomass:,.0f} BDT, "
              f"{c.n_clusters} clusters")

    if result.top_candidates:
        parent = result.top_candidates[0].hex_id
        print(f"\n=== R5 drill-down into {parent} ===")
        t0 = time.time()
        r5 = find_best_locations(
            priority="cost", resolution=5, parent_hex=parent, n_results=3
        )
        t1 = time.time()
        print(f"  {r5.n_hexes} hexes, {(t1-t0)*1000:.1f}ms")
        for c in r5.top_candidates:
            print(f"  ★ {c.county_name}: ({c.lat:.2f}, {c.lng:.2f}) "
                  f"${c.avg_cost}/GT, {c.total_biomass:,.0f} BDT")

    print("\n=== R4 statewide (fire priority) ===")
    result = find_best_locations(priority="fire", resolution=4, n_results=3)
    for c in result.top_candidates:
        print(f"  ★ {c.county_name}: fire={c.avg_fire:.4f}, "
              f"${c.avg_cost}/GT, {c.total_biomass:,.0f} BDT")