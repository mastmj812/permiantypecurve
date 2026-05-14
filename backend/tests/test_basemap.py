"""Tests for PMTiles range serving.

We don't ship a real PMTiles file in the repo (it's ~150–250 MB and
gitignored), so the test points the backend at a small fixture file via
monkeypatching settings.pmtiles_path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


@pytest.fixture
def fake_pmtiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Deterministic 4 KB payload — easy to verify slice contents.
    f = tmp_path / "fake.pmtiles"
    f.write_bytes(bytes(i % 256 for i in range(4096)))
    monkeypatch.setattr(settings, "pmtiles_path", f)
    return f


def test_pmtiles_missing_returns_404(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "pmtiles_path", tmp_path / "does-not-exist.pmtiles")
    resp = client.get("/api/basemap/permian.pmtiles")
    assert resp.status_code == 404


def test_head_returns_size(fake_pmtiles: Path) -> None:
    resp = client.head("/api/basemap/permian.pmtiles")
    assert resp.status_code == 200
    assert resp.headers["accept-ranges"] == "bytes"
    assert int(resp.headers["content-length"]) == fake_pmtiles.stat().st_size


def test_range_returns_206_with_correct_slice(fake_pmtiles: Path) -> None:
    resp = client.get("/api/basemap/permian.pmtiles", headers={"Range": "bytes=100-199"})
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes 100-199/{fake_pmtiles.stat().st_size}"
    assert resp.headers["accept-ranges"] == "bytes"
    expected = fake_pmtiles.read_bytes()[100:200]
    assert resp.content == expected


def test_range_open_ended(fake_pmtiles: Path) -> None:
    resp = client.get("/api/basemap/permian.pmtiles", headers={"Range": "bytes=4000-"})
    assert resp.status_code == 206
    size = fake_pmtiles.stat().st_size
    assert resp.headers["content-range"] == f"bytes 4000-{size - 1}/{size}"
    assert len(resp.content) == size - 4000


def test_range_out_of_bounds_returns_416(fake_pmtiles: Path) -> None:
    resp = client.get("/api/basemap/permian.pmtiles", headers={"Range": "bytes=99999-"})
    assert resp.status_code == 416


def test_malformed_range_returns_416(fake_pmtiles: Path) -> None:
    resp = client.get("/api/basemap/permian.pmtiles", headers={"Range": "rows=0-10"})
    assert resp.status_code == 416


def test_no_range_returns_full_file(fake_pmtiles: Path) -> None:
    resp = client.get("/api/basemap/permian.pmtiles")
    assert resp.status_code == 200
    assert resp.content == fake_pmtiles.read_bytes()
