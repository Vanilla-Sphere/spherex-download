"""Download overlapping SPHEREx Level 2 FITS cutouts from IRSA."""

import argparse
import hashlib
import io
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from astropy.io import fits
from astropy.table import Table

from downloading import DownloadClient

ARCHIVE = "https://irsa.ipac.caltech.edu/"
LOGGER = logging.getLogger(__name__)


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate(path):
    with fits.open(path, memmap=False) as hdus:
        hdus.verify("exception")
        for name in ("IMAGE", "FLAGS", "VARIANCE"):
            if hdus[name].data is None or hdus[name].data.ndim != 2:
                raise ValueError(f"Missing 2D {name} pixels")


def validate_region(ra, dec, size):
    if not (math.isfinite(ra) and 0 <= ra < 360):
        raise ValueError("RA must be finite and in [0, 360)")
    if not (math.isfinite(dec) and -90 <= dec <= 90):
        raise ValueError("Dec must be finite and in [-90, 90]")
    if not (math.isfinite(size) and 0 < size <= 1):
        raise ValueError("Cutout size must be in (0, 1] degrees")


def download_field(ra, dec, output, *, size=0.12, workers=2, limit=None, refresh=False):
    validate_region(ra, dec, size)
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # A circumscribed cone also handles RA wrap and polar fields.
    query = (
        "SELECT a.uri,p.time_bounds_lower,p.energy_bandpassname "
        "FROM spherex.artifact a JOIN spherex.plane p ON a.planeid=p.planeid "
        f"WHERE 1=INTERSECTS(p.poly,CIRCLE('ICRS',{ra},{dec},{size / math.sqrt(2)})) "
        "AND p.calibrationlevel=2 ORDER BY p.time_bounds_lower,a.uri"
    )
    request = dict(ra=ra, dec=dec, size=size, query=query)
    request_file = output / "request.json"
    if request_file.exists() and json.loads(request_file.read_text(encoding="utf-8")) != request:
        raise ValueError("Output directory belongs to a different request; use another directory")
    write_json(request_file, request)
    coverage = output / "coverage.vot"
    if refresh or not coverage.exists():
        LOGGER.info("Querying IRSA coverage for %.6f, %.6f", ra, dec)
        with DownloadClient() as client:
            response = client.get(
                ARCHIVE + "TAP/sync",
                params=dict(REQUEST="doQuery", LANG="ADQL", FORMAT="votable", QUERY=query),
            )
            response.raise_for_status()
            table = Table.read(io.BytesIO(response.content), format="votable", use_names_over_ids=True)
            # TAP can return a partial table with HTTP 200: never cache it as complete.
            from astropy.io.votable import parse

            votable = parse(io.BytesIO(response.content))
            infos = list(votable.infos)
            for resource in votable.resources:
                infos.extend(resource.infos)
            if any(i.name == "QUERY_STATUS" and i.value != "OK" for i in infos):
                raise RuntimeError("IRSA returned an incomplete or failed coverage query")
            temporary = coverage.with_suffix(".tmp")
            temporary.write_bytes(response.content)
            temporary.replace(coverage)
    else:
        table = Table.read(coverage, format="votable", use_names_over_ids=True)
    rows = {str(row["uri"]): row for row in table if str(row["uri"]).endswith(".fits")}
    items = sorted(rows.items(), key=lambda item: (float(item[1]["time_bounds_lower"]), item[0]))
    if limit is not None:
        items = items[:limit]
    fits_dir = output / "fits"
    fits_dir.mkdir(exist_ok=True)

    def transfer(item):
        uri, row = item
        url = urljoin(ARCHIVE, uri)
        if urlparse(url).netloc != urlparse(ARCHIVE).netloc:
            raise ValueError("Coverage contains an unexpected archive host")
        identity = hashlib.sha256(f"{uri}|{ra}|{dec}|{size}".encode()).hexdigest()[:24]
        path = fits_dir / f"{identity}.fits"
        cached = False
        if path.exists():
            try:
                validate(path)
                cached = True
            except (OSError, ValueError, KeyError):
                path.replace(path.with_suffix(".invalid"))
        if not cached:
            partial = path.with_suffix(".fits.part")
            with DownloadClient() as client:
                for attempt in range(2):
                    try:
                        client.fetch(url, partial, params=dict(center=f"{ra},{dec}d", size=size))
                    except requests.HTTPError as exc:
                        response = exc.response
                        if (
                            response is not None
                            and response.status_code == 422
                            and "Arrays do not overlap" in response.text
                        ):
                            partial.unlink(missing_ok=True)
                            LOGGER.info("Skipping artifact outside square cutout: %s", uri)
                            return dict(uri=uri, status="no_overlap")
                        raise
                    try:
                        validate(partial)
                    except (OSError, ValueError, KeyError):
                        partial.replace(partial.with_suffix(".invalid"))
                        if attempt:
                            raise
                    else:
                        break
            partial.replace(path)
        LOGGER.info("%s %s", "Reused" if cached else "Downloaded", path)
        return dict(
            path=path.relative_to(output).as_posix(),
            uri=uri,
            mjd=float(row["time_bounds_lower"]),
            band=str(row["energy_bandpassname"]),
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(transfer, items))
    artifacts = [row for row in results if "path" in row]
    manifest = dict(
        request=request,
        available=len(rows),
        downloaded=len(artifacts),
        artifacts=artifacts,
        skipped=[row for row in results if "path" not in row],
    )
    write_json(output / "manifest.json", manifest)
    LOGGER.info("Saved %d/%d FITS files in %s", len(artifacts), len(rows), output)
    return manifest


def add_download_options(parser):
    parser.add_argument("--ra", required=True, type=float, help="ICRS RA in degrees")
    parser.add_argument("--dec", required=True, type=float, help="ICRS declination in degrees")
    parser.add_argument("--output", type=Path, default=Path("data/normal"))
    parser.add_argument("--workers", type=int, default=2, help="Concurrent downloads, 1-8")
    parser.add_argument(
        "--limit", type=int, help="Maximum artifacts per field, for small trial runs"
    )
    parser.add_argument("--refresh", action="store_true", help="Refresh coverage; reuse valid FITS")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_download_options(parser)
    parser.add_argument("--size", type=float, default=0.12, help="Cutout width in degrees")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        download_field(
            args.ra,
            args.dec,
            args.output,
            size=args.size,
            workers=args.workers,
            limit=args.limit,
            refresh=args.refresh,
        )
    except Exception as exc:
        parser.exit(1, f"Download failed: {exc}\n")


if __name__ == "__main__":
    main()
