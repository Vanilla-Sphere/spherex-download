"""Convert individual FITS images or a directory tree into grayscale PNG previews."""

import argparse
from pathlib import Path

import numpy as np
from astropy.io import fits
from PIL import Image


def convert(source, destination, *, hdu="IMAGE", lower=1, upper=99.5):
    if not 0 <= lower < upper <= 100:
        raise ValueError("Percentiles must satisfy 0 <= lower < upper <= 100")
    with fits.open(source, memmap=False) as hdus:
        data = np.array(hdus[hdu].data, dtype=float)
    if data.ndim != 2:
        raise ValueError(f"{source}: selected HDU must be a 2D image")
    valid = np.isfinite(data)
    if not valid.any():
        raise ValueError(f"{source}: image has no finite pixels")
    lo, hi = np.percentile(data[valid], [lower, upper])
    scaled = np.zeros(data.shape, dtype=float)
    if hi > lo:
        scaled[valid] = np.clip((data[valid] - lo) / (hi - lo), 0, 1)
    scaled = np.arcsinh(10 * scaled) / np.arcsinh(10)
    pixels = np.rint(scaled * 255).astype(np.uint8)[::-1].copy()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    Image.fromarray(pixels).save(temporary, format="PNG")
    temporary.replace(destination)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="FITS file or directory (searched recursively)")
    parser.add_argument("--output", type=Path, help="PNG file or output directory")
    parser.add_argument("--hdu", default="IMAGE", help="HDU name or zero-based integer index")
    parser.add_argument("--lower", type=float, default=1, help="Lower display percentile")
    parser.add_argument("--upper", type=float, default=99.5, help="Upper display percentile")
    args = parser.parse_args(argv)
    hdu = int(args.hdu) if args.hdu.isdecimal() else args.hdu
    if args.source.is_dir():
        sources = sorted(
            p
            for p in args.source.rglob("*")
            if p.is_file() and p.name.lower().endswith((".fits", ".fits.gz"))
        )
        targets = [
            (
                p,
                (args.output / p.relative_to(args.source) if args.output else p)
                .with_suffix("")
                .with_suffix(".png")
                if p.suffix == ".gz"
                else (args.output / p.relative_to(args.source) if args.output else p).with_suffix(
                    ".png"
                ),
            )
            for p in sources
        ]
    elif args.source.is_file():
        default = args.source.with_suffix("") if args.source.suffix == ".gz" else args.source
        targets = [(args.source, args.output or default.with_suffix(".png"))]
    else:
        parser.error(f"Source does not exist: {args.source}")
    if not targets:
        parser.error("No FITS files found")
    failures = 0
    for source, destination in targets:
        try:
            print(convert(source, destination, hdu=hdu, lower=args.lower, upper=args.upper))
        except Exception as exc:
            failures += 1
            print(f"Failed {source}: {exc}")
    if failures:
        parser.exit(1, f"{failures} conversion(s) failed\n")


if __name__ == "__main__":
    main()
