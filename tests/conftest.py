"""Shared fixtures.

`bridge_for_agents.bridge` reads its configuration from the environment at
import time, so the variables it requires must exist before the first import.
"""
from __future__ import annotations

import os
import shutil

os.environ.setdefault("TG_BOT_TOKEN", "123:test-token")
os.environ.setdefault("TG_CHAT_ID", "-1001234567890")

import subprocess  # noqa: E402

import pytest  # noqa: E402

from bridge_for_agents import bridge as bridge_module  # noqa: E402


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch):
    """The bridge module with a loopback, no-TLS baseline configuration."""
    monkeypatch.setattr(bridge_module, "BIND", "127.0.0.1")
    monkeypatch.setattr(bridge_module, "TOKEN", "")
    monkeypatch.setattr(bridge_module, "TLS_CERT", "")
    monkeypatch.setattr(bridge_module, "TLS_KEY", "")
    monkeypatch.setattr(bridge_module, "INSECURE", False)
    monkeypatch.setattr(bridge_module, "SCOPE", "session")
    return bridge_module


@pytest.fixture(scope="session")
def tls_cert_pair(tmp_path_factory) -> tuple:
    """A throwaway self-signed cert/key pair, for exercising the TLS path."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    d = tmp_path_factory.mktemp("pki")
    cert, key = d / "server.crt", d / "server.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
         "-days", "1", "-subj", "/CN=localhost",
         "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )
    return cert, key
