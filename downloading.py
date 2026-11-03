"""Cross-platform, resumable HTTP downloads for archived FITS files."""

import logging
import random
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import HTTPError as TransportError
from urllib3.util.retry import Retry

LOGGER = logging.getLogger(__name__)
TRANSIENT = (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)


class DownloadClient:
    """Own an HTTP session with retry policy for archive transfers."""

    def __init__(self, attempts: int = 4) -> None:
        if attempts < 1:
            raise ValueError("attempts must be positive")
        self.attempts = attempts
        self.session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods={"GET", "HEAD"},
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "DownloadClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _pause(self, attempt: int, error: Exception) -> None:
        delay = min(30, 2 ** (attempt + 1)) + random.uniform(0, 1)
        LOGGER.warning(
            "IRSA transfer interrupted (%s); retry %s/%s in %.1fs",
            type(error).__name__,
            attempt + 2,
            self.attempts,
            delay,
        )
        time.sleep(delay)

    def get(self, url: str, *, params=None, timeout=(30, 240)) -> requests.Response:
        """Retry buffered responses too: adapter retries do not cover all body resets."""
        for attempt in range(self.attempts):
            try:
                return self.session.get(url, params=params, timeout=timeout)
            except TRANSIENT as error:
                if attempt + 1 == self.attempts:
                    raise
                self._pause(attempt, error)

    def fetch(self, url: str, partial: Path, *, params=None, preserve_encoding=False) -> None:
        for attempt in range(self.attempts):
            try:
                self._fetch_once(url, partial, params=params, preserve_encoding=preserve_encoding)
                return
            except TRANSIENT as error:
                if attempt + 1 == self.attempts:
                    raise
                self._pause(attempt, error)

    def _fetch_once(self, url: str, partial: Path, *, params=None, preserve_encoding=False) -> None:
        partial.parent.mkdir(parents=True, exist_ok=True)
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        with self.session.get(
            url, params=params, headers=headers, stream=True, timeout=(30, 180)
        ) as response:
            if response.status_code == 416 and offset:
                # The caller validates FITS before publishing a completed partial file.
                if response.headers.get("Content-Range") == f"bytes */{offset}":
                    return
            response.raise_for_status()
            if response.status_code == 206:
                expected = f"bytes {offset}-"
                if not response.headers.get("Content-Range", "").startswith(expected):
                    raise ValueError("Archive returned an unexpected resume range")
            mode = "ab" if offset and response.status_code == 206 else "wb"
            written = 0
            with partial.open(mode) as target:
                chunks = (
                    response.raw.stream(1024 * 1024, decode_content=False)
                    if preserve_encoding
                    else response.iter_content(chunk_size=1024 * 1024)
                )
                try:
                    for chunk in chunks:
                        if chunk:
                            target.write(chunk)
                            written += len(chunk)
                except TransportError as exc:
                    raise requests.ConnectionError(str(exc)) from exc
            expected_length = response.headers.get("Content-Length")
            if expected_length is not None and written != int(expected_length):
                raise requests.exceptions.ChunkedEncodingError("Incomplete response body")
