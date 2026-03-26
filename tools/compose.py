"""
Composition and Supply Curve (§6-§7 of CS-RAG pipeline)

Transport methodology:
  1. road_distance = haversine(cluster, facility) × cf_estimate  (Ch6)
  2. transport_cost = FRREDSS formula: labor + fuel + oil + ownership  (Ch3)
  
The cf_estimate (from Ch6 IDW interpolation of 445K OSRM routes)
replaces live OSRM calls, enabling real-time analysis of thousands
of clusters. The cost model matches FRREDSS transportation.ts.

All transport parameters are configurable. Default values match
FRREDSS defaults from thesis Table 3.13.
"""

import math
import copy
from models import ClusterData, SupplyCurveResponse, SelectedCluster

KM_TO_MILES = 0.621371

TRANSPORT_DEFAULTS = {
    "diesel_price": 4.50,            # $/gallon
    "wage": 24.71,                   # $/hour (BLS CA tractor-trailer)
    "benefits_overhead": 0.67,       # 67%
    "oil_cost_per_mile": 0.35,       # $/mile
    "truck_ownership_per_hour": 13.10,  # $/hour
    "fuel_economy_mpg": 6.0,         # miles per gallon
    "avg_speed_kmh": 40.0,           # average truck speed
    "payload_gt": 25.0,              # green tons per truck
}


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _normalize(value: float, min_val: float, max_val: float) -> float:
    if max_val == min_val:
        return 0.0
    return (value - min_val) / (max_val - min_val)


def _frredss_transport(road_distance_km: float, params: dict = None) -> dict:
    """
    FRREDSS-equivalent transport cost with full breakdown.

    Returns a dict with each cost component so the agent can
    show the user exactly how the cost was calculated.
    """
    p = {**TRANSPORT_DEFAULTS, **(params or {})}

    if road_distance_km <= 0:
        return {
            "cost_per_gt": 0.0,
            "labor": 0.0, "fuel": 0.0, "oil": 0.0, "ownership": 0.0,
            "cost_per_trip": 0.0,
            "road_km": 0.0, "road_miles_rt": 0.0, "hours_rt": 0.0,
        }

    one_way_miles = road_distance_km * KM_TO_MILES
    one_way_hours = road_distance_km / p["avg_speed_kmh"]

    rt_miles = one_way_miles * 2
    rt_hours = one_way_hours * 2

    labor = p["wage"] * (1 + p["benefits_overhead"]) * rt_hours
    fuel = p["diesel_price"] * rt_miles / p["fuel_economy_mpg"]
    oil = p["oil_cost_per_mile"] * rt_miles
    ownership = p["truck_ownership_per_hour"] * rt_hours

    cost_per_trip = labor + fuel + oil + ownership
    cost_per_gt = cost_per_trip / p["payload_gt"]

    return {
        "cost_per_gt": round(cost_per_gt, 2),
        "labor": round(labor, 2),
        "fuel": round(fuel, 2),
        "oil": round(oil, 2),
        "ownership": round(ownership, 2),
        "cost_per_trip": round(cost_per_trip, 2),
        "road_km": round(road_distance_km, 1),
        "road_miles_rt": round(rt_miles, 1),
        "hours_rt": round(rt_hours, 2),
    }


def _enrich_clusters(
    clusters: list[ClusterData],
    facility_lat: float,
    facility_lng: float,
    transport_params: dict = None,
) -> list[dict]:
    """
    Compute transport cost and delivered cost for each cluster.

    Uses haversine × cf_estimate for road distance (Ch6),
    then FRREDSS truck cost formula for cost (Ch3).
    """
    enriched = []
    for c in clusters:
        if c.total_biomass_bdt <= 0:
            continue

        straight_km = _haversine_km(
            c.landing_lat, c.landing_lng, facility_lat, facility_lng
        )
        road_km = straight_km * c.cf_estimate

        transport = _frredss_transport(road_km, transport_params)
        delivered_cost = c.harvest_cost + transport["cost_per_gt"]

        enriched.append({
            "cluster": c,
            "straight_km": straight_km,
            "road_km": road_km,
            "transport_cost": transport["cost_per_gt"],
            "transport_breakdown": transport,
            "delivered_cost": delivered_cost,
        })

    return enriched


def _score_and_select(
    enriched: list[dict],
    demand_bdt: float,
    alpha: float,
) -> list[dict]:
    """Score, sort, and select clusters to meet demand."""
    max_delivered = max(e["delivered_cost"] for e in enriched) if enriched else 1.0
    min_delivered = min(e["delivered_cost"] for e in enriched) if enriched else 0.0
    fire_min = min(e["cluster"].burn_probability for e in enriched) if enriched else 0.0
    fire_max = max(e["cluster"].burn_probability for e in enriched) if enriched else 1.0

    working = copy.deepcopy(enriched)

    for e in working:
        norm_cost = _normalize(e["delivered_cost"], min_delivered, max_delivered)
        norm_fire = 1.0 - _normalize(
            e["cluster"].burn_probability, fire_min, fire_max
        )
        e["score"] = alpha * norm_cost + (1 - alpha) * norm_fire

    working.sort(key=lambda x: x["score"])

    selected = []
    cumulative = 0.0
    for s in working:
        if cumulative >= demand_bdt:
            break
        cumulative += s["cluster"].total_biomass_bdt
        s["cumulative"] = cumulative
        selected.append(s)

    return selected


