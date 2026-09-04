"""Runway API key storage via the OS credential store (Windows Credential
Manager on Windows, via the `keyring` package). Falls back to a local JSON
file ONLY when no OS keyring backend is available (e.g. this Linux dev
sandbox, or a locked-down machine) - that fallback stores the key in
PLAINTEXT and callers must surface `using_fallback_store()` to the user so
they know the stronger Windows Credential Manager path isn't active.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SERVICE_NAME = "VOXiniVideoStudio"
RUNWAY_KEY_NAME = "runway_api_key"


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


def get_runway_api_key() -> str | None:
    try:
        import keyring

        value = keyring.get_password(SERVICE_NAME, RUNWAY_KEY_NAME)
        if value:
            return value
    except Exception:
        pass
    return _read_fallback(RUNWAY_KEY_NAME)


def set_runway_api_key(value: str) -> None:
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, RUNWAY_KEY_NAME, value)
        return
    except Exception:
        _write_fallback(RUNWAY_KEY_NAME, value)


def delete_runway_api_key() -> None:
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, RUNWAY_KEY_NAME)
    except Exception:
        pass
    _write_fallback(RUNWAY_KEY_NAME, None)
