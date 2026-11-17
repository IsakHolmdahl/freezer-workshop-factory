"""Credential backend selection and errors without accessing a real desktop store."""
from types import SimpleNamespace

import pytest

import credential_store
from steps.common import StepError


@pytest.mark.parametrize("module,accepted", [
    ("keyring.backends.macOS", True),
    ("keyring.backends.SecretService", True),
    ("keyrings.alt.file", False),
    ("keyring.backends.null", False),
])
def test_only_native_secure_backends_are_selected(monkeypatch, module, accepted):
    backend = type("Keyring", (), {"__module__": module})()
    monkeypatch.setattr(credential_store.keyring, "get_keyring", lambda: backend)
    assert credential_store.backend() is (backend if accepted else None)


def test_chainer_selects_secure_backend_without_file_fallback(monkeypatch):
    secure = type("Keyring", (), {"__module__": "keyring.backends.SecretService"})()
    chain = type("ChainerBackend", (), {"__module__": "keyring.backends.chainer",
                                       "backends": [object(), secure]})()
    monkeypatch.setattr(credential_store.keyring, "get_keyring", lambda: chain)
    assert credential_store.backend() is secure


@pytest.mark.parametrize("operation", ["load", "save"])
def test_backend_errors_never_expose_credentials(operation):
    def broken(*args):
        raise RuntimeError("credential-value")

    store = SimpleNamespace(get_password=broken, set_password=broken)
    args = (store, "https://hub.example", "credential-value") if operation == "save" else (store, "https://hub.example")
    with pytest.raises(StepError) as error:
        getattr(credential_store, operation)(*args)
    assert "credential-value" not in str(error.value)
