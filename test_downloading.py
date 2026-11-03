"""Cross-platform archive resume behavior."""

from unittest.mock import MagicMock

import pytest
import requests

from downloading import DownloadClient


def response(status, content_range, payload):
    result = MagicMock()
    result.status_code = status
    result.headers = {"Content-Range": content_range}
    result.iter_content.return_value = [payload]
    result.__enter__.return_value = result
    return result


def test_resume_appends_only_the_requested_range(tmp_path):
    partial = tmp_path / "frame.fits.part"
    partial.write_bytes(b"first")
    with DownloadClient() as client:
        client.session.get = MagicMock(return_value=response(206, "bytes 5-8/9", b"last"))
        client.fetch("https://example.test/frame.fits", partial)
        assert client.session.get.call_args.kwargs["headers"]["Range"] == "bytes=5-"
    assert partial.read_bytes() == b"firstlast"


def test_full_response_replaces_partial(tmp_path):
    partial = tmp_path / "frame.fits.part"
    partial.write_bytes(b"stale")
    with DownloadClient() as client:
        client.session.get = MagicMock(return_value=response(200, "", b"fresh"))
        client.fetch("https://example.test/frame.fits", partial)
    assert partial.read_bytes() == b"fresh"


def test_wrong_resume_range_is_rejected(tmp_path):
    partial = tmp_path / "frame.fits.part"
    partial.write_bytes(b"first")
    with DownloadClient() as client:
        client.session.get = MagicMock(return_value=response(206, "bytes 0-8/9", b"other"))
        with pytest.raises(ValueError, match="resume range"):
            client.fetch("https://example.test/frame.fits", partial)
    assert partial.read_bytes() == b"first"


def test_interrupted_stream_resumes_from_bytes_written(tmp_path, monkeypatch):
    partial = tmp_path / "frame.part"

    def interrupted():
        yield b"first"
        raise requests.exceptions.ChunkedEncodingError("reset")

    first = response(200, "", b"")
    first.iter_content.return_value = interrupted()
    with DownloadClient() as client:
        monkeypatch.setattr(client, "_pause", lambda *_: None)
        client.session.get = MagicMock(side_effect=[first, response(206, "bytes 5-8/9", b"last")])
        client.fetch("https://example.test/frame", partial)
        assert client.session.get.call_args.kwargs["headers"]["Range"] == "bytes=5-"
    assert partial.read_bytes() == b"firstlast"


def test_buffered_query_retries_reset(monkeypatch):
    with DownloadClient() as client:
        monkeypatch.setattr(client, "_pause", lambda *_: None)
        result = response(200, "", b"coverage")
        client.session.get = MagicMock(side_effect=[requests.ConnectionError("reset"), result])
        assert client.get("https://example.test/TAP/sync") is result


def test_transfer_retries_are_bounded(tmp_path, monkeypatch):
    with DownloadClient(attempts=2) as client:
        monkeypatch.setattr(client, "_pause", lambda *_: None)
        client.session.get = MagicMock(side_effect=requests.Timeout("timeout"))
        with pytest.raises(requests.Timeout):
            client.fetch("https://example.test/frame", tmp_path / "frame.part")
        assert client.session.get.call_count == 2


def test_gzip_transport_keeps_wire_bytes_for_correct_range_resume(tmp_path, monkeypatch):
    from urllib3.exceptions import ProtocolError

    partial = tmp_path / "encoded.part"

    def interrupted():
        yield b"\x1f\x8bfirst"
        raise ProtocolError("reset")

    first = response(200, "", b"wrong decoded bytes")
    first.raw.stream.return_value = interrupted()
    second = response(206, "bytes 7-10/11", b"wrong decoded bytes")
    second.raw.stream.return_value = [b"last"]
    with DownloadClient(attempts=2) as client:
        monkeypatch.setattr(client, "_pause", lambda *_: None)
        client.session.get = MagicMock(side_effect=[first, second])
        client.fetch("https://example.test/frame", partial, preserve_encoding=True)
        assert client.session.get.call_args.kwargs["headers"]["Range"] == "bytes=7-"
        first.raw.stream.assert_called_once_with(1024 * 1024, decode_content=False)
        first.iter_content.assert_not_called()
    assert partial.read_bytes() == b"\x1f\x8bfirstlast"