def build_supply_curve(
    clusters: list[ClusterData],
    facility_lat: float,
    facility_lng: float,
    demand_bdt: float,
    alpha: float = 1.0,
    transport_params: dict = None,
) -> SupplyCurveResponse:
    """
    Build a supply curve from retrieved clusters.

    Args:
        transport_params: Override any FRREDSS default. e.g.,
            {"diesel_price": 5.50} to test fuel price sensitivity.
            Unspecified params keep their defaults.
    """
    enriched = _enrich_clusters(clusters, facility_lat, facility_lng, transport_params)

    if not enriched:
        return SupplyCurveResponse(
            demand_bdt=demand_bdt, alpha=alpha,
            n_selected=0, n_candidates=len(clusters),
            avg_delivered_cost=0.0, marginal_delivered_cost=0.0,
            total_biomass_selected=0.0, fire_reduction_ratio=1.0,
            selected_clusters=[],
        )

    cost_only_selected = _score_and_select(enriched, demand_bdt, alpha=1.0)

    if cost_only_selected:
        baseline_total_fire = sum(
            s["cluster"].burn_probability * s["cluster"].total_biomass_bdt
            for s in cost_only_selected
        )
        baseline_biomass = sum(
            s["cluster"].total_biomass_bdt for s in cost_only_selected
        )
        baseline_avg_fire = baseline_total_fire / baseline_biomass if baseline_biomass > 0 else 0
    else:
        baseline_avg_fire = 0

    if alpha == 1.0:
        selected = cost_only_selected
    else:
        selected = _score_and_select(enriched, demand_bdt, alpha=alpha)

    if not selected:
        return SupplyCurveResponse(
            demand_bdt=demand_bdt, alpha=alpha,
            n_selected=0, n_candidates=len(clusters),
            avg_delivered_cost=0.0, marginal_delivered_cost=0.0,
            total_biomass_selected=0.0, fire_reduction_ratio=1.0,
            selected_clusters=[],
        )

    total_biomass = sum(s["cluster"].total_biomass_bdt for s in selected)

    # Cost decomposition: avg harvest vs avg transport
    avg_harvest = sum(
        s["cluster"].harvest_cost * s["cluster"].total_biomass_bdt for s in selected
    ) / total_biomass
    avg_transport = sum(
        s["transport_cost"] * s["cluster"].total_biomass_bdt for s in selected
    ) / total_biomass
    avg_cost = avg_harvest + avg_transport

    marginal_cost = selected[-1]["delivered_cost"]

    selected_total_fire = sum(
        s["cluster"].burn_probability * s["cluster"].total_biomass_bdt
        for s in selected
    )
    selected_avg_fire = selected_total_fire / total_biomass if total_biomass > 0 else 0

    if baseline_avg_fire > 0 and alpha < 1.0:
        fire_reduction_ratio = selected_avg_fire / baseline_avg_fire
    else:
        fire_reduction_ratio = 1.0

    output_clusters = []
    cumulative = 0.0
    for s in selected:
        cumulative += s["cluster"].total_biomass_bdt
        output_clusters.append(SelectedCluster(
            cluster_no=s["cluster"].cluster_no,
            harvest_cost=round(s["cluster"].harvest_cost, 2),
            transport_cost=round(s["transport_cost"], 2),
            delivered_cost=round(s["delivered_cost"], 2),
            biomass_bdt=round(s["cluster"].total_biomass_bdt, 1),
            cumulative_biomass=round(cumulative, 0),
            score=round(s["score"], 4),
            burn_probability=s["cluster"].burn_probability,
            distance_km=round(s["road_km"], 1),
        ))

    return SupplyCurveResponse(
        demand_bdt=demand_bdt,
        alpha=alpha,
        n_selected=len(selected),
        n_candidates=len(clusters),
        avg_delivered_cost=round(avg_cost, 2),
        marginal_delivered_cost=round(marginal_cost, 2),
        total_biomass_selected=round(total_biomass, 0),
        fire_reduction_ratio=round(fire_reduction_ratio, 2),
        selected_clusters=output_clusters,
        avg_harvest_cost=round(avg_harvest, 2),
        avg_transport_cost=round(avg_transport, 2),
        transport_params_used={**TRANSPORT_DEFAULTS, **(transport_params or {})},
    )


def estimate_transport_cost(
    straight_line_km: float,
    circuity_factor: float,
    params: dict = None,
) -> dict:
    """
    Calculate transport cost for a single route with full breakdown.
    Used by the agent to explain transport methodology to the user.
    """
    road_km = straight_line_km * circuity_factor
    result = _frredss_transport(road_km, params)
    result["straight_line_km"] = round(straight_line_km, 1)
    result["circuity_factor"] = circuity_factor
    return result


if __name__ == "__main__":
    print("=== FRREDSS Transport Cost Model ===")
    print(f"Default parameters: {TRANSPORT_DEFAULTS}\n")

    for dist in [10, 20, 40, 60, 100]:
        t = _frredss_transport(dist)
        print(f"  {dist:>3}km road: ${t['cost_per_gt']:.2f}/GT "
              f"(labor=${t['labor']:.0f} fuel=${t['fuel']:.0f} "
              f"oil=${t['oil']:.0f} truck=${t['ownership']:.0f} "
              f"= ${t['cost_per_trip']:.0f}/trip)")

    print("\n=== Circuity factor effect (30km haversine) ===")
    for cf in [1.3, 1.8, 2.2, 3.0, 4.3]:
        t = estimate_transport_cost(30, cf)
        print(f"  CF={cf:.1f}: road={t['road_km']:.0f}km, "
              f"${t['cost_per_gt']:.2f}/GT, {t['hours_rt']:.1f}h round trip")

    print("\n=== Diesel price sensitivity (50km road) ===")
    for diesel in [3.50, 4.50, 5.50, 6.50]:
        t = _frredss_transport(50, {"diesel_price": diesel})
        print(f"  ${diesel:.2f}/gal: ${t['cost_per_gt']:.2f}/GT "
              f"(fuel component: ${t['fuel']:.2f})")