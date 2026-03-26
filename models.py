from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


# ── Enums ─────────────────────────────────────────────────────────────

class TechnologyType(str, Enum):
    direct_combustion = "direct_combustion"
    gasification = "gasification"
    pyrolysis = "pyrolysis"


# ── TEA ───────────────────────────────────────────────────────────────

class TEARequest(BaseModel):
    capacity_kw: float = Field(..., description="Facility capacity in kW")
    technology: TechnologyType = TechnologyType.direct_combustion

class TEAResponse(BaseModel):
    capacity_kw: float
    technology: str
    efficiency: float
    capacity_factor: float
    demand_bdt_per_year: float
    demand_bdt_per_day: float


# ── Regional Summary ──────────────────────────────────────────────────

class RegionalRequest(BaseModel):
    county_name: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None

class RegionalResponse(BaseModel):
    county_name: Optional[str] = "Unknown"
    n_clusters: int
    total_biomass_bdt: float
    avg_harvest_cost: float
    min_harvest_cost: float
    max_harvest_cost: float
    avg_slope: float
    avg_burn_prob: float
    avg_cf: float
    pct_ground_feasible: float


# ── Radius Estimation ─────────────────────────────────────────────────

class RadiusRequest(BaseModel):
    lat: float
    lng: float
    demand_bdt: float
    treatmentid: int = 1
    buffer_multiplier: float = 1.5

class RadiusResponse(BaseModel):
    radius_km: float
    available_biomass_bdt: float
    demand_bdt: float
    n_clusters_in_radius: int


# ── Cluster Retrieval ─────────────────────────────────────────────────

class ClusterData(BaseModel):
    cluster_no: int
    treatmentid: int
    best_system: str
    harvest_cost: float
    total_biomass_bdt: float
    center_lat: float
    center_lng: float
    landing_lat: float
    landing_lng: float
    slope: float
    burn_probability: float
    cf_estimate: float
    county_name: Optional[str] = None
    land_use: Optional[str] = None  

class RetrieveRequest(BaseModel):
    lat: float
    lng: float
    radius_km: float
    treatmentid: int = 1
    harvest_system: Optional[str] = None  # None = auto-select best (default)
    max_slope: Optional[float] = None     # Optional constraint

class RetrieveResponse(BaseModel):
    n_clusters: int
    clusters: list[ClusterData]
    facility_lat: float
    facility_lng: float


# ── Supply Curve ──────────────────────────────────────────────────────

class SupplyCurveRequest(BaseModel):
    clusters: list[ClusterData]
    facility_lat: float
    facility_lng: float
    demand_bdt: float
    alpha: float = Field(1.0, ge=0.0, le=1.0, description="Cost weight. 1.0=cost only, 0.0=fire only")

class SelectedCluster(BaseModel):
    cluster_no: int
    harvest_cost: float
    transport_cost: float
    delivered_cost: float
    biomass_bdt: float
    cumulative_biomass: float
    score: float
    burn_probability: float
    distance_km: float

class SupplyCurveResponse(BaseModel):
    demand_bdt: float
    alpha: float
    n_selected: int
    n_candidates: int
    avg_delivered_cost: float
    marginal_delivered_cost: float
    total_biomass_selected: float
    fire_reduction_ratio: float  # compared to cost-only baseline
    selected_clusters: list[SelectedCluster]
    avg_harvest_cost: float = 0.0
    avg_transport_cost: float = 0.0
    transport_params_used: dict = {}



# ── Conversation State ────────────────────────────────────────────────

class FacilityParams(BaseModel):
    lat: float
    lng: float
    capacity_kw: float
    technology: TechnologyType = TechnologyType.direct_combustion
    demand_bdt: float = 0.0

class ScenarioResult(BaseModel):
    treatment: str
    treatmentid: int
    harvest_system: Optional[str] = None  # None = auto-selected
    alpha: float
    radius_km: float
    avg_cost: float
    marginal_cost: float
    fire_reduction: float
    n_clusters_selected: int
    total_biomass: float

class ConversationState(BaseModel):
    """Maintained across turns for delta reporting and refinements."""
    facility: Optional[FacilityParams] = None
    current_scenario: Optional[ScenarioResult] = None
    previous_scenarios: list[ScenarioResult] = []
    # Cache the retrieved clusters so alpha-only changes skip re-retrieval
    cached_clusters: list[ClusterData] = []
    cached_treatmentid: Optional[int] = None
    cached_radius_km: Optional[float] = None
