"""
Multi-Year Procurement Projection

Simulates facility operation over N years with:
  - Temporal depletion: clusters selected in year K are unavailable in K+1
  - Radius expansion: as nearby clusters deplete, search expands
  - Cost escalation: later years draw from more distant/expensive clusters
  - Fire benefit tracking: cumulative fire risk reduction over time

Uses cached clusters from retrieve_clusters. No DB calls needed.
Runs build_supply_curve for each year on the shrinking cluster pool.

Returns year-by-year breakdown for the agent to present.
"""

from tools.compose import build_supply_curve
from models import ClusterData
from tools.retrieve import retrieve_clusters
import math

def _straight_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def project_multi_year(
    clusters: list[ClusterData],
    facility_lat: float,
    facility_lng: float,
    demand_bdt: float,
    alpha: float = 1.0,
    n_years: int = 10,
    transport_params: dict = None,
    regrowth_rate: float = 0.0,
) -> dict:
    """
    Simulate multi-year procurement from the same facility.

    Each year:
      1. Run build_supply_curve on remaining clusters
      2. Remove selected clusters from the pool (depleted)
      3. Record year's cost, radius, fire, clusters used
      4. If regrowth_rate > 0, partially restore earlier clusters

    Args:
        clusters: Full cluster pool (from retrieve_clusters, possibly with
                  a larger radius than single-year needs)
        facility_lat, facility_lng: Facility location
        demand_bdt: Annual demand (constant across years)
        alpha: Cost-fire weighting
        n_years: Number of years to simulate (1-20)
        transport_params: Override transport defaults
        regrowth_rate: Annual biomass regrowth as fraction (0.02 = 2%/year)

    Returns:
        Dict with year-by-year breakdown and summary statistics
    """
    if not clusters or demand_bdt <= 0:
        return {"error": "No clusters or demand to project"}

    n_years = min(max(n_years, 1), 20)  # clamp to 1-20

    # Working pool: deep copy so we can mutate biomass
    pool = []
    for c in clusters:
        pool.append({
            "cluster": c,
            "remaining_biomass": c.total_biomass_bdt,
            "year_first_harvested": None,
            "times_harvested": 0,
        })

    yearly_results = []
    cumulative_fire_reduction = 0.0
    cumulative_biomass = 0.0
    total_depleted = 0

    for year in range(1, n_years + 1):
        # Build cluster list from remaining pool
        available = []
        for p in pool:
            if p["remaining_biomass"] > 0.5:  # minimum viable biomass
                # Create a modified ClusterData with current biomass
                c = p["cluster"]
                available.append(ClusterData(
                    cluster_no=c.cluster_no,
                    treatmentid=c.treatmentid,
                    best_system=c.best_system,
                    harvest_cost=c.harvest_cost,
                    total_biomass_bdt=p["remaining_biomass"],
                    center_lat=c.center_lat,
                    center_lng=c.center_lng,
                    landing_lat=c.landing_lat,
                    landing_lng=c.landing_lng,
                    slope=c.slope,
                    burn_probability=c.burn_probability,
                    cf_estimate=c.cf_estimate,
                    county_name=c.county_name,
                ))

        if not available:
            yearly_results.append({
                "year": year,
                "status": "exhausted",
                "error": "No clusters remaining with sufficient biomass",
            })
            break

        # Run supply curve on available pool
        try:
            result = build_supply_curve(
                clusters=available,
                facility_lat=facility_lat,
                facility_lng=facility_lng,
                demand_bdt=demand_bdt,
                alpha=alpha,
                transport_params=transport_params,
            )
        except Exception as e:
            yearly_results.append({
                "year": year,
                "status": "error",
                "error": str(e),
            })
            break

        # Check if demand was met
        demand_met = result.total_biomass_selected >= demand_bdt * 0.95

        # Mark selected clusters as depleted
        selected_nos = set(sc.cluster_no for sc in result.selected_clusters)
        year_depleted = 0
        for p in pool:
            if p["cluster"].cluster_no in selected_nos:
                p["remaining_biomass"] = 0.0  # fully depleted
                if p["year_first_harvested"] is None:
                    p["year_first_harvested"] = year
                p["times_harvested"] += 1
                year_depleted += 1

        total_depleted += year_depleted

        selected_lookup = {p["cluster"].cluster_no: p["cluster"] for p in pool if p["cluster"].cluster_no in selected_nos}
        max_distance = 0
        for c in selected_lookup.values():
            d = _straight_km(c.landing_lat, c.landing_lng, facility_lat, facility_lng)
            if d > max_distance:
                max_distance = d


        cumulative_biomass += result.total_biomass_selected
        cumulative_fire_reduction += (result.fire_reduction_ratio - 1.0) if result.fire_reduction_ratio > 0 else 0

        yearly_results.append({
            "year": year,
            "status": "ok" if demand_met else "shortfall",
            "demand_bdt": round(demand_bdt, 0),
            "biomass_selected": round(result.total_biomass_selected, 0),
            "n_selected": result.n_selected,
            "n_available": len(available),
            "avg_delivered_cost": result.avg_delivered_cost,
            "avg_harvest_cost": result.avg_harvest_cost,
            "avg_transport_cost": result.avg_transport_cost,
            "marginal_cost": result.marginal_delivered_cost,
            "max_distance_km": round(max_distance, 1),
            "effective_radius_km": round(max_distance, 1),
            "fire_reduction": result.fire_reduction_ratio,
            "clusters_depleted_this_year": year_depleted,
            "selected_cluster_nos": list(selected_nos),
        })

        # Apply regrowth to previously harvested clusters
        if regrowth_rate > 0:
            for p in pool:
                if p["year_first_harvested"] is not None and p["remaining_biomass"] == 0:
                    years_since = year - p["year_first_harvested"]
                    if years_since > 0:
                        original = p["cluster"].total_biomass_bdt
                        regrown = original * regrowth_rate * years_since
                        p["remaining_biomass"] = min(regrown, original)

    # Summary statistics
    ok_years = [y for y in yearly_results if y.get("status") == "ok"]
    if ok_years:
        first_year_cost = ok_years[0]["avg_delivered_cost"]
        last_year_cost = ok_years[-1]["avg_delivered_cost"]
        cost_escalation_pct = (
            (last_year_cost - first_year_cost) / first_year_cost * 100
            if first_year_cost > 0 else 0
        )
        avg_annual_cost = sum(y["avg_delivered_cost"] for y in ok_years) / len(ok_years)
    else:
        cost_escalation_pct = 0
        avg_annual_cost = 0
        first_year_cost = 0
        last_year_cost = 0

    remaining_pool = sum(1 for p in pool if p["remaining_biomass"] > 0.5)

    summary = {
        "n_years_simulated": len(yearly_results),
        "n_years_demand_met": len(ok_years),
        "first_year_cost": round(first_year_cost, 2),
        "last_year_cost": round(last_year_cost, 2),
        "avg_annual_cost": round(avg_annual_cost, 2),
        "cost_escalation_pct": round(cost_escalation_pct, 1),
        "total_biomass_harvested": round(cumulative_biomass, 0),
        "total_clusters_depleted": total_depleted,
        "clusters_remaining": remaining_pool,
        "clusters_original": len(pool),
    }

    return {
        "summary": summary,
        "yearly": yearly_results,
    }

