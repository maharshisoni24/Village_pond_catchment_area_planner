"""
KML/KMZ parser — extracts contour geometry + elevation values, and any
explicitly-tagged river/stream layers.

Designed to work generically across contour KMLs from different generators
(ContourMapGenerator, Google Earth Pro exports, QGIS, Global Mapper, etc.),
not just the sample file.

Elevation extraction strategy (tried in order per placemark):
  1. <name> tag is a bare number (e.g. "280.0")
  2. <ExtendedData>/<SchemaData>/<SimpleData> with elevation-related field name
  3. <ExtendedData>/<Data>/<value> with elevation-related field name
  4. <description> containing a number labelled as elevation/contour

Geometry support: LineString, LinearRing (from Polygon contours), and
MultiGeometry wrappers — all common contour representations.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree
from shapely.geometry import LineString, Polygon

from app.core.config import RIVER_KEYWORDS

# Keywords (case-insensitive) that identify elevation fields in ExtendedData
_ELEV_FIELD_KEYWORDS = ("elev", "height", "contour", "z_value", "altitude")

# Pattern to find elevation in <description> text, e.g. "Elevation: 280.5 m"
_DESC_ELEV_RE = re.compile(
    r"(?:elevation|elev|contour|height|altitude)\s*[:=]\s*(-?\d+\.?\d*)",
    re.IGNORECASE,
)


@dataclass
class ContourLine:
    """One contour polyline with its elevation."""
    elevation_m: float
    coords: list[tuple[float, float]]  # [(lon, lat), ...]
    geometry: LineString = field(repr=False)


@dataclass
class RiverGeometry:
    """An explicitly-tagged river/stream feature from the KML."""
    name: str
    geometry: LineString
    source: str = "explicit_kml_layer"


@dataclass
class ParseResult:
    """Everything the parser hands to the rest of the pipeline."""
    contours: list[ContourLine]
    rivers: list[RiverGeometry]
    bounding_polygon: Polygon | None  # area outline, if present


def parse_kml_bytes(raw: bytes, filename: str = "") -> ParseResult:
    """
    Parse KML or KMZ bytes into contour lines + optional river geometry.

    Why bytes?  FastAPI gives us the uploaded file as bytes; accepting bytes
    avoids writing to disk.
    """
    xml_bytes = _extract_kml_xml(raw, filename)
    root = etree.fromstring(xml_bytes)

    # Strip namespace prefixes so XPath works regardless of xmlns declarations
    _strip_ns(root)

    contours: list[ContourLine] = []
    rivers: list[RiverGeometry] = []
    bounding_polygon: Polygon | None = None

    for placemark in root.iter("Placemark"):
        name = _placemark_name(placemark)

        # --- Bounding polygon ---
        # Some generators include an area outline as a Polygon placemark
        # with a non-numeric name like "land".  Numeric-named Polygons are
        # closed contours and should fall through to contour extraction.
        poly_coords_el = placemark.find(".//Polygon//coordinates")
        if poly_coords_el is not None and poly_coords_el.text:
            if not _looks_numeric(name):
                coords = _parse_coords(poly_coords_el.text)
                if len(coords) >= 3:
                    bounding_polygon = Polygon(coords)
                continue

        # --- Extract all line geometries from this placemark ---
        # Handles: <LineString>, <Polygon>/<LinearRing> used as contours,
        # and <MultiGeometry> wrappers around any of the above.
        line_coords_list = _extract_line_coords(placemark)
        if not line_coords_list:
            continue  # skip points / non-line placemarks (labels, etc.)

        # Check if this placemark (or its parent folder) is a river
        is_river = _is_river(placemark, name)

        # Try to read elevation
        elev = _parse_elevation(placemark, name)

        for coords in line_coords_list:
            if len(coords) < 2:
                continue
            line = LineString(coords)

            if is_river:
                rivers.append(RiverGeometry(
                    name=name or "unnamed_river", geometry=line
                ))
            elif elev is not None:
                contours.append(ContourLine(
                    elevation_m=elev, coords=coords, geometry=line
                ))

    if not contours:
        raise ValueError(
            "No contour lines with parseable elevation values found in the file. "
            "Expected Placemarks with numeric <name> tags (e.g. '280.0'), "
            "or elevation in ExtendedData/description, with LineString geometry."
        )

    return ParseResult(contours=contours, rivers=rivers, bounding_polygon=bounding_polygon)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_kml_xml(raw: bytes, filename: str) -> bytes:
    """
    If the input is a KMZ (zip archive), extract the first .kml inside it.
    Otherwise return the bytes as-is.
    """
    if filename.lower().endswith(".kmz") or (len(raw) >= 4 and raw[:4] == b"PK\x03\x04"):
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_names:
                raise ValueError("KMZ archive contains no .kml file")
            return zf.read(kml_names[0])
    return raw


def _strip_ns(root: etree._Element) -> None:
    """Remove all XML namespaces in-place so we can use simple tag names."""
    for el in root.iter():
        if isinstance(el.tag, str) and "{" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
        # Clean up lxml objectify pytype attributes
        for attr_key in list(el.attrib):
            if "{" in attr_key:
                el.attrib.pop(attr_key, None)


def _placemark_name(pm: etree._Element) -> str | None:
    """Get the text of the <name> child, stripped."""
    n = pm.find("name")
    if n is not None and n.text:
        return n.text.strip()
    return None


def _looks_numeric(name: str | None) -> bool:
    """Check if a name looks like a number (i.e. likely an elevation contour)."""
    if not name:
        return False
    try:
        float(name)
        return True
    except ValueError:
        return False


def _extract_line_coords(pm: etree._Element) -> list[list[tuple[float, float]]]:
    """
    Extract all line geometries from a placemark, handling:
    - <LineString>
    - <Polygon> / <LinearRing> (some generators use polygons for closed contours)
    - <MultiGeometry> wrapping any of the above

    Returns a list of coordinate lists (one per sub-geometry).
    """
    results = []

    # Direct LineStrings
    for ls in pm.iterfind(".//LineString/coordinates"):
        if ls.text:
            coords = _parse_coords(ls.text)
            if coords:
                results.append(coords)

    # LinearRings inside Polygons (closed contours from some generators)
    # Only grab the outer boundary — inner rings are holes, not contour lines
    for lr in pm.iterfind(".//Polygon/outerBoundaryIs/LinearRing/coordinates"):
        if lr.text:
            coords = _parse_coords(lr.text)
            if coords:
                results.append(coords)

    # Standalone LinearRings (rare, but possible)
    for lr in pm.iterfind(".//LinearRing/coordinates"):
        if lr.text:
            coords = _parse_coords(lr.text)
            if coords and coords not in results:  # avoid duplicates from Polygon parse above
                results.append(coords)

    return results


def _parse_coords(text: str) -> list[tuple[float, float]]:
    """
    Parse a KML <coordinates> text blob into (lon, lat) tuples.

    KML spec: 'lon,lat[,alt] lon,lat[,alt] ...'
    Some generators use newlines instead of spaces as separators — handle both.
    """
    coords = []
    # Split on any whitespace (spaces, newlines, tabs)
    for token in text.strip().split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                coords.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue  # skip malformed coordinate tokens
    return coords


def _parse_elevation(pm: etree._Element, name: str | None) -> float | None:
    """
    Try to extract a numeric elevation from a Placemark.

    Strategy (in order):
    1. <name> tag is a number (e.g. "280.0") — common case.
    2. <SimpleData> in <SchemaData>/<ExtendedData> with elevation-related name.
    3. <Data> in <ExtendedData> with elevation-related name.
    4. <description> contains text like "Elevation: 280.5".

    Returns None if no elevation can be parsed (placemark is skipped).
    """
    # 1. Name is numeric
    if name:
        try:
            return float(name)
        except ValueError:
            pass

    # 2. SimpleData (used by SchemaData-based KMLs)
    for sd in pm.iterfind(".//SimpleData"):
        attr_name = (sd.get("name") or "").lower()
        if any(kw in attr_name for kw in _ELEV_FIELD_KEYWORDS):
            try:
                return float(sd.text)
            except (TypeError, ValueError):
                pass

    # 3. Data/value (used by simpler ExtendedData KMLs)
    for data in pm.iterfind(".//Data"):
        attr_name = (data.get("name") or "").lower()
        if any(kw in attr_name for kw in _ELEV_FIELD_KEYWORDS):
            val_el = data.find("value")
            if val_el is not None and val_el.text:
                try:
                    return float(val_el.text.strip())
                except ValueError:
                    pass

    # 4. Description text
    desc = pm.findtext("description") or ""
    match = _DESC_ELEV_RE.search(desc)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    return None


def _is_river(pm: etree._Element, name: str | None) -> bool:
    """
    Decide whether a Placemark represents a river/stream rather than a contour.

    Checks the placemark name *and* its ancestor folder names against known
    river keywords — so a file that puts rivers in a "Rivers" or "Streams"
    folder is handled automatically without needing a specific folder name.
    """
    # Check placemark name
    if name and any(kw in name.lower() for kw in RIVER_KEYWORDS):
        return True

    # Walk up to parent Folder(s) and check their names
    parent = pm.getparent()
    while parent is not None:
        if parent.tag == "Folder":
            folder_name = parent.findtext("name") or ""
            if any(kw in folder_name.lower() for kw in RIVER_KEYWORDS):
                return True
        parent = parent.getparent()

    return False
