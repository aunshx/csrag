"""
Conversation State Manager

Maintains state across turns for:
  - Facility parameters (location, capacity, technology, demand)
  - Current and previous scenarios (for delta reporting)
  - Cached clusters (so alpha-only changes skip re-retrieval)
  - Last supply curve result (for spatial rendering)
"""

from models import (
    ConversationState, FacilityParams, ScenarioResult,
    ClusterData, TechnologyType, SupplyCurveResponse,
)


class StateManager:
    """Manages conversation state across agent turns."""

    def __init__(self):
        self.state = ConversationState()
        self._last_supply_result: SupplyCurveResponse | None = None

    def set_facility(
        self,
        lat: float,
        lng: float,
        capacity_kw: float,
        technology: str = "direct_combustion",
        demand_bdt: float = 0.0,
    ):
        self.state.facility = FacilityParams(
            lat=lat,
            lng=lng,
            capacity_kw=capacity_kw,
            technology=TechnologyType(technology),
            demand_bdt=demand_bdt,
        )

    def set_scenario(self, scenario: ScenarioResult):
        """Push current scenario to history and set new one."""
        if self.state.current_scenario is not None:
            self.state.previous_scenarios.append(self.state.current_scenario)
        self.state.current_scenario = scenario

    def set_supply_result(self, result: SupplyCurveResponse):
        """Store the last supply curve result for spatial data extraction."""
        self._last_supply_result = result

    def cache_clusters(
        self,
        clusters: list[ClusterData],
        treatmentid: int,
        radius_km: float,
    ):
        """Cache retrieved clusters for alpha-only refinements."""
        self.state.cached_clusters = clusters
        self.state.cached_treatmentid = treatmentid
        self.state.cached_radius_km = radius_km

    def can_reuse_cache(self, treatmentid: int, radius_km: float) -> bool:
        """Check if cached clusters can be reused (same treatment and radius)."""
        return (
            len(self.state.cached_clusters) > 0
            and self.state.cached_treatmentid == treatmentid
            and self.state.cached_radius_km == radius_km
        )

    def get_delta_report(self, new_scenario: ScenarioResult) -> dict:
        """Compare new scenario against current scenario."""
        old = self.state.current_scenario
        if old is None:
            return {}

        return {
            "cost_delta": round(new_scenario.avg_cost - old.avg_cost, 2),
            "cost_pct_change": round(
                (new_scenario.avg_cost - old.avg_cost) / old.avg_cost * 100, 1
            ) if old.avg_cost > 0 else 0,
            "fire_delta": round(new_scenario.fire_reduction - old.fire_reduction, 2),
            "radius_delta": round(new_scenario.radius_km - old.radius_km, 1),
            "clusters_delta": new_scenario.n_clusters_selected - old.n_clusters_selected,
        }

    def get_all_scenarios_for_comparison(self) -> list[ScenarioResult]:
        """Get all scenarios (previous + current) for comparison table."""
        scenarios = list(self.state.previous_scenarios)
        if self.state.current_scenario is not None:
            scenarios.append(self.state.current_scenario)
        return scenarios

    def to_context_string(self) -> str:
        """Serialize current state for the agent's context."""
        parts = []

        if self.state.facility:
            f = self.state.facility
            parts.append(
                f"Current facility: ({f.lat}, {f.lng}), "
                f"{f.capacity_kw/1000:.0f}MW {f.technology.value}, "
                f"{f.demand_bdt:,.0f} BDT/year demand"
            )

        if self.state.current_scenario:
            s = self.state.current_scenario
            parts.append(
                f"Current scenario: {s.treatment} (id={s.treatmentid}), "
                f"system={'auto' if s.harvest_system is None else s.harvest_system}, "
                f"alpha={s.alpha}, R={s.radius_km}km, "
                f"avg=${s.avg_cost:.1f}/GT, marginal=${s.marginal_cost:.1f}/GT, "
                f"fire_reduction={s.fire_reduction:.1f}x, "
                f"{s.n_clusters_selected} clusters"
            )

        if self.state.previous_scenarios:
            parts.append(
                f"Previous scenarios: {len(self.state.previous_scenarios)} "
                f"(available for comparison)"
            )

        if self.state.cached_clusters:
            parts.append(
                f"Cached: {len(self.state.cached_clusters)} clusters for "
                f"treatment {self.state.cached_treatmentid} at {self.state.cached_radius_km}km "
                f"(reusable for alpha-only changes)"
            )

        return "\n".join(parts) if parts else "No active session."