async def project_multi_year_streaming(
    clusters, facility_lat, facility_lng, demand_bdt,
    alpha=1.0, n_years=10, transport_params=None, regrowth_rate=0.0
):
    """Same logic as project_multi_year but yields one year at a time."""
    pool = [{"cluster": c, "remaining_biomass": c.total_biomass_bdt,
             "year_first_harvested": None, "times_harvested": 0} for c in clusters]
    
    known_selected = set()

    for year in range(1, n_years + 1):
        available = [
            ClusterData(
                cluster_no=p["cluster"].cluster_no,
                treatmentid=p["cluster"].treatmentid,
                best_system=p["cluster"].best_system,
                harvest_cost=p["cluster"].harvest_cost,
                total_biomass_bdt=p["remaining_biomass"],
                center_lat=p["cluster"].center_lat,
                center_lng=p["cluster"].center_lng,
                landing_lat=p["cluster"].landing_lat,
                landing_lng=p["cluster"].landing_lng,
                slope=p["cluster"].slope,
                burn_probability=p["cluster"].burn_probability,
                cf_estimate=p["cluster"].cf_estimate,
                county_name=p["cluster"].county_name,
            )
            for p in pool if p["remaining_biomass"] > 0.5
        ]

        if not available:
            yield {"year": year, "status": "exhausted",
                   "selected_cluster_nos": [], "n_selected": 0,
                   "avg_delivered_cost": 0, "avg_harvest_cost": 0,
                   "avg_transport_cost": 0, "effective_radius_km": 0}
            break

        result = build_supply_curve(available, facility_lat, facility_lng, demand_bdt, alpha, transport_params)

        selected_nos = set(sc.cluster_no for sc in result.selected_clusters)
        for p in pool:
            if p["cluster"].cluster_no in selected_nos:
                p["remaining_biomass"] = 0.0
                if p["year_first_harvested"] is None:
                    p["year_first_harvested"] = year

        selected_lookup = {p["cluster"].cluster_no: p["cluster"] for p in pool if p["cluster"].cluster_no in selected_nos}
        max_distance = max(
            (_straight_km(c.landing_lat, c.landing_lng, facility_lat, facility_lng) for c in selected_lookup.values()),
            default=0
        )

        demand_met = result.total_biomass_selected >= demand_bdt * 0.95
        yield {
            "year": year,
            "status": "ok" if demand_met else "shortfall",
            "avg_delivered_cost": result.avg_delivered_cost,
            "avg_harvest_cost": result.avg_harvest_cost,
            "avg_transport_cost": result.avg_transport_cost,
            "effective_radius_km": round(max_distance, 1),
            "n_selected": result.n_selected,
            "selected_cluster_nos": list(selected_nos),
        }
        
if __name__ == "__main__":
    print("multi_year projection module loaded")
    print("Requires cached clusters from retrieve_clusters to run")
    print("Call project_multi_year(clusters, lat, lng, demand, alpha, n_years)")