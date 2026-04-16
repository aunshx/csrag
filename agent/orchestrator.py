"""
Agent Orchestrator

Connects to Claude API, injects system prompt + conversation state,
dispatches tool calls to the appropriate functions, and manages
the multi-turn tool-use loop.

Enhanced: stores supply curve results on state so main.py can
extract cluster geometries for map rendering.
"""

import json
import anthropic
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, TREATMENTS
from agent.system_prompt import SYSTEM_PROMPT, TOOL_DEFINITIONS
from agent.state import StateManager
from models import ScenarioResult, ClusterData
from tools.tea import estimate_demand
from tools.regional import get_regional_summary
from tools.radius import estimate_radius
from tools.retrieve import retrieve_clusters
from tools.compose import build_supply_curve
from tools.cache import tool_cache
from anthropic import Anthropic
import os
client = Anthropic(
    api_key=os.environ["RESPAN_API_KEY"],
    base_url=os.environ["RESPAN_CLAUDE_URL"],
)

# Legacy Anthropic client - Uncomment later after Respan is removed later and remove the above client initialization
# Also, update the .env file to remove Respan keys and add back ANTHROPIC_API_KEY and ANTHROPIC_MODEL if needed
# and update the imports at the top of this file to remove Respan and use Anthropic instead
# Finally, change the session_id parameter in run_agent calls in main.py to use a fixed string
# client = Anthropic(api_key=ANTHROPIC_API_KEY)


TOOL_TTL = {
    "estimate_demand": 86400,       # 24 hours (pure math)
    "get_regional_summary": 3600,   # 1 hour
    "find_best_locations": 3600,    # 1 hour
    "estimate_radius": 1800,        # 30 minutes
    "retrieve_clusters": 1800,      # 30 minutes
}


TOOL_LABELS = {
    "geocode_location":     "Looking up location coordinates",
    "estimate_demand":      "Calculating biomass demand",
    "get_regional_summary": "Getting regional data",
    "find_best_locations":  "Scanning California for optimal sites",
    "estimate_radius":      "Estimating procurement radius",
    "retrieve_clusters":    "Retrieving forest clusters",
    "build_supply_curve":   "Building supply curve",
    "analyze_tradeoffs":    "Analyzing fire-cost tradeoffs",
    "project_multi_year":   "Setting up multi-year projection",
}

