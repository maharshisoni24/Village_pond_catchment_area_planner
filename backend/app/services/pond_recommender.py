"""
Runoff volume estimation and pond dimension recommendation.

Uses the Rational Method (simplified):
    Runoff_volume = C × P × A
where C = runoff coefficient, P = rainfall depth (m), A = catchment area (m²).

Pond sizing targets storing 30–50% of annual runoff (per PRD.md §7).
"""

from __future__ import annotations

# Default runoff coefficient for semi-arid Indian terrain with moderate slope.
# ponytail: single global default — upgrade to land-use-based lookup if needed.
DEFAULT_RUNOFF_COEFFICIENT = 0.35


def estimate_runoff(
    catchment_area_sq_km: float,
    annual_rainfall_mm: float,
    runoff_coefficient: float = DEFAULT_RUNOFF_COEFFICIENT,
) -> dict:
    """
    Estimate annual runoff volume from catchment area and rainfall.

    Returns
    -------
    dict with:
        annual_runoff_m3   – estimated annual runoff in cubic metres
        runoff_coefficient – the C value used
    """
    area_m2 = catchment_area_sq_km * 1e6
    rainfall_m = annual_rainfall_mm / 1000.0
    runoff_m3 = runoff_coefficient * rainfall_m * area_m2

    return {
        "annual_runoff_m3": round(runoff_m3, 1),
        "runoff_coefficient": runoff_coefficient,
    }


def recommend_pond(
    annual_runoff_m3: float,
    storage_fraction: float = 0.40,
    max_depth_m: float = 5.0,
) -> dict:
    """
    Recommend pond dimensions from estimated runoff.

    Targets storing `storage_fraction` of annual runoff (default 40%).
    Assumes a simple rectangular pond for volume calculation.

    Returns
    -------
    dict with:
        target_volume_m3   – how much water the pond should hold
        recommended_depth_m – capped at max_depth_m
        surface_area_m2    – pond surface area = volume / depth
        storage_fraction   – fraction of annual runoff stored
    """
    target_volume = storage_fraction * annual_runoff_m3
    # Depth: at least 1.5m (practical minimum), capped at max_depth_m
    depth = min(max_depth_m, max(1.5, target_volume ** (1 / 3)))
    surface_area = target_volume / depth if depth > 0 else 0

    return {
        "target_volume_m3": round(target_volume, 1),
        "recommended_depth_m": round(depth, 1),
        "surface_area_m2": round(surface_area, 1),
        "storage_fraction": storage_fraction,
    }
