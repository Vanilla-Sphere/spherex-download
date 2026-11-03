import json
from unittest.mock import Mock

import numpy as np
import pytest
from astropy.io import fits
from astropy.table import Table
from PIL import Image

import download
import progressive
from fits_to_png import convert
from fits_to_png import main as png_main


def make_fits(path):
    data = np.array([[0.0, 1.0, np.nan], [2.0, 3.0, 4.0]])
    fits.HDUList(
        [fits.PrimaryHDU()]
        + [fits.ImageHDU(data, name=name) for name in ("IMAGE", "FLAGS", "VARIANCE")]
    ).writeto(path, overwrite=True)


def test_download_resume_refresh_and_request_identity(tmp_path, monkeypatch):
    coverage = tmp_path / "source.vot"
    Table(
        dict(uri=["/frame.fits"], time_bounds_lower=[60000.0], energy_bandpassname=["SPHEREx-D1"])
    ).write(coverage, format="votable")
    payload = coverage.read_bytes().replace(b'ID="uri"', b'ID="col_0"')
    response = Mock(content=payload)
    query = Mock(return_value=response)
    transfer = Mock(side_effect=lambda url, path, **kwargs: make_fits(path))
    monkeypatch.setattr(download.DownloadClient, "get", lambda self, *a, **kw: query(*a, **kw))
    monkeypatch.setattr(download.DownloadClient, "fetch", lambda self, *a, **kw: transfer(*a, **kw))
    output = tmp_path / "field"
    first = download.download_field(1.0, 2.0, output)
    download.download_field(1.0, 2.0, output)
    assert query.call_count == transfer.call_count == 1
    path = output / first["artifacts"][0]["path"]
    path.write_bytes(b"corrupt")
    download.download_field(1.0, 2.0, output, refresh=True)
    assert query.call_count == transfer.call_count == 2
    assert path.with_suffix(".invalid").exists()
    with pytest.raises(ValueError, match="different request"):
        download.download_field(3.0, 2.0, output)
    assert json.loads((output / "manifest.json").read_text())["downloaded"] == 1


def test_progressive_rings_wrap_and_poles():
    tiles = list(progressive.grid(0, 0, 2))
    assert len(tiles) == 25
    assert [sum(t["level"] == i for t in tiles) for i in range(3)] == [1, 8, 16]
    assert len({t["tile_id"] for t in tiles}) == 25
    assert any(t["ra_c"] > 359 for t in tiles)
    assert len(list(progressive.grid(0, 90, 1))) == 6
    with pytest.raises(ValueError):
        list(progressive.grid(float("nan"), 0, 0))


def test_progressive_command_stages_every_tile(tmp_path, monkeypatch):
    stage = Mock()
    monkeypatch.setattr(progressive, "download_field", stage)
    progressive.main(
        ["--ra", "1", "--dec", "2", "--levels", "1", "--limit", "1", "--output", str(tmp_path)]
    )
    assert stage.call_count == 9
    assert all(call.kwargs["limit"] == 1 for call in stage.call_args_list)
    assert len({call.args[2] for call in stage.call_args_list}) == 9


def test_png_pixels_orientation_nan_and_recursive_paths(tmp_path):
    source = tmp_path / "inputs" / "tile" / "frame.fits"
    source.parent.mkdir(parents=True)
    make_fits(source)
    output = tmp_path / "preview.png"
    convert(source, output, lower=0, upper=100)
    with Image.open(output) as image:
        pixels = np.array(image)
        assert image.size == (3, 2)
        assert pixels[0, 2] == 255
        assert pixels[1, 2] == 0
        assert pixels[0, 0] > pixels[1, 0]
    png_main([str(source.parents[1]), "--output", str(tmp_path / "png")])
    assert (tmp_path / "png" / "tile" / "frame.png").exists()


def test_invalid_fits_never_published(tmp_path, monkeypatch):
    coverage = tmp_path / "source.vot"
    Table(
        dict(uri=["/frame.fits"], time_bounds_lower=[60000.0], energy_bandpassname=["SPHEREx-D1"])
    ).write(coverage, format="votable")
    monkeypatch.setattr(
        download.DownloadClient, "get", lambda *a, **kw: Mock(content=coverage.read_bytes())
    )
    monkeypatch.setattr(
        download.DownloadClient,
        "fetch",
        lambda self, url, path, **kw: path.write_bytes(b"not fits"),
    )
    with pytest.raises(OSError):
        download.download_field(1, 2, tmp_path / "field")
    assert not list((tmp_path / "field" / "fits").glob("*.fits"))
    assert not (tmp_path / "field" / "manifest.json").exists()
