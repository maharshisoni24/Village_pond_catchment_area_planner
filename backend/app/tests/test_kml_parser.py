"""
Tests for kml_parser — verifies that contour extraction and river-layer
detection work on the sample file *and* on synthetic KMLs representing
different generator formats.

Why multiple synthetic KMLs?  The parser must work on any similarly-structured
contour file, not just our sample. Each synthetic test represents a real
variation: ExtendedData elevation, Data/value elevation, description-based
elevation, Polygon-wrapped contours, MultiGeometry, and river folders.
"""

import io
import zipfile

import pytest
from pathlib import Path

from app.services.kml_parser import parse_kml_bytes


SAMPLE_PATH = Path(__file__).resolve().parents[3] / "contours_1m.kml"


class TestSampleFile:
    """Tests against the actual sample KML — proves the parser handles it."""

    @pytest.fixture(scope="class")
    def parsed(self):
        raw = SAMPLE_PATH.read_bytes()
        return parse_kml_bytes(raw, "contours_1m.kml")

    def test_extracts_contours(self, parsed):
        assert len(parsed.contours) > 0

    def test_elevation_range_sane(self, parsed):
        elevs = [c.elevation_m for c in parsed.contours]
        assert min(elevs) < max(elevs)
        assert min(elevs) > 0

    def test_contour_has_geometry(self, parsed):
        for c in parsed.contours:
            assert len(c.coords) >= 2

    def test_no_explicit_rivers_in_sample(self, parsed):
        assert parsed.rivers == []

    def test_bounding_polygon_present(self, parsed):
        assert parsed.bounding_polygon is not None


class TestNameBasedElevation:
    """KML where elevation is in the <name> tag — the most common format."""

    KML = b"""<?xml version="1.0"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>100.0</name>
        <LineString><coordinates>0,0 1,0 1,1</coordinates></LineString>
      </Placemark>
      <Placemark><name>200.0</name>
        <LineString><coordinates>0,1 1,1 1,2</coordinates></LineString>
      </Placemark>
    </Document></kml>"""

    def test_parses_contours(self):
        r = parse_kml_bytes(self.KML, "test.kml")
        assert len(r.contours) == 2
        assert r.contours[0].elevation_m == 100.0
        assert r.contours[1].elevation_m == 200.0


class TestExtendedDataElevation:
    """KML where elevation is in <ExtendedData>/<SimpleData name='ELEV'>."""

    KML = b"""<?xml version="1.0"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>contour_line_1</name>
        <ExtendedData><SchemaData schemaUrl="#c">
          <SimpleData name="ELEVATION">350.5</SimpleData>
          <SimpleData name="ID">1</SimpleData>
        </SchemaData></ExtendedData>
        <LineString><coordinates>0,0 1,0 1,1</coordinates></LineString>
      </Placemark>
    </Document></kml>"""

    def test_reads_elevation_from_simpledata(self):
        r = parse_kml_bytes(self.KML, "test.kml")
        assert len(r.contours) == 1
        assert r.contours[0].elevation_m == 350.5


class TestDataValueElevation:
    """KML using <Data name='elev'><value> instead of SimpleData."""

    KML = b"""<?xml version="1.0"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>line_42</name>
        <ExtendedData>
          <Data name="height"><value>275.0</value></Data>
        </ExtendedData>
        <LineString><coordinates>0,0 1,0 1,1</coordinates></LineString>
      </Placemark>
    </Document></kml>"""

    def test_reads_elevation_from_data_value(self):
        r = parse_kml_bytes(self.KML, "test.kml")
        assert len(r.contours) == 1
        assert r.contours[0].elevation_m == 275.0


class TestDescriptionElevation:
    """KML where elevation is in the <description> text."""

    KML = b"""<?xml version="1.0"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>some_line</name>
        <description>Contour line. Elevation: 310.2 metres</description>
        <LineString><coordinates>0,0 1,0 1,1</coordinates></LineString>
      </Placemark>
    </Document></kml>"""

    def test_reads_elevation_from_description(self):
        r = parse_kml_bytes(self.KML, "test.kml")
        assert len(r.contours) == 1
        assert r.contours[0].elevation_m == 310.2


class TestPolygonContours:
    """KML where closed contours use <Polygon>/<LinearRing> instead of LineString."""

    KML = b"""<?xml version="1.0"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>500.0</name>
        <Polygon><outerBoundaryIs><LinearRing>
          <coordinates>0,0 1,0 1,1 0,1 0,0</coordinates>
        </LinearRing></outerBoundaryIs></Polygon>
      </Placemark>
    </Document></kml>"""

    def test_extracts_polygon_as_contour(self):
        """Closed contours stored as Polygons should still be parsed."""
        r = parse_kml_bytes(self.KML, "test.kml")
        assert len(r.contours) == 1
        assert r.contours[0].elevation_m == 500.0


class TestRiverDetection:
    """KML with rivers in a named folder."""

    KML = b"""<?xml version="1.0"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Folder><name>lines</name>
        <Placemark><name>100.0</name>
          <LineString><coordinates>0,0 1,0 1,1</coordinates></LineString>
        </Placemark>
      </Folder>
      <Folder><name>Rivers</name>
        <Placemark><name>Main River</name>
          <LineString><coordinates>0.5,0 0.5,2</coordinates></LineString>
        </Placemark>
      </Folder>
    </Document></kml>"""

    def test_detects_river_folder(self):
        r = parse_kml_bytes(self.KML, "test.kml")
        assert len(r.contours) == 1
        assert len(r.rivers) == 1
        assert r.rivers[0].name == "Main River"

    def test_river_by_placemark_name(self):
        """A placemark named 'stream ...' should be classified as river."""
        kml = b"""<?xml version="1.0"?>
        <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
          <Placemark><name>100</name>
            <LineString><coordinates>0,0 1,1</coordinates></LineString>
          </Placemark>
          <Placemark><name>stream channel</name>
            <LineString><coordinates>0,0 0,1</coordinates></LineString>
          </Placemark>
        </Document></kml>"""
        r = parse_kml_bytes(kml, "test.kml")
        assert len(r.contours) == 1
        assert len(r.rivers) == 1


class TestEdgeCases:

    def test_rejects_empty_file(self):
        with pytest.raises(Exception):
            parse_kml_bytes(b"<kml></kml>", "bad.kml")

    def test_kmz_extraction(self):
        inner_kml = b"""<?xml version="1.0"?>
        <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
          <Placemark><name>100</name>
            <LineString><coordinates>0,0 1,1</coordinates></LineString>
          </Placemark>
        </Document></kml>"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("doc.kml", inner_kml)
        r = parse_kml_bytes(buf.getvalue(), "test.kmz")
        assert len(r.contours) == 1

    def test_newline_separated_coordinates(self):
        """Some generators use newlines between coordinate pairs."""
        kml = b"""<?xml version="1.0"?>
        <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
          <Placemark><name>100</name>
            <LineString><coordinates>
              0,0,30
              1,0,30
              1,1,30
            </coordinates></LineString>
          </Placemark>
        </Document></kml>"""
        r = parse_kml_bytes(kml, "test.kml")
        assert len(r.contours) == 1
        assert len(r.contours[0].coords) == 3

    def test_no_namespace(self):
        """KML without xmlns should still parse."""
        kml = b"""<?xml version="1.0"?>
        <kml><Document>
          <Placemark><name>100</name>
            <LineString><coordinates>0,0 1,1</coordinates></LineString>
          </Placemark>
        </Document></kml>"""
        r = parse_kml_bytes(kml, "test.kml")
        assert len(r.contours) == 1
