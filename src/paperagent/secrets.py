"""Credentials never enter product settings or CLI output."""
import os
import warnings

NAMES = ("LLM_API_KEY", "FAST_LLM_API_KEY")
_warned = False


def _validate(name):
    if name not in NAMES:
        raise ValueError("Unsupported credential name")


def get_secret(name):
    global _warned
    _validate(name)
    try:
        import keyring
        value = keyring.get_password("paperagent", name)
        if value:
            return value
    except Exception:
        if not _warned:
            warnings.warn("System credential store unavailable; using environment fallback.", RuntimeWarning)
            _warned = True
    return os.environ.get("PAPERAGENT_" + name)


def set_secret(name, value):
    _validate(name)
    if not value:
        raise ValueError("Credential cannot be empty")
    import keyring
    try:
        keyring.set_password("paperagent", name, value)
    except Exception:
        raise RuntimeError("System credential store unavailable; credential was not saved") from None


def list_names():
    """Application-owned directory; no backend enumeration or secret output."""
    return [name for name in NAMES if get_secret(name)]


def command(args):
    if args.secrets_action == "list":
        print("\n".join(list_names()))
    else:
        from getpass import getpass
        set_secret(args.name, getpass("Credential: "))
        print("Credential saved.")
