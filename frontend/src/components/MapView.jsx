import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { getContourColor } from '../utils/contourColors';

import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png';
import markerIcon from 'leaflet/dist/images/marker-icon.png';
import markerShadow from 'leaflet/dist/images/marker-shadow.png';
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconUrl: markerIcon,
  iconRetinaUrl: markerIcon2x,
  shadowUrl: markerShadow,
});

// Selection size limits — matches design.md §8 constants
const MIN_DEG = 0.01;
const MAX_DEG = 0.15;

const POND_ICON = L.divIcon({
  className: '',
  html: `<div style="
    width: 16px; height: 16px; border-radius: 50%;
    background: #49A3C1; border: 2px solid #fff;
  "></div>`,
  iconSize: [16, 16],
  iconAnchor: [8, 8],
});

function getBboxValidity(anchor, corner) {
  if (!anchor || !corner) return 'none';
  const dLon = Math.abs(corner.lng - anchor.lng);
  const dLat = Math.abs(corner.lat - anchor.lat);
  if (dLon < MIN_DEG || dLat < MIN_DEG) return 'too_small';
  if (dLon > MAX_DEG || dLat > MAX_DEG) return 'too_large';
  return 'valid';
}

function getRectStyle(validity) {
  if (validity === 'valid') {
    return { color: '#49A3C1', weight: 2, dashArray: null, fillColor: '#1E6091', fillOpacity: 0.10 };
  }
  if (validity === 'too_small' || validity === 'too_large') {
    return { color: '#EF4444', weight: 2, dashArray: null, fill: false };
  }
  // drawing / not yet confirmed
  return { color: '#49A3C1', weight: 1.5, dashArray: '6 4', fill: false };
}