def _dispatch_tool(
    tool_name: str,
    tool_input: dict,
    state: StateManager,
    emit=None,
) -> str:
    """
    Execute a tool call and return the result as a JSON string.
    Also updates conversation state as needed.
    """
    print(f"[TOOL] {tool_name}: {json.dumps(tool_input)[:200]}")
    print(f"[STATE] facility={state.state.facility}, cached={len(state.state.cached_clusters)}")

    label = TOOL_LABELS.get(tool_name, tool_name)
    # Build human-readable detail from inputs
    detail_parts = []
    if "lat" in tool_input and "lng" in tool_input:
        detail_parts.append(f"{tool_input['lat']:.3f}°N, {abs(tool_input['lng']):.3f}°W")
    if "radius_km" in tool_input:
        detail_parts.append(f"{tool_input['radius_km']} km radius")
    if "demand_bdt" in tool_input:
        detail_parts.append(f"{tool_input['demand_bdt']:,.0f} BDT")
    if "n_years" in tool_input:
        detail_parts.append(f"{tool_input['n_years']} years")
    if "alpha" in tool_input and tool_input["alpha"] < 1.0:
        detail_parts.append(f"α={tool_input['alpha']}")
    if "county_name" in tool_input and tool_input["county_name"]:
        detail_parts.append(tool_input["county_name"])

    if emit:
        emit({"type": "tool_start", "tool": tool_name, "label": label,
              "detail": ", ".join(detail_parts) if detail_parts else ""})

    if tool_name == "estimate_demand":
        # Map GPO/CHP/GP → TechnologyType enum values in models.py
        _TECH_MAP = {"GPO": "direct_combustion", "CHP": "direct_combustion", "GP": "gasification"}
        _tech = _TECH_MAP.get(str(tool_input.get("technology", "GPO")).upper(), "direct_combustion")

        cache_params = {
            "capacity_kw": tool_input["capacity_kw"],
            "technology": tool_input.get("technology", "GPO"),
        }
        cached = tool_cache.get("estimate_demand", ttl_seconds=TOOL_TTL["estimate_demand"], **cache_params)
        if cached:
            result_data = json.loads(cached)
            if state.state.facility is None:
                state.set_facility(
                    lat=0.0, lng=0.0,
                    capacity_kw=tool_input["capacity_kw"],
                    technology=_tech,
                    demand_bdt=result_data["demand_bdt_per_year"],
                )
            else:
                state.state.facility.capacity_kw = tool_input["capacity_kw"]
                state.state.facility.demand_bdt = result_data["demand_bdt_per_year"]
            return cached

        result = estimate_demand(
            capacity_kw=tool_input["capacity_kw"],
            technology=tool_input.get("technology", "GPO"),
        )
        if state.state.facility is None:
            state.set_facility(
                lat=0.0, lng=0.0,
                capacity_kw=tool_input["capacity_kw"],
                technology=_tech,
                demand_bdt=result.demand_bdt_per_year,
            )
        else:
            state.state.facility.capacity_kw = tool_input["capacity_kw"]
            state.state.facility.demand_bdt = result.demand_bdt_per_year

        result_json = result.model_dump_json()
        tool_cache.set("estimate_demand", result_json, ttl_seconds=TOOL_TTL["estimate_demand"], **cache_params)
        return result_json

    elif tool_name == "get_regional_summary":
        cache_params = {
            "county_name": tool_input.get("county_name"),
            "lat": tool_input.get("lat"),
            "lng": tool_input.get("lng"),
        }
        cached = tool_cache.get("get_regional_summary", ttl_seconds=TOOL_TTL["get_regional_summary"], **cache_params)
        if cached:
            return cached

        results = get_regional_summary(
            county_name=tool_input.get("county_name"),
            lat=tool_input.get("lat"),
            lng=tool_input.get("lng"),
        )
        result_json = json.dumps([r.model_dump() for r in results], default=str)
        tool_cache.set("get_regional_summary", result_json, ttl_seconds=TOOL_TTL["get_regional_summary"], **cache_params)
        return result_json

    elif tool_name == "estimate_radius":
        state._hex_result = None
        state._candidate_locations = []

        cache_params = {
            "lat": tool_input["lat"],
            "lng": tool_input["lng"],
            "demand_bdt": tool_input["demand_bdt"],
            "treatmentid": tool_input.get("treatmentid", 1),
        }
        cached = tool_cache.get("estimate_radius", ttl_seconds=TOOL_TTL["estimate_radius"], **cache_params)

        # Still need to update facility state even on cache hit
        if state.state.facility is not None:
            state.state.facility.lat = tool_input["lat"]
            state.state.facility.lng = tool_input["lng"]
        else:
            state.set_facility(
                lat=tool_input["lat"],
                lng=tool_input["lng"],
                capacity_kw=0.0,
                demand_bdt=tool_input["demand_bdt"],
            )

        if cached:
            return cached

        result = estimate_radius(
            lat=tool_input["lat"],
            lng=tool_input["lng"],
            demand_bdt=tool_input["demand_bdt"],
            treatmentid=tool_input.get("treatmentid", 1),
        )
        result_json = result.model_dump_json()
        tool_cache.set("estimate_radius", result_json, ttl_seconds=TOOL_TTL["estimate_radius"], **cache_params)
        return result_json

    elif tool_name == "retrieve_clusters":
        state._hex_result = None
        state._candidate_locations = []

        treatmentid = tool_input.get("treatmentid", 1)
        radius_km = tool_input["radius_km"]

        cache_params = {
            "lat": tool_input["lat"],
            "lng": tool_input["lng"],
            "radius_km": radius_km,
            "treatmentid": treatmentid,
            "harvest_system": tool_input.get("harvest_system"),
            "max_slope": tool_input.get("max_slope"),
        }

        # Update facility state
        if state.state.facility is not None:
            state.state.facility.lat = tool_input["lat"]
            state.state.facility.lng = tool_input["lng"]
        else:
            state.set_facility(
                lat=tool_input["lat"],
                lng=tool_input["lng"],
                capacity_kw=0.0,
            )

        if state.can_reuse_cache(treatmentid, radius_km):
            print(f"[STATE CACHE] Reusing {len(state.state.cached_clusters)} cached clusters")
            cached = tool_cache.get("retrieve_clusters", ttl_seconds=TOOL_TTL["retrieve_clusters"], **cache_params)
            if cached:
                return cached

        result = retrieve_clusters(
            lat=tool_input["lat"],
            lng=tool_input["lng"],
            radius_km=radius_km,
            treatmentid=treatmentid,
            harvest_system=tool_input.get("harvest_system"),
            max_slope=tool_input.get("max_slope"),
        )

        state.cache_clusters(result.clusters, treatmentid, radius_km)

        summary = {
            "n_clusters": result.n_clusters,
            "facility_lat": result.facility_lat,
            "facility_lng": result.facility_lng,
            "sample_clusters": [
                c.model_dump() for c in result.clusters[:5]
            ] if result.clusters else [],
        }
        result_json = json.dumps(summary)
        tool_cache.set("retrieve_clusters", result_json, ttl_seconds=TOOL_TTL["retrieve_clusters"], **cache_params)
        return result_json

    elif tool_name == "build_supply_curve":
        clusters = state.state.cached_clusters
        if not clusters:
            return json.dumps({"error": "No clusters cached. Call retrieve_clusters first."})

        facility = state.state.facility
        if not facility or (facility.lat == 0.0 and facility.lng == 0.0):
            return json.dumps({"error": "Facility location not set."})

        try:
            result = build_supply_curve(
                clusters=clusters,
                facility_lat=facility.lat,
                facility_lng=facility.lng,
                demand_bdt=tool_input["demand_bdt"],
                alpha=tool_input.get("alpha", 1.0),
                transport_params=tool_input.get("transport_params"),  
            )
            print(f"[SUPPLY] n_selected={result.n_selected}, avg_cost={result.avg_delivered_cost}, fire={result.fire_reduction_ratio}")

            # Store for spatial extraction by main.py
            state.set_supply_result(result)

        except Exception as e:
            print(f"[SUPPLY ERROR] {e}")
            return json.dumps({"error": f"Supply curve failed: {str(e)}"})

        treatmentid = state.state.cached_treatmentid or 1
        new_scenario = ScenarioResult(
            treatment=TREATMENTS.get(treatmentid, f"Treatment {treatmentid}"),
            treatmentid=treatmentid,
            harvest_system=None,
            alpha=tool_input.get("alpha", 1.0),
            radius_km=state.state.cached_radius_km or 0,
            avg_cost=result.avg_delivered_cost,
            marginal_cost=result.marginal_delivered_cost,
            fire_reduction=result.fire_reduction_ratio,
            n_clusters_selected=result.n_selected,
            total_biomass=result.total_biomass_selected,
        )

        delta = state.get_delta_report(new_scenario)
        state.set_scenario(new_scenario)

        summary = {
            "demand_bdt": result.demand_bdt,
            "alpha": result.alpha,
            "n_selected": result.n_selected,
            "n_candidates": result.n_candidates,
            "avg_delivered_cost": result.avg_delivered_cost,
            "avg_harvest_cost": result.avg_harvest_cost,       
            "avg_transport_cost": result.avg_transport_cost,   
            "marginal_delivered_cost": result.marginal_delivered_cost,
            "total_biomass_selected": result.total_biomass_selected,
            "fire_reduction_ratio": result.fire_reduction_ratio,
            "transport_params": result.transport_params_used,  
        }
        if delta:
            summary["delta_from_previous"] = delta

        return json.dumps(summary)
        
    elif tool_name == "find_best_locations":
        if state.state.facility:
            state.state.facility.lat = 0.0
            state.state.facility.lng = 0.0

        cache_params = {
            "priority": tool_input.get("priority", "balanced"),
            "resolution": tool_input.get("resolution", 4),
            "parent_hex": tool_input.get("parent_hex"),
            "min_capacity_mw": tool_input.get("min_capacity_mw", 25),
            "technology": tool_input.get("technology", "direct_combustion"),
            "n_results": tool_input.get("n_results", 3),
        }

        # Always run the function (needed to populate state for map rendering)
        from tools.locations import find_best_locations
        result = find_best_locations(**{k: v for k, v in cache_params.items() if v is not None})

        state._hex_result = result
        state._candidate_locations = [
            {"lat": c.lat, "lng": c.lng, "county_name": c.county_name,
             "score": c.score, "reasoning": f"{c.total_biomass:,.0f} BDT, "
             f"${c.avg_cost}/GT avg cost, {c.pct_ground:.0f}% ground-accessible. "
             f"{c.county_name} County."}
            for c in result.top_candidates
        ]

        result_json = json.dumps({
            "resolution": result.resolution,
            "n_hexes_total": result.n_hexes,
            "top_candidates": [c.model_dump() for c in result.top_candidates],
            "parent_hex": result.parent_hex,
        })
        return result_json

    elif tool_name == "analyze_tradeoffs":
        from tools.tradeoffs import analyze_tradeoffs
        clusters = state.state.cached_clusters
        facility = state.state.facility
        if not clusters or not facility:
            return json.dumps({"error": "Run retrieve_clusters first"})
        result = analyze_tradeoffs(
            clusters=clusters,
            facility_lat=facility.lat,
            facility_lng=facility.lng,
            demand_bdt=tool_input.get("demand_bdt", facility.demand_bdt),
        )
        return json.dumps(result)

    elif tool_name == "project_multi_year":
        clusters = state.state.cached_clusters
        facility = state.state.facility
        if not clusters or not facility:
            return json.dumps({"error": "Run retrieve_clusters first"})

        # Store params on state -- actual computation happens via SSE stream
        state._multi_year_ready = True
        state._multi_year_n_years = tool_input.get("n_years", 10)
        state._multi_year_alpha = tool_input.get("alpha", 1.0)
        state._multi_year_regrowth = tool_input.get("regrowth_rate", 0.0)

        return json.dumps({
            "status": "ready",
            "n_years": state._multi_year_n_years,
            "n_clusters_in_pool": len(clusters),
            "message": "Multi-year projection ready to stream."
        })
    else:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})


