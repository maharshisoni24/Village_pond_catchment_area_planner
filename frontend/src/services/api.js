import axios from 'axios';

// Backend URL: use env var if set, else try the IIT Bhilai network address,
// else fall back to localhost.
const BASE_URL =
  import.meta.env.VITE_API_URL ||
  (window.location.hostname === 'localhost'
    ? 'http://localhost:7000'
    : 'http://10.1.75.53:7202');

const api = axios.create({ baseURL: BASE_URL });

/**
 * POST /api/analysis/run — full analysis from a drawn rectangle bbox.
 * @param {number[]} bbox  [lon_min, lat_min, lon_max, lat_max]
 * @param {object} opts  { includeRainfall }
 */
export async function runAnalysis(bbox, { includeRainfall = true } = {}) {
  const { data } = await api.post('/api/analysis/run', {
    bbox,
    include_rainfall: includeRainfall,
  });
  return data;
}

/**
 * POST /analyzeContour — upload a KML/KMZ file and get full analysis.
 * @param {File} file
 * @param {object} opts  { includeContours, includeRainfall }
 * @returns {Promise<object>} analysis result JSON
 */
export async function analyzeContour(file, { includeContours = true, includeRainfall = true } = {}) {
  const form = new FormData();
  form.append('file', file);

  const params = new URLSearchParams();
  if (includeContours) params.set('include_contours', 'true');
  if (includeRainfall) params.set('include_rainfall', 'true');

  const { data } = await api.post(`/analyzeContour?${params}`, form);
  return data;
}

/**
 * POST /api/land/detect — detect open land patches in a bounding box.
 * @param {number[]} bbox  [lon_min, lat_min, lon_max, lat_max]
 * @returns {Promise<object>} land detection result JSON
 */
export async function detectLand(bbox) {
  const { data } = await api.post('/api/land/detect', { bbox });
  return data;
}

/**
 * Download GeoJSON result as a file blob.
 */
export async function downloadGeoJSON(file) {
  const form = new FormData();
  form.append('file', file);

  const { data } = await api.post('/analyzeContour?format=geojson', form, {
    responseType: 'blob',
  });
  return data;
}
