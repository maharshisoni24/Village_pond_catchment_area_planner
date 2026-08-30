"""
DEM builder — interpolates scattered contour vertices into a regular elevation
grid that pysheds can consume.

Why scipy.interpolate.griddata?  Contour vertices are irregularly spaced;
griddata does Delaunay triangulation + linear barycentric interpolation, which
is the standard approach for contour-to-raster conversion and easy to explain
in a VIVA.  We avoid cubic because it can create negative elevations between
sparse contour lines.

The output is an in-memory numpy array + an Affine transform describing the
grid's georeferencing — no intermediate TIFF file needed.
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import griddata

from app.core.config import GRID_CELL_SIZE_DEG, INTERPOLATION_METHOD
from app.services.kml_parser import ContourLine


def build_dem(
    contours: list[ContourLine],
    cell_size: float = GRID_CELL_SIZE_DEG,
    method: str = INTERPOLATION_METHOD,
) -> tuple[np.ndarray, dict]:
    """
    Interpolate contour vertices into a regular elevation raster.

    Returns
    -------
    grid : 2-D float64 array, shape (nrows, ncols).  NaN where the
           interpolation has no data (outside the convex hull of vertices).
    meta : dict with keys 'transform' (Affine-style 6-tuple), 'shape',
           'x_min', 'y_min', 'cell_size' — everything downstream modules
           need to georeference the grid.

    Why we collect every vertex (not just one point per contour):
    Each contour line has many vertices at the *same* elevation — feeding all
    of them into griddata gives far denser spatial coverage than using a single
    representative point, which would leave huge interpolation gaps.
    """
    # Collect all (lon, lat, elevation) from every contour vertex
    points = []  # (lon, lat)
    values = []  # elevation
    for c in contours:
        for lon, lat in c.coords:
            points.append((lon, lat))
            values.append(c.elevation_m)

    pts = np.array(points)
    vals = np.array(values)

    # Build the regular grid
    x_min, x_max = pts[:, 0].min(), pts[:, 0].max()
    y_min, y_max = pts[:, 1].min(), pts[:, 1].max()

    # Tiny padding so edge contours aren't right on the grid boundary
    pad = cell_size * 2
    x_min -= pad
    x_max += pad
    y_min -= pad
    y_max += pad

    xi = np.arange(x_min, x_max, cell_size)
    yi = np.arange(y_min, y_max, cell_size)
    grid_x, grid_y = np.meshgrid(xi, yi)

    # Interpolate — linear is the default; see config.py
    grid = griddata(pts, vals, (grid_x, grid_y), method=method)

    # Flip vertically: raster convention is top-row = north (high lat),
    # but arange gives low-to-high.
    grid = np.flipud(grid)
    y_max_actual = yi[-1] + cell_size  # top-left corner after flip

    meta = {
        "x_min": x_min,
        "y_min": y_min,
        "y_max": y_max_actual,
        "cell_size": cell_size,
        "shape": grid.shape,
        # Affine-style transform: (x_origin, cell_w, 0, y_origin, 0, -cell_h)
        "transform": (x_min, cell_size, 0.0, y_max_actual, 0.0, -cell_size),
    }
    return grid.astype(np.float64), meta
