import requests

def geocode_location(place_name: str, state: str = "California") -> dict:
    """
    Convert a place name to lat/lng using Nominatim (OpenStreetMap).
    Returns {"lat": float, "lng": float, "display_name": str} or {"error": str}
    """
    query = f"{place_name}, {state}"
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "json", "limit": 1},
        headers={"User-Agent": "FRREDSS-FRED/1.0"}
    )
    results = resp.json()
    if not results:
        return {"error": f"Could not find location: {place_name}"}
    r = results[0]
    return {
        "lat": float(r["lat"]),
        "lng": float(r["lon"]),
        "display_name": r["display_name"],
    }