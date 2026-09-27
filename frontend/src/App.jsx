import { useState, useCallback } from 'react';
import InputPanel from './components/InputPanel';
import MapView from './components/MapView';
import ResultDashboard from './components/ResultDashboard';
import { runAnalysis, analyzeContour, detectLand } from './services/api';

const MIN_DEG = 0.01;
const MAX_DEG = 0.15;

function computeSelectionBbox(anchor, corner) {
  if (!anchor || !corner) return null;
  const lonMin = Math.min(anchor.lng, corner.lng);
  const lonMax = Math.max(anchor.lng, corner.lng);
  const latMin = Math.min(anchor.lat, corner.lat);
  const latMax = Math.max(anchor.lat, corner.lat);
  const lonSpan = lonMax - lonMin;
  const latSpan = latMax - latMin;

  if (lonSpan < MIN_DEG || latSpan < MIN_DEG) {
    return { valid: false, reason: `Selection too small — minimum ${MIN_DEG}° per side (~1 km)`, lonSpan, latSpan };
  }
  if (lonSpan > MAX_DEG || latSpan > MAX_DEG) {
    return { valid: false, reason: `Selection too large — maximum ${MAX_DEG}° per side (~17 km)`, lonSpan, latSpan };
  }
  return { valid: true, bbox: [lonMin, latMin, lonMax, latMax], lonSpan, latSpan };
}

export default function App() {
  const [mode, setMode] = useState('map');

  const [result, setResult] = useState(null);
  const [landResult, setLandResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [landLoading, setLandLoading] = useState(false);
  const [error, setError] = useState(null);
  const [file, setFile] = useState(null);
  const [selectedCandidate, setSelectedCandidate] = useState(1); // rank 1 = best

  // Rectangle selection state (map mode)
  const [selectionBbox, setSelectionBbox] = useState(null);

  // Called from MapView when user clicks — updates selection state
  const handleSelectionUpdate = useCallback((anchor, corner) => {
    setSelectionBbox(computeSelectionBbox(anchor, corner));
  }, []);

  const handleCandidateSelect = useCallback((rank) => {
    setSelectedCandidate(rank);
  }, []);

  // Called when user clicks "Proceed to Analysis"
  const handleBboxConfirm = async (bbox) => {
    setLoading(true);
    setError(null);
    setResult(null);
    setLandResult(null);
    setSelectedCandidate(1); // reset to best candidate on new analysis
    try {
      const data = await runAnalysis(bbox);
      setResult(data);
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'Analysis failed';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  // KML upload flow
  const handleAnalyze = async (f, opts) => {
    setFile(f);
    setLoading(true);
    setError(null);
    setResult(null);
    setLandResult(null);
    try {
      const data = await analyzeContour(f, opts);
      setResult(data);
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'Analysis failed';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  // Open land detection
  const handleDetectLand = async () => {
    if (!result?.catchment?.boundary_geojson) return;
    const coords = result.catchment.boundary_geojson.coordinates[0];
    const lons = coords.map(c => c[0]);
    const lats = coords.map(c => c[1]);
    const bbox = [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
    setLandLoading(true);
    setError(null);
    try {
      const data = await detectLand(bbox);
      setLandResult(data);
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'Land detection failed';
      setError(msg);
    } finally {
      setLandLoading(false);
    }
  };

  const handleModeChange = (m) => {
    setMode(m);
    if (m === 'kml') setSelectionBbox(null); // clear selection when switching to KML
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h1>Village Pond Planner</h1>
          <p>
            {mode === 'map'
              ? 'Draw a rectangle on the map to analyse'
              : 'Upload a KML/KMZ contour file'}
          </p>
        </div>
        <div className="sidebar-content">
          <InputPanel
            mode={mode}
            onModeChange={handleModeChange}
            onAnalyze={handleAnalyze}
            onDetectLand={handleDetectLand}
            loading={loading}
            landLoading={landLoading}
            result={result}
            landResult={landResult}
          />

          {error && <div className="error-box">{error}</div>}

          {(loading || landLoading) && (
            <div className="loading-overlay">
              <div className="spinner" />
              <div className="loading-text">
                {landLoading ? 'Detecting open land...' : 'Fetching terrain and analysing...'}
              </div>
            </div>
          )}

          <ResultDashboard
            result={result}
            landResult={landResult}
            file={file}
            selectedCandidate={selectedCandidate}
            onCandidateSelect={handleCandidateSelect}
          />
        </div>
      </aside>
      <main className="map-container">
        <MapView
          result={result}
          landResult={landResult}
          mode={mode}
          loading={loading}
          selectionBbox={selectionBbox}
          onSelectionUpdate={handleSelectionUpdate}
          onBboxConfirm={handleBboxConfirm}
          selectedCandidate={selectedCandidate}
          onCandidateSelect={handleCandidateSelect}
        />
      </main>
    </div>
  );
}
