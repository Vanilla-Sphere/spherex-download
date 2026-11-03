"""Download a fixed 0.12-degree grid, expanding one ring at a time."""

import argparse
import logging
from pathlib import Path

from download import add_download_options, download_field, validate_region

STEP = 0.12
NX, NY = 3000, 1500


def grid(ra, dec, levels):
    validate_region(ra, dec, STEP)
    if not isinstance(levels, int) or not 0 <= levels < NY:
        raise ValueError("levels must be an integer in [0, 1499]")
    x, y = int(ra / STEP), min(NY - 1, int((dec + 90) / STEP))
    for level in range(levels + 1):
        for dy in range(-level, level + 1):
            for dx in range(-level, level + 1):
                if max(abs(dx), abs(dy)) != level:
                    continue
                xx, yy = (x + dx) % NX, y + dy
                if 0 <= yy < NY:
                    yield dict(
                        tile_id=f"icrs012-v1-{xx:04d}-{yy:04d}",
                        ra_c=(xx + 0.5) * STEP,
                        dec_c=(yy + 0.5) * STEP - 90,
                        level=level,
                    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_download_options(parser)
    parser.set_defaults(output=Path("data/progressive"))
    parser.add_argument("--levels", type=int, default=0, help="0=center, 1=3x3, 2=5x5")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        for tile in grid(args.ra, args.dec, args.levels):
            logging.info("Ring %d: %s", tile["level"], tile["tile_id"])
            download_field(
                tile["ra_c"],
                tile["dec_c"],
                args.output / tile["tile_id"],
                workers=args.workers,
                limit=args.limit,
                refresh=args.refresh,
            )
    except Exception as exc:
        parser.exit(1, f"Progressive download failed: {exc}\n")


if __name__ == "__main__":
    main()