def run_agent(
    user_message: str,
    state: StateManager,
    conversation_history: list[dict] = None,
    emit=None,
    session_id: str = "fred-ai",
) -> tuple[str, list[dict]]:
    """Run one turn of the agent loop."""
    if conversation_history is None:
        conversation_history = []

    if not user_message.strip():
        return "I didn't catch that. Could you try again?", conversation_history

    state_context = state.to_context_string()
    full_system = SYSTEM_PROMPT
    if state_context != "No active session.":
        full_system += f"\n\n## Current Session State\n{state_context}"

    conversation_history.append({
        "role": "user",
        "content": user_message,
    })

    for _ in range(10):
        try:
            response = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": full_system,
                        "cache_control": {"type": "ephemeral"}
                    }
                ],                
                tools=TOOL_DEFINITIONS,
                messages=conversation_history,
                extra_headers={
                    "X-Thread-Identifier": session_id,
                    "X-Customer-Identifier": "fred-ai",
                }
            )
        except anthropic.BadRequestError as e:
            if "tool_use" in str(e) and "tool_result" in str(e):
                # Strip orphaned tool_use blocks left by a previous failed request
                cleaned = []
                for i, msg in enumerate(conversation_history):
                    if msg["role"] == "assistant":
                        content = msg.get("content", [])
                        if isinstance(content, list):
                            has_tool_use = any(
                                b.get("type") == "tool_use" for b in content
                                if isinstance(b, dict)
                            )
                            next_msg = conversation_history[i + 1] if i + 1 < len(conversation_history) else None
                            has_result = next_msg and isinstance(next_msg.get("content"), list) and any(
                                b.get("type") == "tool_result" for b in next_msg["content"]
                                if isinstance(b, dict)
                            )
                            if has_tool_use and not has_result:
                                continue
                    cleaned.append(msg)
                conversation_history[:] = cleaned
                continue
            raise
        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                import time
                time.sleep(5)
                continue
            raise
        except Exception as e:
            if "prompt is too long" in str(e):
                return "The conversation got too large to continue. Please start a new session.", conversation_history
            raise


        if response.stop_reason == "tool_use":
            assistant_content = response.content
            conversation_history.append({
                "role": "assistant",
                "content": [block.model_dump() for block in assistant_content],
            })

            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    result_str = _dispatch_tool(
                        block.name, block.input, state, emit=emit
                    )
                    if emit:
                        # Emit done event with brief result summary
                        try:
                            result_data = json.loads(result_str)
                            done_detail = ""
                            if "n_clusters" in result_data:
                                done_detail = f"{result_data['n_clusters']:,} clusters"
                            elif "radius_km" in result_data:
                                done_detail = f"R* = {result_data['radius_km']} km"
                            elif "demand_bdt_per_year" in result_data:
                                done_detail = f"{result_data['demand_bdt_per_year']:,.0f} BDT/year"
                            elif "n_selected" in result_data:
                                done_detail = f"{result_data['n_selected']:,} selected, ${result_data.get('avg_delivered_cost', 0):.2f}/GT"
                            elif "status" in result_data and result_data["status"] == "ready":
                                done_detail = f"{result_data.get('n_clusters_in_pool', 0):,} clusters ready"
                            emit({"type": "tool_done", "tool": block.name, "detail": done_detail})
                        except Exception:
                            emit({"type": "tool_done", "tool": block.name, "detail": ""})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

            conversation_history.append({
                "role": "user",
                "content": tool_results,
            })

        else:
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text

            conversation_history.append({
                "role": "assistant",
                "content": text,
            })

            return text, conversation_history

    return "I ran into an issue processing that request. Could you try rephrasing?", conversation_history