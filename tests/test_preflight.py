"""The startup preflight is the guard that keeps a weak listener off the network."""
from __future__ import annotations

import pytest

TOKEN_32 = "a" * 32


def test_loopback_without_token_is_allowed(bridge):
    """A loopback bind needs neither a token nor TLS."""
    assert bridge.preflight() is None


def test_public_bind_without_token_aborts(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "BIND", "0.0.0.0")
    with pytest.raises(SystemExit):
        bridge.preflight()


def test_public_bind_without_tls_aborts(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "BIND", "0.0.0.0")
    monkeypatch.setattr(bridge, "TOKEN", TOKEN_32)
    with pytest.raises(SystemExit):
        bridge.preflight()


def test_short_token_aborts(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "TOKEN", "tooshort")
    with pytest.raises(SystemExit):
        bridge.preflight()


def test_insecure_override_starts_anyway(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "BIND", "0.0.0.0")
    monkeypatch.setattr(bridge, "INSECURE", True)
    assert bridge.preflight() is None


def test_tls_context_is_built_from_cert_pair(bridge, monkeypatch, tls_cert_pair):
    cert, key = tls_cert_pair
    monkeypatch.setattr(bridge, "BIND", "0.0.0.0")
    monkeypatch.setattr(bridge, "TOKEN", TOKEN_32)
    monkeypatch.setattr(bridge, "TLS_CERT", str(cert))
    monkeypatch.setattr(bridge, "TLS_KEY", str(key))
    ctx = bridge.preflight()
    assert ctx is not None
    assert ctx.minimum_version.name == "TLSv1_2"
