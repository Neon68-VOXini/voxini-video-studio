"""API key storage via the OS credential store (Windows Credential Manager
on Windows, via the `keyring` package). Falls back to a local JSON file
ONLY when no OS keyring backend is available (e.g. this Linux dev sandbox,
or a locked-down machine) - that fallback stores the key in PLAINTEXT and
callers must surface `using_fallback_store()` to the user so they know the
stronger Windows Credential Manager path isn't active.

Originally Runway-only (get_runway_api_key/set_runway_api_key/
delete_runway_api_key). Task #665 (Kling/Seedance/Veo integration)
generalized this to a provider-id-keyed get_api_key()/set_api_key()/
delete_api_key() trio so new cloud providers don't each need their own
copy-pasted keyring glue; the original Runway-specific functions are kept
as thin wrappers so existing call sites (generation_service, setup_wizard)
keep working unchanged."""
from __future__ import annotations

import json
import os
from pathlib import Path

SERVICE_NAME = "VOXiniVideoStudio"
RUNWAY_KEY_NAME = "runway_api_key"
KLING_KEY_NAME = "kling_api_key"
SEEDANCE_KEY_NAME = "seedance_api_key"
VEO_KEY_NAME = "veo_api_key"

# provider id (Provider.id, e.g. "kling") -> credential store key name.
# Lets callers that only know the provider id (registry.py, setup_wizard.py)
# use the generic get_api_key(provider_id) below instead of hard-coding a
# per-provider function name.
_PROVIDER_KEY_NAMES: dict[str, str] = {
    "runway": RUNWAY_KEY_NAME,
    "kling": KLING_KEY_NAME,
    "seedance": SEEDANCE_KEY_NAME,
    "veo": VEO_KEY_NAME,
}


def _fallback_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    d = root / "VOXiniVideoStudio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fallback_path() -> Path:
    return _fallback_dir() / "credentials_fallback.json"


def _read_fallback(key_name: str) -> str | None:
    path = _fallback_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data.get(key_name) or None


def _write_fallback(key_name: str, value: str | None) -> None:
    path = _fallback_path()
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    if value:
        data[key_name] = value
    else:
        data.pop(key_name, None)
    path.write_text(json.dumps(data), encoding="utf-8")


def using_fallback_store() -> bool:
    """True if the OS keyring backend is unusable and the (plaintext,
    weaker) local-file fallback is what's actually storing the key. The UI
    should warn the user clearly when this is true."""
    try:
        import keyring
        from keyring.errors import NoKeyringError

        backend = keyring.get_keyring()
        # keyring's "fail" backend raises on first real use; detect it by
        # class name too, since some platforms only fail lazily.
        if type(backend).__module__.endswith("backends.fail"):
            return True
        # probe with a harmless get - many backends only truly fail here
        keyring.get_password(SERVICE_NAME, "__voxini_probe__")
        return False
    except Exception:
        return True


def get_api_key(provider_id: str) -> str | None:
    """Generic lookup by provider id (e.g. "kling", "seedance", "veo",
    "runway"). Raises KeyError for an id with no known credential-store key
    name - callers should only pass ids from _PROVIDER_KEY_NAMES."""
    key_name = _PROVIDER_KEY_NAMES[provider_id]
    try:
        import keyring

        value = keyring.get_password(SERVICE_NAME, key_name)
        if value:
            return value
    except Exception:
        pass
    return _read_fallback(key_name)


def set_api_key(provider_id: str, value: str) -> None:
    key_name = _PROVIDER_KEY_NAMES[provider_id]
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, key_name, value)
        return
    except Exception:
        _write_fallback(key_name, value)


def delete_api_key(provider_id: str) -> None:
    key_name = _PROVIDER_KEY_NAMES[provider_id]
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, key_name)
    except Exception:
        pass
    _write_fallback(key_name, None)


# -- Runway-specific wrappers (pre-existing call sites) -----------------

def get_runway_api_key() -> str | None:
    return get_api_key("runway")


def set_runway_api_key(value: str) -> None:
    set_api_key("runway", value)


def delete_runway_api_key() -> None:
    delete_api_key("runway")


# -- Kling/Seedance/Veo convenience wrappers (Task #665) -----------------

def get_kling_api_key() -> str | None:
    return get_api_key("kling")


def set_kling_api_key(value: str) -> None:
    set_api_key("kling", value)


def delete_kling_api_key() -> None:
    delete_api_key("kling")


def get_seedance_api_key() -> str | None:
    return get_api_key("seedance")


def set_seedance_api_key(value: str) -> None:
    set_api_key("seedance", value)


def delete_seedance_api_key() -> None:
    delete_api_key("seedance")


def get_veo_api_key() -> str | None:
    return get_api_key("veo")


def set_veo_api_key(value: str) -> None:
    set_api_key("veo", value)


def delete_veo_api_key() -> None:
    delete_api_key("veo")
