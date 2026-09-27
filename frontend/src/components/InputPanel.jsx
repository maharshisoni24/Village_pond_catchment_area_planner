import { useState, useRef } from 'react';

export default function InputPanel({
  mode, onModeChange,
  onAnalyze, onDetectLand,
  loading, landLoading,
  result, landResult,
}) {
  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [includeContours, setIncludeContours] = useState(true);
  const [includeRainfall, setIncludeRainfall] = useState(true);
  const inputRef = useRef();

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) setFile(f);
  };

  const handleSubmit = () => {
    if (file) onAnalyze(file, { includeContours, includeRainfall });
  };

  return (
    <>
      {/* Mode toggle */}
      <div className="mode-toggle">
        <button
          className={`mode-btn ${mode === 'map' ? 'active' : ''}`}
          onClick={() => onModeChange('map')}
        >
          Select Area
        </button>
        <button
          className={`mode-btn ${mode === 'kml' ? 'active' : ''}`}
          onClick={() => onModeChange('kml')}
        >
          Upload KML
        </button>
      </div>

      {mode === 'map' ? (
        <div className="map-hint">
          {loading
            ? 'Fetching terrain data...'
            : 'Draw a rectangle on the map, then press Proceed to Analysis'}
        </div>
      ) : (
        <>
          <div
            className={`upload-zone ${dragOver ? 'drag-over' : ''}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
          >
            <div className="upload-zone-label">
              {file ? 'File selected — click to change' : 'Drop KML/KMZ file here or click to browse'}
            </div>
            {file && <div className="filename">{file.name}</div>}
            <input
              ref={inputRef}
              type="file"
              accept=".kml,.kmz"
              hidden
              onChange={(e) => setFile(e.target.files[0] || null)}
            />
          </div>

          <label className="checkbox-row">
            <input type="checkbox" checked={includeContours} onChange={(e) => setIncludeContours(e.target.checked)} />
            Show contour lines on map
          </label>

          <label className="checkbox-row">
            <input type="checkbox" checked={includeRainfall} onChange={(e) => setIncludeRainfall(e.target.checked)} />
            Fetch rainfall and pond recommendation
          </label>

          <button
            className="btn btn-primary"
            disabled={!file || loading || landLoading}
            onClick={handleSubmit}
          >
            {loading ? 'Analyzing...' : 'Analyze Contour'}
          </button>
        </>
      )}

      {result?.catchment?.boundary_geojson && (
        <button
          className="btn btn-secondary"
          style={{ marginTop: 8 }}
          disabled={landLoading || loading}
          onClick={onDetectLand}
        >
          {landLoading ? 'Detecting...' : landResult ? 'Re-detect Open Land' : 'Detect Open Land'}
        </button>
      )}
    </>
  );
}
