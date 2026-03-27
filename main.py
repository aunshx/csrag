"""
CS-RAG API Server

Enhanced /chat endpoint returns both text response AND spatial data
so the frontend can render clusters, facility pin, and radius on the map.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import asyncio, json
from asyncio import Queue
from pydantic import BaseModel
from typing import Optional

from agent.orchestrator import run_agent
from agent.state import StateManager
from tools.tea import estimate_demand
from tools.regional import get_regional_summary
from tools.radius import estimate_radius
from tools.retrieve import retrieve_clusters
from tools.compose import build_supply_curve
from models import TEAResponse, RegionalResponse, RadiusResponse

app = FastAPI(
    title="CS-RAG API",
    description="Compositional Spatial RAG for Forest Biomass Decision Support",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

sessions: dict[str, dict] = {}
tool_event_queues: dict[str, Queue] = {}


def _get_tool_queue(session_id: str) -> Queue:
    if session_id not in tool_event_queues:
        tool_event_queues[session_id] = Queue()
    return tool_event_queues[session_id]


def _emit_tool_event(session_id: str, event: dict):
    """Non-blocking emit of a tool event to the session queue."""
    try:
        queue = _get_tool_queue(session_id)
        queue.put_nowait(event)
    except Exception:
        pass


def _get_session(session_id: str) -> dict:
    if session_id not in sessions:
        sessions[session_id] = {
            "state": StateManager(),
            "history": [],
        }
    return sessions[session_id]


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    # Optional: frontend can pass map state so agent knows current view
    facility_lat: Optional[float] = None
    facility_lng: Optional[float] = None
    radius_km: Optional[float] = None

class ClusterGeoData(BaseModel):
    cluster_no: int
    lat: float
    lng: float
    harvest_cost: float
    delivered_cost: Optional[float] = None
    biomass_bdt: float
    burn_probability: float
    score: Optional[float] = None
    selected: bool = False
    best_system: Optional[str] = None

class CandidateMarker(BaseModel):
    lat: float
    lng: float
    label: str
    county_name: str
    score: float
    reasoning: str

class HexGeoData(BaseModel):
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
    county_name: str
    score: float
    highlighted: bool = False
    boundary: list[list[float]] = []

class YearData(BaseModel):
    year: int
    status: str
    avg_delivered_cost: float
    avg_harvest_cost: float = 0
    avg_transport_cost: float = 0
    effective_radius_km: float
    n_selected: int
    selected_cluster_nos: list[int] = []

class SpatialState(BaseModel):
    facility_lat: Optional[float] = None
    facility_lng: Optional[float] = None
    radius_km: Optional[float] = None
    clusters: list[ClusterGeoData] = []
    candidates: list[CandidateMarker] = []
    hexes: list[HexGeoData] = []         
    hex_resolution: Optional[int] = None  
    alpha: Optional[float] = None
    treatmentid: Optional[int] = None
    n_selected: Optional[int] = None
    avg_cost: Optional[float] = None
    fire_reduction: Optional[float] = None
    active_counties: list[str] = []
    multi_year: list[YearData] = []
    multi_year_ready: bool = False  
    multi_year_n_years: int = 10 

class ChatResponse(BaseModel):
    response: str
    session_id: str
    spatial: Optional[SpatialState] = None

@app.get("/status/{session_id}")
async def tool_status_stream(session_id: str):
    """
    SSE stream of tool-call events. Frontend subscribes during loading
    to show live tool progress (like Claude's thinking blocks).
    """
    queue = _get_tool_queue(session_id)

    async def generate():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=90.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "done":
                    # Clean up queue
                    tool_event_queues.pop(session_id, None)
                    break
            except asyncio.TimeoutError:
                yield 'data: {"type": "ping"}\n\n'

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """
    Conversational agent endpoint.
    
    Returns both text response AND spatial data for map rendering.
    The spatial field contains facility location, radius, and all
    clusters with their selection status and scores.
    """
    session = _get_session(req.session_id)
    state: StateManager = session["state"]

    # If frontend sends map coordinates, update state
    if req.facility_lat and req.facility_lng:
        if state.state.facility is None:
            state.set_facility(
                lat=req.facility_lat,
                lng=req.facility_lng,
                capacity_kw=0.0,
            )
        else:
            state.state.facility.lat = req.facility_lat
            state.state.facility.lng = req.facility_lng

    state._hex_result = None
    state._candidate_locations = []

    try:
        def emit(event: dict):
            _emit_tool_event(req.session_id, event)

        response_text, updated_history = run_agent(
            user_message=req.message,
            state=state,
            conversation_history=session["history"],
            emit=emit,
        )
        session["history"] = updated_history

        # Build spatial state from current session
        spatial = _build_spatial_state(state)

        # Signal frontend that tool calls are done
        _emit_tool_event(req.session_id, {"type": "done"})

        return ChatResponse(
            response=response_text,
            session_id=req.session_id,
            spatial=spatial.model_dump() if spatial else None,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


def _build_spatial_state(state: StateManager) -> Optional[SpatialState]:
    try:
        facility = state.state.facility
        scenario = state.state.current_scenario
        cached = state.state.cached_clusters

        if not facility or (facility.lat == 0.0 and facility.lng == 0.0):
            raw_candidates = getattr(state, '_candidate_locations', [])
            raw_hexes = getattr(state, '_hex_result', None)
            if not raw_candidates and not raw_hexes:
                return None

        cluster_geos = []
        selected_cluster_nos = set()

        if scenario and hasattr(state, '_last_supply_result') and state._last_supply_result:
            for sc in state._last_supply_result.selected_clusters:
                selected_cluster_nos.add(sc.cluster_no)

        for c in cached:
            is_selected = c.cluster_no in selected_cluster_nos

            score = None
            delivered_cost = None
            if state._last_supply_result:
                for sc in state._last_supply_result.selected_clusters:
                    if sc.cluster_no == c.cluster_no:
                        score = sc.score
                        delivered_cost = sc.delivered_cost
                        break

            cluster_geos.append(ClusterGeoData(
                cluster_no=c.cluster_no,
                lat=c.center_lat,
                lng=c.center_lng,
                harvest_cost=round(c.harvest_cost, 2),
                delivered_cost=delivered_cost,
                biomass_bdt=round(c.total_biomass_bdt, 1),
                burn_probability=c.burn_probability,
                score=score,
                selected=is_selected,
                best_system=c.best_system,
            ))
        candidate_markers = []
        raw_candidates = getattr(state, '_candidate_locations', [])
        for i, cand in enumerate(raw_candidates):
            candidate_markers.append(CandidateMarker(
                lat=cand["lat"],
                lng=cand["lng"],
                label=f"Option {i+1}",
                county_name=cand.get("county_name", "Unknown"),
                score=cand.get("score", 0),
                reasoning=cand.get("reasoning", ""),
            ))
        active_counties = list(set(
            c.county_name for c in cached 
            if c.county_name
        ))

        hex_geos = []
        raw_hexes = getattr(state, '_hex_result', None)
        if raw_hexes:
            for h in raw_hexes.all_hexes:
                hex_geos.append(HexGeoData(
                    hex_id=h.hex_id,
                    resolution=h.resolution,
                    lat=h.lat,
                    lng=h.lng,
                    n_clusters=h.n_clusters,
                    total_biomass=h.total_biomass,
                    avg_cost=h.avg_cost,
                    min_cost=h.min_cost,
                    avg_fire=h.avg_fire,
                    avg_slope=h.avg_slope,
                    pct_ground=h.pct_ground,
                    county_name=h.county_name,
                    score=h.score,
                    highlighted=h.highlighted,
                    boundary=h.boundary,
                ))

        multi_year_data = []
        multi_year_ready = getattr(state, '_multi_year_ready', False)

        return SpatialState(
            facility_lat=facility.lat if facility and facility.lat != 0.0 else None,
            facility_lng=facility.lng if facility and facility.lng != 0.0 else None,
            radius_km=state.state.cached_radius_km,
            clusters=cluster_geos,
            candidates=candidate_markers,
            alpha=scenario.alpha if scenario else None,
            treatmentid=scenario.treatmentid if scenario else None,
            n_selected=scenario.n_clusters_selected if scenario else None,
            avg_cost=scenario.avg_cost if scenario else None,
            fire_reduction=scenario.fire_reduction if scenario else None,
            active_counties=active_counties,
            hexes=hex_geos,
            hex_resolution=raw_hexes.resolution if raw_hexes else None,
            multi_year=multi_year_data,
            multi_year_ready=multi_year_ready,
            multi_year_n_years=getattr(state, '_multi_year_n_years', 10)
        )

    except Exception as e:
        print(f"[SPATIAL STATE ERROR] {e}")
        import traceback
        traceback.print_exc()
        return None

@app.delete("/chat/{session_id}")
def clear_session(session_id: str):
    if session_id in sessions:
        del sessions[session_id]
    return {"status": "cleared", "session_id": session_id}


@app.post("/multi-year/stream")
async def multi_year_stream(session_id: str = "default"):
    session = _get_session(session_id)
    state = session["state"]
    clusters = state.state.cached_clusters
    facility = state.state.facility

    if not clusters or not facility:
        raise HTTPException(400, "No cached clusters. Run analysis first.")

    demand = facility.demand_bdt
    alpha = getattr(state, '_multi_year_alpha', 1.0)
    n_years = getattr(state, '_multi_year_n_years', 10)
    regrowth = getattr(state, '_multi_year_regrowth', 0.0)

    state._multi_year_ready = False
    treatmentid = state.state.cached_treatmentid or 1  

    async def generate():
        from tools.multi_year import project_multi_year_streaming
        async for year_result in project_multi_year_streaming(
            clusters=clusters,
            facility_lat=facility.lat,
            facility_lng=facility.lng,
            demand_bdt=demand,
            alpha=alpha,
            n_years=n_years,
            regrowth_rate=regrowth,
            treatmentid=treatmentid,
            initial_radius_km=state.state.cached_radius_km,
        ):
            yield f"data: {json.dumps(year_result)}\n\n"
            await asyncio.sleep(0)
        yield 'data: {"done": true}\n\n'

    return StreamingResponse(generate(), media_type="text/event-stream")

class TEARequest(BaseModel):
    capacity_kw: float
    technology: str = "direct_combustion"

@app.post("/tools/tea", response_model=TEAResponse)
def tool_tea(req: TEARequest):
    return estimate_demand(req.capacity_kw, req.technology)


class RegionalRequest(BaseModel):
    county_name: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None

@app.post("/tools/regional", response_model=list[RegionalResponse])
def tool_regional(req: RegionalRequest):
    return get_regional_summary(req.county_name, req.lat, req.lng)


class RadiusRequest(BaseModel):
    lat: float
    lng: float
    demand_bdt: float
    treatmentid: int = 1

@app.post("/tools/radius", response_model=RadiusResponse)
def tool_radius(req: RadiusRequest):
    return estimate_radius(req.lat, req.lng, req.demand_bdt, req.treatmentid)


class RetrieveRequest(BaseModel):
    lat: float
    lng: float
    radius_km: float
    treatmentid: int = 1
    harvest_system: Optional[str] = None
    max_slope: Optional[float] = None

@app.post("/tools/retrieve")
def tool_retrieve(req: RetrieveRequest):
    result = retrieve_clusters(
        req.lat, req.lng, req.radius_km,
        req.treatmentid, req.harvest_system, req.max_slope
    )
    return {
        "n_clusters": result.n_clusters,
        "sample": [c.model_dump() for c in result.clusters[:10]],
    }


# ── Health check ──────────────────────────────────────────────────────

@app.get("/health")
def health():
    from config import get_db_connection
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM mv_cluster_supply LIMIT 1")
            count = cur.fetchone()[0]
        conn.close()
        return {"status": "ok", "mv_cluster_supply_rows": count}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/cache/stats")
def cache_stats():
    from tools.cache import tool_cache
    return tool_cache.stats()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)