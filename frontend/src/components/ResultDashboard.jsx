// No API imports needed — GeoJSON download is built client-side from result.

export default function ResultDashboard({ result, landResult, file, selectedCandidate = 1, onCandidateSelect }) {
  if (!result) return null;

  const { terrain_stats, catchment, river_check, pond_location, rainfall, runoff, pond_recommendation } = result;
  const candidates = result.candidates || [];

  // Use selected candidate's data for pond location + catchment area if available
  const activeCand = candidates.find(c => c.rank === selectedCandidate);
  const displayLat   = activeCand?.lat     ?? pond_location.lat;
  const displayLon   = activeCand?.lon     ?? pond_location.lon;
  const displayArea  = activeCand?.area_sq_km ?? catchment.area_sq_km;

  const RANK_LABELS = ['Site 1 (Best)', 'Site 2', 'Site 3'];

  const handleDownloadJSON = () => {
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'analysis_result.json';
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDownloadGeoJSON = () => {
    // Build GeoJSON from the selected candidate (or fallback to primary result).
    const features = [];

    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [displayLon, displayLat] },
      properties: {
        type: 'pond_site',
        rank: selectedCandidate,
        elevation_min_m: terrain_stats.elevation_min_m,
        elevation_max_m: terrain_stats.elevation_max_m,
        dem_source: terrain_stats.dem_source || 'KML contours',
        river_detected: river_check.river_detected,
        nearest_river_m: river_check.nearest_river_distance_m ?? null,
        annual_runoff_m3: runoff?.annual_runoff_m3 ?? null,
      },
    });

    const boundaryGeo = activeCand?.boundary_geojson ?? catchment?.boundary_geojson;
    if (boundaryGeo) {
      features.push({
        type: 'Feature',
        geometry: boundaryGeo,
        properties: { type: 'catchment_boundary', area_sq_km: displayArea },
      });
    }

    const geojson = { type: 'FeatureCollection', features };
    const blob = new Blob([JSON.stringify(geojson, null, 2)], { type: 'application/geo+json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = file ? file.name.replace(/\.\w+$/, '_result.geojson') : 'pond_analysis.geojson';
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      {/* Candidate picker — only shown in map mode when backend returned candidates */}
      {candidates.length > 0 && (
        <>
          <div className="section-title">Candidate Sites</div>
          <div className="stat-row" style={{ flexDirection: 'column', gap: 6 }}>
            {candidates.map((cand, i) => (
              <button
                key={cand.rank}
                onClick={() => onCandidateSelect?.(cand.rank)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '8px 12px', borderRadius: 8, border: 'none',
                  cursor: 'pointer', textAlign: 'left', width: '100%',
                  background: cand.rank === selectedCandidate ? '#1E3A5F' : '#1a2535',
                  outline: cand.rank === selectedCandidate ? '2px solid #49A3C1' : 'none',
                  color: '#E2E8F0',
                }}
              >
                <span style={{ fontSize: 18 }}>{['(1)','(2)','(3)'][i]}</span>
                <span style={{ flex: 1 }}>
                  <span style={{ fontWeight: 600 }}>{RANK_LABELS[i]}</span>
                  <span style={{ marginLeft: 8, fontSize: 12, color: '#94A3B8' }}>
                    {cand.area_sq_km} km²
                  </span>
                </span>
                {cand.rank === selectedCandidate && (
                  <span style={{ fontSize: 11, color: '#49A3C1', fontWeight: 700 }}>ACTIVE</span>
                )}
              </button>
            ))}
          </div>
        </>
      )}

      <div className="section-title">Terrain</div>
      <div className="stat-row">
        <div className="stat-card">
          <div className="stat-label">Min Elevation</div>
          <div className="stat-value">{terrain_stats.elevation_min_m}<span className="stat-unit">m</span></div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Max Elevation</div>
          <div className="stat-value">{terrain_stats.elevation_max_m}<span className="stat-unit">m</span></div>
        </div>
      </div>
      <div className="stat-card">
        {terrain_stats.dem_source ? (
          <>
            <div className="stat-label">Data Source</div>
            <div className="stat-value" style={{ fontSize: 13 }}>{terrain_stats.dem_source}</div>
          </>
        ) : (
          <>
            <div className="stat-label">Contour Lines Used</div>
            <div className="stat-value">{terrain_stats.contour_lines_used.toLocaleString()}</div>
          </>
        )}
      </div>

      {/* Catchment */}
      <div className="section-title">Catchment</div>
      <div className="stat-card">
        <div className="stat-label">Catchment Area</div>
        <div className="stat-value">{displayArea}<span className="stat-unit">km²</span></div>
      </div>
      <div className="stat-card">
        <div className="stat-label">Pond Location</div>
        <div className="stat-value" style={{ fontSize: 14 }}>
          {displayLat.toFixed(5)}°N, {displayLon.toFixed(5)}°E
        </div>
      </div>

      {/* River Check */}
      <div className="section-title">River Check</div>
      <div className="stat-card">
        <div className="stat-label">River Detected</div>
        <div className="stat-value">
          {river_check.river_detected ? (
            <span className="tag tag-warning">{river_check.detection_method.replace(/_/g, ' ')}</span>
          ) : (
            <span className="tag tag-success">None detected</span>
          )}
        </div>
      </div>
      {river_check.nearest_river_distance_m != null && (
        <div className="stat-row">
          <div className="stat-card">
            <div className="stat-label">Pond on River</div>
            <div className="stat-value">
              <span className={`tag ${river_check.pond_site_on_river ? 'tag-warning' : 'tag-success'}`}>
                {river_check.pond_site_on_river ? 'Yes ⚠️' : 'No ✓'}
              </span>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Nearest River</div>
            <div className="stat-value">{river_check.nearest_river_distance_m}<span className="stat-unit">m</span></div>
          </div>
        </div>
      )}

      {/* Rainfall (if fetched) */}
      {rainfall && (
        <>
          <div className="section-title">Rainfall ({rainfall.years} yr avg)</div>
          <div className="stat-row">
            <div className="stat-card">
              <div className="stat-label">Annual</div>
              <div className="stat-value">{rainfall.annual_avg_mm}<span className="stat-unit">mm</span></div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Monsoon (Jun–Sep)</div>
              <div className="stat-value">{rainfall.monsoon_avg_mm}<span className="stat-unit">mm</span></div>
            </div>
          </div>
        </>
      )}

      {/* Runoff (if computed) */}
      {runoff && (
        <>
          <div className="section-title">Runoff Estimate</div>
          <div className="stat-card">
            <div className="stat-label">Annual Runoff (C={runoff.runoff_coefficient})</div>
            <div className="stat-value">{runoff.annual_runoff_m3.toLocaleString()}<span className="stat-unit">m³</span></div>
          </div>
        </>
      )}

      {/* Pond Recommendation */}
      {pond_recommendation && (
        <>
          <div className="section-title">Pond Recommendation</div>
          <div className="stat-row">
            <div className="stat-card">
              <div className="stat-label">Target Volume</div>
              <div className="stat-value">{pond_recommendation.target_volume_m3.toLocaleString()}<span className="stat-unit">m³</span></div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Depth</div>
              <div className="stat-value">{pond_recommendation.recommended_depth_m}<span className="stat-unit">m</span></div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Surface Area</div>
            <div className="stat-value">{pond_recommendation.surface_area_m2.toLocaleString()}<span className="stat-unit">m²</span></div>
          </div>
        </>
      )}

      {/* Open Land (if detected) */}
      {landResult && (
        <>
          <div className="section-title">Open Land (OpenCV)</div>
          <div className="stat-row">
            <div className="stat-card">
              <div className="stat-label">Open Patches</div>
              <div className="stat-value">{landResult.patch_count}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Total Area</div>
              <div className="stat-value">
                {landResult.total_area_m2 >= 10000
                  ? (landResult.total_area_m2 / 10000).toFixed(2)
                  : landResult.total_area_m2.toLocaleString()}
                <span className="stat-unit">
                  {landResult.total_area_m2 >= 10000 ? 'ha' : 'm²'}
                </span>
              </div>
            </div>
          </div>
          {landResult.built_up_areas?.features?.length > 0 && (
            <div className="stat-card">
              <div className="stat-label">Built-up Areas Excluded (OSM)</div>
              <div className="stat-value">{landResult.built_up_areas.features.length}</div>
            </div>
          )}
        </>
      )}

      {/* Downloads */}
      <div className="download-group">
        <button className="btn btn-outline btn-sm" onClick={handleDownloadJSON}>Download JSON</button>
        <button className="btn btn-outline btn-sm" onClick={handleDownloadGeoJSON}>Download GeoJSON</button>
      </div>
    </div>
  );
}
