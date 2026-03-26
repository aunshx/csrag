"""
Tradeoff Analysis (dynamic Tier 3 knowledge generation)

Instead of citing memorized statewide averages, this tool computes
location-specific analytical findings by running multi-alpha sweeps,
treatment comparisons, and ownership/system breakdowns on the
actual retrieved clusters.

This is the core "compositional" innovation: knowledge that emerges
from composing multiple spatial prediction layers at query time.
"""

from tools.compose import build_supply_curve
from models import ClusterData


def analyze_tradeoffs(
    clusters: list[ClusterData],
    facility_lat: float,
    facility_lng: float,
    demand_bdt: float,
) -> dict:
    """
    Compute location-specific analytical findings.

    Runs on cached clusters, no DB calls needed. ~800ms total.

    Returns:
        - Multi-alpha sweep with leverage at each level
        - Optimal alpha and leverage for THIS location
        - Ownership breakdown (federal/private/state)
        - Harvesting system distribution
        - Cost decomposition (cheapest vs most expensive quartile)
        - Fire risk distribution of the local cluster pool
    """
    if not clusters or demand_bdt <= 0:
        return {"error": "No clusters or demand to analyze"}

    # ── 1. Multi-alpha sweep ──────────────────────────────────────────
    alphas = [1.0, 0.95, 0.90, 0.85, 0.80, 0.70, 0.50]

    baseline = build_supply_curve(
        clusters, facility_lat, facility_lng, demand_bdt, alpha=1.0
    )
    baseline_cost = baseline.avg_delivered_cost

    sweep_results = []
    best_leverage = 0.0
    best_alpha = 1.0

    for alpha in alphas:
        if alpha == 1.0:
            curve = baseline
        else:
            curve = build_supply_curve(
                clusters, facility_lat, facility_lng, demand_bdt, alpha=alpha
            )

        cost_premium_pct = (
            (curve.avg_delivered_cost - baseline_cost) / baseline_cost * 100
            if baseline_cost > 0 else 0
        )
        fire_gain = curve.fire_reduction_ratio - 1.0
        leverage = (
            fire_gain / (cost_premium_pct / 100)
            if cost_premium_pct > 0.1 else 0
        )

        sweep_results.append({
            "alpha": alpha,
            "avg_cost": round(curve.avg_delivered_cost, 2),
            "marginal_cost": round(curve.marginal_delivered_cost, 2),
            "fire_reduction": round(curve.fire_reduction_ratio, 2),
            "cost_premium_pct": round(cost_premium_pct, 1),
            "leverage": round(leverage, 1),
            "n_selected": curve.n_selected,
        })

        if leverage > best_leverage:
            best_leverage = leverage
            best_alpha = alpha

    # ── 2. Ownership breakdown ────────────────────────────────────────
    ownership_counts = {}
    for c in clusters:
        owner = (c.land_use or "Unknown").strip()
        ownership_counts[owner] = ownership_counts.get(owner, 0) + 1

    total = len(clusters)
    ownership_pcts = {
        k: round(v / total * 100, 1)
        for k, v in sorted(ownership_counts.items(), key=lambda x: -x[1])
    }

    # ── 3. Harvesting system distribution ─────────────────────────────
    system_counts = {}
    for c in clusters:
        sys = c.best_system or "Unknown"
        system_counts[sys] = system_counts.get(sys, 0) + 1

    system_pcts = {
        k: round(v / total * 100, 1)
        for k, v in sorted(system_counts.items(), key=lambda x: -x[1])
    }

    # ── 4. Fire risk distribution ─────────────────────────────────────
    fire_probs = [c.burn_probability for c in clusters]
    fire_probs.sort()
    n = len(fire_probs)
    high_fire = sum(1 for f in fire_probs if f > 0.02)
    moderate_fire = sum(1 for f in fire_probs if 0.01 < f <= 0.02)
    low_fire = sum(1 for f in fire_probs if f <= 0.01)

    fire_distribution = {
        "high_pct": round(high_fire / n * 100, 1) if n > 0 else 0,
        "moderate_pct": round(moderate_fire / n * 100, 1) if n > 0 else 0,
        "low_pct": round(low_fire / n * 100, 1) if n > 0 else 0,
        "max_burn_prob": round(max(fire_probs), 5) if fire_probs else 0,
        "mean_burn_prob": round(sum(fire_probs) / n, 5) if n > 0 else 0,
    }
    costs = sorted([c.harvest_cost for c in clusters])
    n_costs = len(costs)
    cost_quartiles = {
        "q1_cost": round(costs[n_costs // 4], 2) if n_costs > 3 else 0,
        "median_cost": round(costs[n_costs // 2], 2) if n_costs > 1 else 0,
        "q3_cost": round(costs[3 * n_costs // 4], 2) if n_costs > 3 else 0,
        "min_cost": round(costs[0], 2) if costs else 0,
        "max_cost": round(costs[-1], 2) if costs else 0,
    }
    slopes = [c.slope for c in clusters]
    ground_feasible = sum(1 for s in slopes if s < 40)
    cable_terrain = sum(1 for s in slopes if 40 <= s < 60)
    helicopter_terrain = sum(1 for s in slopes if s >= 60)

    terrain = {
        "ground_pct": round(ground_feasible / n * 100, 1) if n > 0 else 0,
        "cable_pct": round(cable_terrain / n * 100, 1) if n > 0 else 0,
        "helicopter_pct": round(helicopter_terrain / n * 100, 1) if n > 0 else 0,
        "avg_slope": round(sum(slopes) / n, 1) if n > 0 else 0,
    }

    return {
        "alpha_sweep": sweep_results,
        "optimal_alpha": best_alpha,
        "optimal_leverage": round(best_leverage, 1),
        "baseline_cost": round(baseline_cost, 2),
        "ownership": ownership_pcts,
        "systems": system_pcts,
        "fire_distribution": fire_distribution,
        "cost_quartiles": cost_quartiles,
        "terrain": terrain,
        "n_clusters_analyzed": total,
    }


if __name__ == "__main__":
    print("analyze_tradeoffs module loaded successfully")
    print("Requires cached clusters from retrieve_clusters to run")