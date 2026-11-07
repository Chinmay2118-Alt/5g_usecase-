"""Tests for production server features: environment loading, TLS SAN generation, and API health routes."""
from __future__ import annotations

import os
from pathlib import Path

from server.certs import CERT, KEY, make_cert
from server.main import _load_dotenv, make_app


def test_make_cert_handles_ips_and_hostnames(tmp_path):
    os.environ["CERT_DIR"] = str(tmp_path)
    try:
        from server import certs
        certs.CERT_DIR = tmp_path
        certs.CERT = tmp_path / "cert.pem"
        certs.KEY = tmp_path / "key.pem"

        recorded = certs.make_cert(["10.99.0.1", "5g-mec.internal"])
        assert "10.99.0.1" in recorded
        assert "5g-mec.internal" in recorded
        assert (tmp_path / "cert.pem").is_file()
        assert (tmp_path / "key.pem").is_file()
        assert (tmp_path / "cert.pem").stat().st_size > 500
    finally:
        os.environ.pop("CERT_DIR", None)


def test_load_dotenv_parses_env_file(tmp_path, monkeypatch):
    test_env = tmp_path / ".env"
    test_env.write_text("TEST_5G_VAR=mec_ready\n# comment\nTEST_QUOTED=\"with_quotes\"\n", encoding="utf-8")

    import server.main as main_mod

    monkeypatch.delenv("TEST_5G_VAR", raising=False)
    monkeypatch.delenv("TEST_QUOTED", raising=False)

    main_mod._load_dotenv(test_env)
    assert os.environ.get("TEST_5G_VAR") == "mec_ready"
    assert os.environ.get("TEST_QUOTED") == "with_quotes"


def test_make_app_registers_health_endpoints():
    app = make_app("flood")
    routes = [r.resource.canonical for r in app.router.routes()]
    assert "/api/health" in routes
    assert "/healthz" in routes
    assert "/api/status" in routes
    assert "/api/results" in routes
    assert "/ws/operator" in routes
    assert "/ws/dashboard" in routes
