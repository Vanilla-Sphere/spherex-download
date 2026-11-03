# SPHEREx download

Standalone Python scripts for downloading SPHEREx Level 2 FITS cutouts from
NASA/IPAC IRSA and converting them into PNG previews. Includes normal downloads
and progressive downloads that expand across a sky grid. No science pipeline,
database, or API server is included.

Requires Python 3.11 or newer. Install from this directory:

```sh
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e .
```

## Normal download

```sh
python download.py --ra 166.5688 --dec -77.3658 --size 0.12 --output data/normal
```

Coordinates use ICRS degrees; `--size` is the square cutout width in degrees
(default 0.12, maximum 1). All overlapping Level 2 artifacts are requested,
across all available dates and detector bands. Coverage uses a circumscribed
cone; artifacts that IRSA explicitly reports as outside the square cutout are
recorded in the manifest as skipped. Other failures exit with a nonzero status.

Use `--limit 1 --workers 1` for a small trial. The default is two simultaneous
downloads; `--workers` accepts 1 through 8. A field may contain hundreds of
artifacts, so an unrestricted download can take substantial time and disk space.

## Progressive download

```sh
python progressive.py --ra 166.5688 --dec -77.3658 --levels 1 --output data/progressive
```

Downloads the stable 0.12-degree ICRS grid in expanding square rings:

| Levels | Tiles away from the poles |
| --- | --- |
| 0 | Center tile only |
| 1 | Center and eight neighbors (3 by 3) |
| 2 | Center and 24 neighbors (5 by 5) |

The seed is snapped to its grid cell center. RA wraps at 360 degrees; rows beyond
the poles are omitted. This is an RA/declination grid, so tiles are not equal-area.
Each tile has its own output subdirectory. `--limit` applies separately to each
tile. Increasing `--levels` reuses validated downloads from earlier rings.

## PNG conversion

After downloading, run either:

```sh
python fits_to_png.py data/normal --output data/png
python fits_to_png.py data/progressive --output data/progressive-png
```

Or convert one FITS file:

```sh
python fits_to_png.py path/to/frame.fits --output data/frame.png
```

Directory conversion recurses and preserves relative paths. Without `--output`,
PNGs are written beside the FITS files. `.fits` and `.fits.gz` files are supported.
The default HDU is `IMAGE`; select another with `--hdu 0` or `--hdu EXTNAME`.
Previews use the 1st and 99.5th percentiles, an asinh stretch, and a vertical flip
from FITS pixel order. Adjust contrast with `--lower` and `--upper`. Invalid
pixels render black. PNGs retain native dimensions but do not contain FITS WCS
or calibrated pixel values. They are per-frame display previews, without mosaics,
stacking, detections, or overlays. FITS files remain the scientific source data.

Installed command equivalents are `spherex-download`, `spherex-progressive`, and
`spherex-png`; they accept the same arguments as their respective Python scripts.

## Resume and output files

Each field directory contains `request.json`, cached `coverage.vot`,
`manifest.json`, and a `fits/` directory. FITS filenames hash the archive URI
and cutout parameters, keeping identities stable across reordered coverage.
The manifest records archive URIs, observation MJDs, bands, relative file paths,
and skipped non-overlapping artifacts. Empty coverage produces an empty manifest.

Rerun the same command to resume. Interrupted transfers retain `.part` files
and use HTTP byte ranges when supported. A server that ignores ranges causes
a full replacement download. Completed files are validated for `IMAGE`, `FLAGS`,
and `VARIANCE` extensions before publication or reuse; corrupt files are
quarantined as `.invalid`. Retries are bounded. `--refresh` queries coverage
again while retaining valid FITS files. Use separate output directories for
different coordinates or cutout sizes. Run one downloader per output directory.

PNG conversion is offline and can be repeated independently of downloads.
Downloaded data, previews, environments, and caches are excluded from Git.

## Development

```sh
python -m pip install -e ".[dev]"
python -m pytest
ruff check .
```

Download retry logic and grid conventions were extracted from
[space-apps-2026-spherex](https://github.com/Duy247/space-apps-2026-spherex).
Archive access uses the [IRSA TAP service](https://irsa.ipac.caltech.edu/TAP).