export default function MapView({ result, landResult, mode, loading, onBboxConfirm, onSelectionUpdate, selectionBbox, selectedCandidate, onCandidateSelect }) {
  const mapRef = useRef(null);
  const mapInstanceRef = useRef(null);
  const resultLayersRef = useRef([]);
  const landLayersRef = useRef([]);
  const selectionRectRef = useRef(null);
  const anchorRef = useRef(null);
  const drawStateRef = useRef('idle');

  // Initialize map once
  useEffect(() => {
    if (mapInstanceRef.current) return;
    const map = L.map(mapRef.current, {
      center: [21.25, 81.29],
      zoom: 13,
      zoomControl: true,
    });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© OpenStreetMap',
      maxZoom: 19,
    }).addTo(map);
    mapInstanceRef.current = map;
  }, []);

  // Rectangle draw interaction (map mode)
  useEffect(() => {
    const map = mapInstanceRef.current;
    if (!map) return;

    const handleClick = (e) => {
      if (loading) return;

      if (drawStateRef.current === 'idle') {
        // First corner
        anchorRef.current = e.latlng;
        drawStateRef.current = 'drawing';
        onSelectionUpdate?.(e.latlng, null); // signal drawing started
      } else {
        // Second corner — finalize
        const anchor = anchorRef.current;
        const corner = e.latlng;
        const validity = getBboxValidity(anchor, corner);

        if (selectionRectRef.current) map.removeLayer(selectionRectRef.current);
        selectionRectRef.current = L.rectangle(
          [[Math.min(anchor.lat, corner.lat), Math.min(anchor.lng, corner.lng)],
           [Math.max(anchor.lat, corner.lat), Math.max(anchor.lng, corner.lng)]],
          getRectStyle(validity)
        ).addTo(map);

        onSelectionUpdate?.(anchor, corner); // notify App of confirmed selection
        anchorRef.current = null;
        drawStateRef.current = 'idle';
      }
    };

    const handleMouseMove = (e) => {
      if (drawStateRef.current !== 'drawing' || loading) return;
      const anchor = anchorRef.current;
      if (!anchor) return;

      const corner = e.latlng;
      const validity = getBboxValidity(anchor, corner);

      if (selectionRectRef.current) map.removeLayer(selectionRectRef.current);
      selectionRectRef.current = L.rectangle(
        [[Math.min(anchor.lat, corner.lat), Math.min(anchor.lng, corner.lng)],
         [Math.max(anchor.lat, corner.lat), Math.max(anchor.lng, corner.lng)]],
        getRectStyle(validity === 'none' ? 'drawing' : validity)
      ).addTo(map);
    };

    if (mode === 'map') {
      map.on('click', handleClick);
      map.on('mousemove', handleMouseMove);
      map.getContainer().style.cursor = 'crosshair';
    } else {
      map.off('click', handleClick);
      map.off('mousemove', handleMouseMove);
      map.getContainer().style.cursor = '';
      if (selectionRectRef.current) {
        map.removeLayer(selectionRectRef.current);
        selectionRectRef.current = null;
      }
      anchorRef.current = null;
      drawStateRef.current = 'idle';
    }

    return () => {
      map.off('click', handleClick);
      map.off('mousemove', handleMouseMove);
    };
  }, [mode, loading, onSelectionUpdate]);


  // After analysis completes — change selection rect to persistent style (solid, no fill)
  useEffect(() => {
    const map = mapInstanceRef.current;
    if (!map || !result || !selectionRectRef.current) return;
    // Transition rect to post-analysis style: solid thin outline, no fill
    const bounds = selectionRectRef.current.getBounds();
    map.removeLayer(selectionRectRef.current);
    selectionRectRef.current = L.rectangle(bounds, {
      color: '#49A3C1',
      weight: 1.5,
      dashArray: null,
      fill: false,
    }).addTo(map);
  }, [result]);

  // Result layers (catchment, contours, pond marker, candidate markers)
  useEffect(() => {
    const map = mapInstanceRef.current;
    if (!map) return;

    resultLayersRef.current.forEach((l) => map.removeLayer(l));
    resultLayersRef.current = [];

    if (!result) return;

    // --- Contours (KML mode) ---
    if (result.contours_geojson) {
      const elevations = result.contours_geojson.features.map(f => f.properties.elevation_m);
      const minE = Math.min(...elevations);
      const maxE = Math.max(...elevations);
      const contourLayer = L.geoJSON(result.contours_geojson, {
        style: (feature) => ({
          color: getContourColor(feature.properties.elevation_m, minE, maxE),
          weight: 1.5,
          opacity: 0.8,
        }),
        onEachFeature: (feature, layer) => {
          layer.bindTooltip(`${feature.properties.elevation_m} m`, { sticky: true });
        },
      }).addTo(map);
      resultLayersRef.current.push(contourLayer);
    }

    // --- Candidates (map mode) ---
    const candidates = result.candidates || [];
    const activeCandidateIdx = (selectedCandidate ?? 1) - 1;  // 0-based

    const RANK_STYLES = [
      { color: '#F59E0B', label: '(1) Site 1 (Best)', border: '#92400E' },
      { color: '#94A3B8', label: '(2) Site 2',        border: '#475569' },
      { color: '#CD7F32', label: '(3) Site 3',        border: '#78350F' },
    ];

    candidates.forEach((cand, i) => {
      const style = RANK_STYLES[i] || RANK_STYLES[2];
      const isActive = i === activeCandidateIdx;

      // Catchment boundary — only show active one fully, others faintly
      const catchLayer = L.geoJSON(cand.boundary_geojson, {
        style: isActive
          ? { color: '#1E6091', weight: 2.5, fillColor: '#1E6091', fillOpacity: 0.25 }
          : { color: '#94A3B8', weight: 1,   fillColor: '#94A3B8', fillOpacity: 0.08 },
      }).addTo(map);
      resultLayersRef.current.push(catchLayer);

      // Candidate marker
      const icon = L.divIcon({
        className: '',
        html: `<div style="
          width:${isActive ? 22 : 16}px; height:${isActive ? 22 : 16}px;
          border-radius:50%; background:${style.color};
          border:2px solid ${style.border};
          box-shadow: 0 1px 4px rgba(0,0,0,0.5);
          cursor:pointer;
          display:flex; align-items:center; justify-content:center;
          font-size:9px; font-weight:700; color:#fff;
        ">${cand.rank}</div>`,
        iconSize: [isActive ? 22 : 16, isActive ? 22 : 16],
        iconAnchor: [isActive ? 11 : 8, isActive ? 11 : 8],
      });

      // Build popup content: location + catchment + water volume
      const runoffVol  = result.runoff?.annual_runoff_m3;
      const rainfall   = result.rainfall?.annual_avg_mm;
      const isEstimated = result.rainfall?.source === 'regional_estimate';
      const popupHtml  = `
        <div style="min-width:180px; font-size:13px; line-height:1.6">
          <b style="font-size:14px">${style.label}</b><br/>
          <span style="color:#555">📍 ${cand.lat.toFixed(5)}°N, ${cand.lon.toFixed(5)}°E</span><br/>
          <hr style="margin:4px 0; border-color:#ddd"/>
          🗺️ <b>Catchment area:</b> ${cand.area_sq_km} km²<br/>
          ${runoffVol != null
            ? `💧 <b>Annual water volume:</b> ${Math.round(runoffVol).toLocaleString()} m³<br/>`
            : ''}
          ${rainfall != null
            ? `🌧️ <b>Avg rainfall:</b> ${Math.round(rainfall)} mm/yr${isEstimated ? ' <i style="color:#888">(~est.)</i>' : ''}`
            : ''}
        </div>`;

      const marker = L.marker([cand.lat, cand.lon], { icon })
        .bindPopup(popupHtml, { maxWidth: 240 })
        .on('click', () => onCandidateSelect?.(cand.rank))
        .addTo(map);
      resultLayersRef.current.push(marker);
    });

    // Fallback: if no candidates (KML mode), render single pond location
    if (candidates.length === 0 && result.pond_location) {
      if (result.catchment?.boundary_geojson) {
        const catchLayer = L.geoJSON(result.catchment.boundary_geojson, {
          style: { color: '#1E6091', weight: 2, fillColor: '#1E6091', fillOpacity: 0.25 },
        }).addTo(map);
        resultLayersRef.current.push(catchLayer);
      }
      const runoffVolKml = result.runoff?.annual_runoff_m3;
      const rainfallKml  = result.rainfall?.annual_avg_mm;
      const marker = L.marker(
        [result.pond_location.lat, result.pond_location.lon],
        { icon: POND_ICON }
      )
        .bindPopup(`
          <div style="min-width:180px; font-size:13px; line-height:1.6">
            <b style="font-size:14px">🏆 Recommended Pond Site</b><br/>
            <span style="color:#555">📍 ${result.pond_location.lat.toFixed(5)}°N, ${result.pond_location.lon.toFixed(5)}°E</span><br/>
            <hr style="margin:4px 0; border-color:#ddd"/>
            🗺️ <b>Catchment area:</b> ${result.catchment.area_sq_km} km²<br/>
            ${runoffVolKml != null ? `💧 <b>Annual water volume:</b> ${Math.round(runoffVolKml).toLocaleString()} m³<br/>` : ''}
            ${rainfallKml  != null ? `🌧️ <b>Avg rainfall:</b> ${Math.round(rainfallKml)} mm/yr` : ''}
          </div>`, { maxWidth: 240 })
        .addTo(map);
      resultLayersRef.current.push(marker);
    }

    // --- Zoom to the selected bbox (full analyzed area), not just the catchment ---
    if (selectionBbox?.bbox) {
      const [lonMin, latMin, lonMax, latMax] = selectionBbox.bbox;
      map.fitBounds([[latMin, lonMin], [latMax, lonMax]], { padding: [40, 40] });
    } else if (result.contours_geojson && resultLayersRef.current.length > 0) {
      // KML mode: zoom to contours
      const b = L.latLngBounds();
      resultLayersRef.current.forEach(l => { try { b.extend(l.getBounds()); } catch {} });
      if (b.isValid()) map.fitBounds(b, { padding: [30, 30] });
    }
  }, [result, selectedCandidate]);

  // Open land overlay
  useEffect(() => {
    const map = mapInstanceRef.current;
    if (!map) return;

    landLayersRef.current.forEach((l) => map.removeLayer(l));
    landLayersRef.current = [];

    if (!landResult?.open_land_areas?.features?.length &&
        !landResult?.built_up_areas?.features?.length) return;

    // Open land — lime green
    if (landResult?.open_land_areas?.features?.length) {
      const landLayer = L.geoJSON(landResult.open_land_areas, {
        style: { color: '#84CC16', weight: 1.5, fillColor: '#84CC16', fillOpacity: 0.25 },
        onEachFeature: (feature, layer) => {
          const a = feature.properties?.area_m2;
          if (a != null)
            layer.bindTooltip(
              `Open land: ${a >= 10000 ? (a / 10000).toFixed(2) + ' ha' : a.toLocaleString() + ' m²'}`,
              { sticky: true }
            );
        },
      }).addTo(map);
      landLayersRef.current.push(landLayer);
    }

    // Built-up areas — muted red, 25% fill
    if (landResult?.built_up_areas?.features?.length) {
      const builtLayer = L.geoJSON(landResult.built_up_areas, {
        style: { color: '#B45309', weight: 1, fillColor: '#EF4444', fillOpacity: 0.25 },
        onEachFeature: (_, layer) => {
          layer.bindTooltip('Built-up area (excluded from pond siting)', { sticky: true });
        },
      }).addTo(map);
      landLayersRef.current.push(builtLayer);
    }
  }, [landResult]);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <div ref={mapRef} style={{ width: '100%', height: '100%' }} />

      {/* Floating Proceed button — appears when a valid rectangle is drawn, hidden after analysis */}
      {mode === 'map' && selectionBbox && selectionBbox.valid && !loading && !result && (
        <div className="proceed-bar">
          <span className="proceed-hint">
            {`${selectionBbox.lonSpan.toFixed(3)}° × ${selectionBbox.latSpan.toFixed(3)}° selected`}
          </span>
          <button className="btn-proceed" onClick={() => onBboxConfirm(selectionBbox.bbox)}>
            Proceed to Analysis
          </button>
        </div>
      )}

      {/* Size validation message */}
      {mode === 'map' && selectionBbox && !selectionBbox.valid && selectionBbox.reason && !result && (
        <div className="proceed-bar proceed-bar--error">
          <span>{selectionBbox.reason}</span>
        </div>
      )}

      {/* Status hint at bottom */}
      {mode === 'map' && !loading && !selectionBbox && (
        <div className="map-status-hint">Click to set first corner of analysis area</div>
      )}
      {mode === 'map' && loading && (
        <div className="map-status-hint">Fetching terrain data...</div>
      )}
    </div>
  );
}
