"""
Techno-Economic Assessment - Demand Estimation

Converts facility capacity + FRREDSS technology into annual biomass demand.

Technologies match FRREDSS TEA models exactly (from thesis Ch3 Table 3.14-3.16):
  GPO: Generic Power Only (conventional combustion/boiler-steam cycle)
       Net station efficiency: 20%, Capacity factor: 80%
  CHP: Combined Heat and Power (cogeneration)
       Net electric efficiency: 20%, Thermal efficiency: 60%, Capacity factor: 85%
  GP:  Gasification Power (thermal gasification)
       Net station efficiency: 23%, Capacity factor: 80%

Demand calculation follows FRREDSS methodology:
  annual_generation_kWh = capacity_kW × capacity_factor × 8760
  fuel_energy_needed_kWh = annual_generation_kWh / net_efficiency
  demand_BDT = fuel_energy_needed_kWh / HHV_kWh_per_green_ton
  
HHV at 50% moisture (FRREDSS default):
  18,608 kJ/kg_dry × 0.5 dry_fraction × 907.185 kg/short_ton / 3600 kJ/kWh
  ≈ 2,345 kWh/green_ton
"""

from models import TEAResponse

# FRREDSS TEA technology parameters (Table 5 of FRREDSS 2.0 paper)
TECHNOLOGY_PARAMS = {
    "GPO": {
        "label": "Generic Power Only",
        "description": "Conventional combustion/boiler-steam cycle",
        "net_efficiency": 0.20,
        "capacity_factor": 0.80,
    },
    "CHP": {
        "label": "Combined Heat and Power",
        "description": "Cogeneration with thermal energy recovery",
        "net_efficiency": 0.20,      # electric
        "thermal_efficiency": 0.60,  # additional thermal output
        "capacity_factor": 0.85,
    },
    "GP": {
        "label": "Gasification Power",
        "description": "Thermal gasification with gas engine/prime mover",
        "net_efficiency": 0.23,
        "capacity_factor": 0.80,
    },
}

# Fuel heating value at 50% moisture (FRREDSS default: 18,608 kJ/kg dry)
# = 18,608 × (1 - 0.50) × 907.185 / 3600 kWh/green_short_ton
HHV_KWH_PER_GREEN_TON = 18608 * 0.50 * 907.185 / 3600  # ≈ 2,344.6 kWh/GT


def estimate_demand(
    capacity_kw: float,
    technology: str = "GPO",
    moisture_content: float = 50.0,
    capacity_factor: float = None,  # override in fraction (0.85), not percent
) -> TEAResponse:
    """
    Calculate annual biomass demand for a given facility.

    Args:
        capacity_kw: Net electrical capacity in kW (e.g. 15000 for 15MW)
        technology: FRREDSS model - "GPO", "CHP", or "GP"
        moisture_content: Biomass moisture % wet basis (default 50%)
        capacity_factor: Override capacity factor as fraction (default per technology)

    Returns:
        TEAResponse with demand_bdt_per_year, annual_generation_kwh, etc.
    """
    params = TECHNOLOGY_PARAMS.get(technology.upper())
    if not params:
        params = TECHNOLOGY_PARAMS["GPO"]
        technology = "GPO"

    net_eta = params["net_efficiency"]
    cap_factor = capacity_factor if capacity_factor is not None else params["capacity_factor"]

    # Annual electricity generation
    annual_generation_kwh = capacity_kw * cap_factor * 8760

    # Fuel energy required
    fuel_energy_kwh = annual_generation_kwh / net_eta

    # Biomass demand at given moisture content
    # Adjust HHV for actual moisture if different from 50%
    moisture_frac = moisture_content / 100.0
    hhv_kwh_per_bdmt = 18608 * 1000 / 3600  # kWh/BDMT
    demand_bdt = fuel_energy_kwh / hhv_kwh_per_bdmt

    # BDT per day
    demand_bdt_per_day = demand_bdt / 365.0

    # Flag if non-default values were used
    default_cf = params["capacity_factor"]
    default_mc = 50.0
    overrides = {}
    if abs(cap_factor - default_cf) > 0.001:
        overrides["capacity_factor"] = f"{cap_factor*100:.0f}% (default: {default_cf*100:.0f}%)"
    if abs(moisture_content - default_mc) > 0.1:
        overrides["moisture_content"] = f"{moisture_content:.0f}% (default: {default_mc:.0f}%)"

    # Base fields match existing TEAResponse model (efficiency = required field name)
    base = dict(
        technology=technology.upper(),
        capacity_kw=capacity_kw,
        efficiency=net_eta,
        capacity_factor=round(cap_factor, 4),
        annual_generation_kwh=round(annual_generation_kwh, 0),
        demand_bdt_per_year=round(demand_bdt, 0),
        moisture_content=moisture_content,
    )
    # Extra fields — only included if TEAResponse model supports them
    try:
        return TEAResponse(**base,
            technology_label=params["label"],
            capacity_mw=round(capacity_kw / 1000, 1),
            demand_bdt_per_day=round(demand_bdt_per_day, 1),
            parameter_overrides=overrides,
        )
    except Exception:
        return TEAResponse(**base)


if __name__ == "__main__":
    print("=== FRREDSS TEA Demand Estimates ===\n")
    for tech in ["GPO", "CHP", "GP"]:
        for mw in [10, 15, 25, 50]:
            r = estimate_demand(mw * 1000, tech)
            print(f"  {tech} {mw}MW: {r.demand_bdt_per_year:,.0f} BDT/yr "
                  f"({r.demand_bdt_per_day:.0f} BDT/day) | "
                  f"η={r.net_efficiency:.0%} CF={r.capacity_factor:.0%}")
        print()