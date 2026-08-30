"""
Tests for the full catchment pipeline — parser → DEM → flow → pond site.

Verifies end-to-end correctness against the sample file: the pipeline
should produce a sane catchment polygon and a pond site that isn't on a
detected river channel.
"""

import pytest
import numpy as np
from pathlib import Path

from app.services.kml_parser import parse_kml_bytes
from app.services.dem_builder import build_dem
from app.services.catchment import run_catchment_analysis
from app.services.river_detector import build_river_mask, nearest_river_distance_m


SAMPLE_PATH = Path(__file__).resolve().parents[3] / "contours_1m.kml"


@pytest.fixture(scope="module")
def pipeline_result():
    """Run the full pipeline once and share across tests (it's slow)."""
    raw = SAMPLE_PATH.read_bytes()
    parsed = parse_kml_bytes(raw, "contours_1m.kml")
    dem, meta = build_dem(parsed.contours)

    # First pass: get flow accumulation
    result = run_catchment_analysis(dem, meta)

    # River mask
    river_mask, method = build_river_mask(
        dem_shape=dem.shape,
        meta=meta,
        flow_accumulation=result["flow_accumulation"],
        explicit_rivers=parsed.rivers,
    )

    # Re-run with river exclusion
    result_excluded = run_catchment_analysis(dem, meta, river_mask=river_mask)

    return {
        "parsed": parsed,
        "dem": dem,
        "meta": meta,
        "result": result_excluded,
        "river_mask": river_mask,
        "river_method": method,
    }


def test_dem_has_valid_data(pipeline_result):
    """DEM should contain finite elevation values spanning a non-trivial range."""
    dem = pipeline_result["dem"]
    finite = dem[np.isfinite(dem)]
    assert len(finite) > 0
    assert finite.max() - finite.min() > 1.0, "DEM elevation range too flat"


def test_catchment_has_positive_area(pipeline_result):
    """Catchment area must be positive and reasonably sized."""
    area = pipeline_result["result"]["area_sq_km"]
    assert area > 0, "Catchment area is zero or negative"
    # Should be less than the entire file extent (~9 km²)
    assert area < 10.0, "Catchment area suspiciously large"


def test_catchment_is_valid_geojson(pipeline_result):
    """Catchment boundary should be a valid GeoJSON Polygon."""
    gj = pipeline_result["result"]["catchment_geojson"]
    assert gj["type"] in ("Polygon", "MultiPolygon")
    assert len(gj["coordinates"]) > 0


def test_pond_location_within_data_extent(pipeline_result):
    """Pond coordinates should fall inside the DEM bounds."""
    meta = pipeline_result["meta"]
    lat = pipeline_result["result"]["pond_lat"]
    lon = pipeline_result["result"]["pond_lon"]
    assert meta["y_min"] <= lat <= meta["y_max"]
    assert meta["x_min"] <= lon <= meta["x_min"] + meta["cell_size"] * meta["shape"][1]


def test_pond_not_on_river(pipeline_result):
    """The chosen pond site must NOT be on a river cell."""
    result = pipeline_result["result"]
    river_mask = pipeline_result["river_mask"]
    meta = pipeline_result["meta"]
    cell_size = meta["cell_size"]

    # Convert pond coords to row/col
    col = int((result["pond_lon"] - meta["transform"][0]) / cell_size)
    row = int((meta["transform"][3] - result["pond_lat"]) / cell_size)

    if 0 <= row < river_mask.shape[0] and 0 <= col < river_mask.shape[1]:
        assert not river_mask[row, col], "Pond site is on a river cell!"


def test_river_detection_method(pipeline_result):
    """Sample file has no explicit rivers — method should be derived."""
    assert pipeline_result["river_method"] == "derived_flow_accumulation"
