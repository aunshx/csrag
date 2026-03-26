import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# ── Database ──────────────────────────────────────────────────────────
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "dbname": os.getenv("DB_NAME", "firegnn"),
    "user": os.getenv("DB_USER", "aunsh"),
    "password": os.getenv("DB_PASSWORD", ""),
}

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)


# ── Anthropic ─────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"


# ── TEA Parameters by Technology ──────────────────────────────────────
# η = conversion efficiency, CF = capacity factor, HHV in GJ/BDT
TEA_PARAMS = {
    "direct_combustion": {
        "label": "Direct Combustion (Stoker Boiler)",
        "efficiency": 0.25,
        "capacity_factor": 0.85,
        "hhv_gj_per_bdt": 18.0,
    },
    "gasification": {
        "label": "Gasification (Combined Cycle)",
        "efficiency": 0.35,
        "capacity_factor": 0.80,
        "hhv_gj_per_bdt": 18.0,
    },
    "pyrolysis": {
        "label": "Fast Pyrolysis",
        "efficiency": 0.30,
        "capacity_factor": 0.75,
        "hhv_gj_per_bdt": 18.0,
    },
}

DEFAULT_TECHNOLOGY = "direct_combustion"


# ── Transport ─────────────────────────────────────────────────────────
# Cost per BDT per km of road distance
TRANSPORT_RATE = float(os.getenv("TRANSPORT_RATE_PER_BDT_KM", 0.15))


# ── Spatial ───────────────────────────────────────────────────────────
# Buffer multiplier: retrieve this much more biomass than demand
# to allow selectivity in the supply curve
BIOMASS_BUFFER = 1.5

# Radius search bounds (km)
RADIUS_MIN_KM = 10
RADIUS_MAX_KM = 200
RADIUS_STEP_KM = 5


# ── Treatment mapping ─────────────────────────────────────────────────
TREATMENTS = {
    1:  "Complete Removal (Clearcut)",
    2:  "Crown Thin Light (TFA-20)",
    3:  "Crown Thin Moderate (TFA-40)",
    4:  "Crown Thin Heavy (TFA-60)",
    5:  "Crown Thin Severe (TFA-80)",
    6:  "Understory Thin Light (TFB-20)",
    7:  "Understory Thin Moderate (TFB-40)",
    8:  "Understory Thin Heavy (TFB-60)",
    9:  "Understory Thin Severe (TFB-80)",
    10: "SDI-30",
    11: "SDI-55",
}

# Treatment shorthand the agent and user might reference
TREATMENT_ALIASES = {
    "clearcut": 1, "clear cut": 1, "complete removal": 1,
    "tfa-20": 2, "tfa20": 2, "crown thin light": 2,
    "tfa-40": 3, "tfa40": 3, "crown thin moderate": 3,
    "tfa-60": 4, "tfa60": 4, "crown thin heavy": 4,
    "tfa-80": 5, "tfa80": 5, "crown thin severe": 5,
    "tfb-20": 6, "tfb20": 6, "understory thin light": 6,
    "tfb-40": 7, "tfb40": 7, "understory thin moderate": 7,
    "tfb-60": 8, "tfb60": 8, "understory thin heavy": 8,
    "tfb-80": 9, "tfb80": 9, "understory thin severe": 9,
    "sdi-30": 10, "sdi30": 10,
    "sdi-55": 11, "sdi55": 11,
}


# ── Harvesting Systems ────────────────────────────────────────────────
HARVEST_SYSTEMS = [
    "Ground-Based Mech WT",
    "Ground-Based Manual WT",
    "Ground-Based Manual Log",
    "Ground-Based CTL",
    "Cable Manual WT/Log",
    "Cable Manual WT",
    "Cable Manual Log",
    "Cable CTL",
    "Helicopter Manual WT",
    "Helicopter CTL",
]


# ── Normalization bounds (loaded once at startup) ─────────────────────
_norm_bounds = None

def get_normalization_bounds():
    """Load min/max for cost and burn_probability from the DB."""
    global _norm_bounds
    if _norm_bounds is not None:
        return _norm_bounds

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT metric, min_val, max_val FROM mv_normalization_bounds")
            rows = cur.fetchall()
            _norm_bounds = {}
            for metric, min_val, max_val in rows:
                _norm_bounds[metric] = {"min": float(min_val), "max": float(max_val)}
    finally:
        conn.close()
    return _norm_bounds
