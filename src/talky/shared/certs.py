"""Self-signed TLS cert generation for the talky daemon (ticket 1edf).

Pure-Python replacement for ``scripts/generate-certs.sh`` — works in wheel
installs without a repo clone. Uses the ``cryptography`` library (already
a transitive dep via pipecat).

Writes ``~/.talky/ssl/server-{cert,key}.pem``. Hostnames come from CLI
override, then ``network.allowed_hosts`` in settings.yaml, then a
``localhost`` fallback. ``localhost`` and ``127.0.0.1`` are always
included as SANs so loopback access stays valid.

Does NOT touch ``client/localhost-*.pem`` — that's a repo-only concern
and stays in the bash script.
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
from pathlib import Path
from typing import Iterable, List, Optional

SSL_DIR = Path.home() / ".talky" / "ssl"
CERT_PATH = SSL_DIR / "server-cert.pem"
KEY_PATH = SSL_DIR / "server-key.pem"

_CERT_VALID_DAYS = 365


def _hostnames_from_settings() -> List[str]:
    """Read ``network.allowed_hosts`` from settings.yaml, fall back to []."""
    try:
        from talky.shared.profile_manager import get_profile_manager

        settings = getattr(get_profile_manager(), "settings", {}) or {}
        raw = settings.get("network", {}).get("allowed_hosts") or []
        if isinstance(raw, str):
            raw = [raw]
        return [h.strip() for h in raw if isinstance(h, str) and h.strip()]
    except Exception:  # noqa: BLE001
        return []


def resolve_hostnames(override: Optional[str]) -> List[str]:
    """Build the SAN list. CLI override wins, else settings, else localhost."""
    if override:
        hosts = [override]
    else:
        hosts = _hostnames_from_settings()
    if not hosts:
        hosts = ["localhost"]
    # Always include localhost as a DNS SAN.
    if "localhost" not in hosts:
        hosts.append("localhost")
    # Dedup preserving order.
    seen = set()
    out: List[str] = []
    for h in hosts:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def cert_summary(cert_path: Path = CERT_PATH) -> Optional[dict]:
    """Read CN/SAN/expiry from an existing cert. Returns None if missing/invalid."""
    if not cert_path.exists():
        return None
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        return None
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes(), default_backend())
        cn_attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        cn = cn_attrs[0].value if cn_attrs else None
        sans: List[str] = []
        try:
            san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            for name in san_ext:
                sans.append(str(name.value))
        except x509.ExtensionNotFound:
            pass
        not_after = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after
        return {
            "cn": cn,
            "sans": sans,
            "not_after": not_after,
            "expired": not_after < _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=not_after.tzinfo),
        }
    except Exception:  # noqa: BLE001
        return None


def generate(hostnames: Iterable[str]) -> tuple[Path, Path]:
    """Generate a self-signed cert + key covering ``hostnames``.

    Writes to ``CERT_PATH`` and ``KEY_PATH``. First hostname becomes the CN.
    ``127.0.0.1`` is added as an IP SAN. Returns (cert_path, key_path).
    """
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    hosts = list(hostnames)
    if not hosts:
        raise ValueError("at least one hostname is required")

    SSL_DIR.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    cn = hosts[0]
    subject = issuer = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, cn)])

    san_entries: list = [x509.DNSName(h) for h in hosts]
    san_entries.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))

    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=1))
        .not_valid_after(now + _dt.timedelta(days=_CERT_VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(private_key=key, algorithm=hashes.SHA256(), backend=default_backend())
    )

    KEY_PATH.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    # tighten perms on the key
    try:
        KEY_PATH.chmod(0o600)
    except OSError:
        pass
    return CERT_PATH, KEY_PATH


def enable_https_in_settings(settings_path: Optional[Path] = None) -> bool:
    """Uncomment the ``network.https`` block in settings.yaml if commented.

    Conservative — only edits the bundled-default shape ``# https:`` / ``#   cert:`` /
    ``#   key:`` lines. If the user has already customized the file, leaves it alone
    and returns False.
    """
    path = settings_path or (Path.home() / ".talky" / "settings.yaml")
    if not path.exists():
        return False
    text = path.read_text()
    if "\n  https:" in text and "# https:" not in text:
        return False  # already enabled
    needles = ("# https:", "#   cert:", "#   key:")
    if not all(n in text for n in needles):
        return False
    new = text
    new = new.replace("  # https:", "  https:")
    new = new.replace("  #   cert:", "    cert:")
    new = new.replace("  #   key:", "    key:")
    if new == text:
        return False
    path.write_text(new)
    return True
