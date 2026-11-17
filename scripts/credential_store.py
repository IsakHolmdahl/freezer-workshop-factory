"""Optional desktop storage for repository PATs via python-keyring."""
import keyring

from steps.common import StepError

SERVICE = "asf-agent-github"
SECURE_BACKENDS = {"keyring.backends.macOS", "keyring.backends.SecretService",
                   "keyring.backends.Windows", "keyring.backends.kwallet"}


def backend():
    """Use a native credential store; never select a plaintext/file fallback."""
    try:
        selected = keyring.get_keyring()
        candidates = selected.backends if type(selected).__module__ == "keyring.backends.chainer" else [selected]
        return next((item for item in candidates if type(item).__module__ in SECURE_BACKENDS), None)
    except Exception:
        return None


def load(store, account, *, service=SERVICE):
    try:
        return store.get_password(service, account)
    except Exception:
        raise StepError("Could not read the GitHub credential from the keyring",
                        ["unlock your Keychain or Secret Service database and retry"]) from None


def save(store, account, token, *, service=SERVICE):
    try:
        store.set_password(service, account, token)
    except Exception:
        raise StepError("Could not save the GitHub credential in the keyring",
                        ["unlock your Keychain or Secret Service database and retry"]) from None
