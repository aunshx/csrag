"""
Pipeline Test: Trace 1 (Full Facility Analysis)

Exercises the complete CS-RAG pipeline without the LLM agent.
Simulates: "25MW biomass facility near Quincy, fire risk matters"

Run from the csrag-api directory:
    python test_pipeline.py
"""

import json
import time

# ── Step 0: TEA Demand Estimation ─────────────────────────────────────
print("=" * 60)
print("TRACE 1: Full Facility Analysis (25MW near Quincy)")
print("=" * 60)

print("\n[§4a] TEA Demand Estimation")
print("-" * 40)

from tools.tea import estimate_demand

tea = estimate_demand(capacity_kw=25000, technology="direct_combustion")
print(f"  Technology: {tea.technology}")
print(f"  Efficiency: {tea.efficiency}")
print(f"  Capacity factor: {tea.capacity_factor}")
print(f"  Demand: {tea.demand_bdt_per_year:,.0f} BDT/year ({tea.demand_bdt_per_day:,.0f} BDT/day)")

demand = tea.demand_bdt_per_year

# ── Step 1: Regional Context ─────────────────────────────────────────
print("\n[Tier 2] Regional Context")
print("-" * 40)

from tools.regional import get_regional_summary

regions = get_regional_summary(lat=39.94, lng=-120.95)
for r in regions:
    print(f"  {r.county_name}: {r.n_clusters:,} clusters, "
          f"avg ${r.avg_harvest_cost:.1f}/GT, "
          f"fire={r.avg_burn_prob:.4f}, "
          f"CF={r.avg_cf:.1f}, "
          f"{r.pct_ground_feasible:.0f}% ground")


# ── Step 2: Radius Estimation ────────────────────────────────────────
print("\n[§4b] Radius Estimation (SDI-55)")
print("-" * 40)

from tools.radius import estimate_radius

t0 = time.time()
radius_result = estimate_radius(
    lat=39.94, lng=-120.95,
    demand_bdt=demand,
    treatmentid=11,  # SDI-55
)
t1 = time.time()
print(f"  Radius: {radius_result.radius_km} km")
print(f"  Available biomass: {radius_result.available_biomass_bdt:,.0f} BDT")
print(f"  Clusters in radius: {radius_result.n_clusters_in_radius:,}")
print(f"  Time: {t1-t0:.1f}s")


# ── Step 3: Cluster Retrieval ────────────────────────────────────────
print("\n[§5] Cluster Retrieval (SDI-55)")
print("-" * 40)

from tools.retrieve import retrieve_clusters

t0 = time.time()
retrieval = retrieve_clusters(
    lat=39.94, lng=-120.95,
    radius_km=radius_result.radius_km,
    treatmentid=11,
)
t1 = time.time()
print(f"  Retrieved: {retrieval.n_clusters:,} clusters")
print(f"  Time: {t1-t0:.1f}s")

if retrieval.clusters:
    costs = [c.harvest_cost for c in retrieval.clusters]
    print(f"  Cost range: ${min(costs):.1f} - ${max(costs):.1f}/GT")
    fires = [c.burn_probability for c in retrieval.clusters]
    print(f"  Fire range: {min(fires):.5f} - {max(fires):.5f}")
    systems = set(c.best_system for c in retrieval.clusters)
    print(f"  Systems: {', '.join(systems)}")


# ── Step 4: Supply Curve (α=0.95, fire matters) ──────────────────────
print("\n[§6-§7] Supply Curve (alpha=0.95)")
print("-" * 40)

from tools.compose import build_supply_curve

t0 = time.time()
curve_fire = build_supply_curve(
    clusters=retrieval.clusters,
    facility_lat=39.94,
    facility_lng=-120.95,
    demand_bdt=demand,
    alpha=0.95,
)
t1 = time.time()
print(f"  Selected: {curve_fire.n_selected} / {curve_fire.n_candidates} clusters")
print(f"  Avg delivered cost: ${curve_fire.avg_delivered_cost:.2f}/GT")
print(f"  Marginal cost: ${curve_fire.marginal_delivered_cost:.2f}/GT")
print(f"  Biomass selected: {curve_fire.total_biomass_selected:,.0f} BDT")
print(f"  Fire reduction: {curve_fire.fire_reduction_ratio:.1f}x")
print(f"  Time: {t1-t0:.3f}s")


# ── Step 5: Comparison - cost-only baseline ──────────────────────────
print("\n[Comparison] Cost-only baseline (alpha=1.0)")
print("-" * 40)

curve_cost = build_supply_curve(
    clusters=retrieval.clusters,
    facility_lat=39.94,
    facility_lng=-120.95,
    demand_bdt=demand,
    alpha=1.0,
)
print(f"  Avg delivered cost: ${curve_cost.avg_delivered_cost:.2f}/GT")
print(f"  Fire reduction: {curve_cost.fire_reduction_ratio:.1f}x")

cost_premium = curve_fire.avg_delivered_cost - curve_cost.avg_delivered_cost
cost_pct = cost_premium / curve_cost.avg_delivered_cost * 100
fire_gain = curve_fire.fire_reduction_ratio - curve_cost.fire_reduction_ratio

print(f"\n  Fire-aware premium: ${cost_premium:.2f}/GT ({cost_pct:.1f}%)")
print(f"  Additional fire reduction: {fire_gain:.1f}x")
if cost_pct > 0:
    leverage = fire_gain / (cost_pct / 100) if cost_pct > 0 else 0
    print(f"  Leverage: {leverage:.1f}x (fire gain per unit cost premium)")


# ── Step 6: Multi-alpha sweep ────────────────────────────────────────
print("\n[Trace 13] Multi-alpha sweep")
print("-" * 40)
print(f"  {'Alpha':>6} {'Avg $/GT':>9} {'Marginal':>9} {'Fire':>6} {'Leverage':>9}")

baseline_cost = None
baseline_fire = None

for alpha in [1.0, 0.95, 0.90, 0.80, 0.70, 0.50]:
    curve = build_supply_curve(
        clusters=retrieval.clusters,
        facility_lat=39.94,
        facility_lng=-120.95,
        demand_bdt=demand,
        alpha=alpha,
    )
    if alpha == 1.0:
        baseline_cost = curve.avg_delivered_cost
        baseline_fire = curve.fire_reduction_ratio
        lev_str = "   ---"
    else:
        cost_pct = (curve.avg_delivered_cost - baseline_cost) / baseline_cost * 100
        fire_gain = curve.fire_reduction_ratio - baseline_fire
        lev = fire_gain / (cost_pct / 100) if cost_pct > 0 else 0
        lev_str = f"{lev:>8.1f}x"

    print(f"  {alpha:>6.2f} {curve.avg_delivered_cost:>8.2f} "
          f"{curve.marginal_delivered_cost:>9.2f} "
          f"{curve.fire_reduction_ratio:>5.1f}x {lev_str}")


print("\n" + "=" * 60)
print("Pipeline test complete.")
print("=" * 60)
