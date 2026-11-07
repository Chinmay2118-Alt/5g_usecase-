"""Generate a self-signed TLS certificate for this laptop's LAN addresses.

WebXR only runs in a secure context, so the Quest must load the page over
HTTPS. Usage:  python -m server.certs [--ip 10.0.4.160 ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import os
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

DEFAULT_CERT_DIR = Path(__file__).resolve().parent.parent / "certs"
CERT_DIR = Path(os.environ.get("CERT_DIR", str(DEFAULT_CERT_DIR)))
CERT = CERT_DIR / "cert.pem"
KEY = CERT_DIR / "key.pem"


def local_ipv4s() -> list[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    try:  # the address used for the default route
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    ips.discard("0.0.0.0")
    return sorted(ips)


def make_cert(extra_ips: list[str] | None = None) -> list[str]:
    env_hosts: list[str] = []
    for var in ("SERVER_IP", "EXTRA_IPS"):
        val = os.environ.get(var, "").strip()
        if val:
            for item in val.replace(",", " ").split():
                if item:
                    env_hosts.append(item)

    combined = set(local_ipv4s()) | {"127.0.0.1"} | set(extra_ips or []) | set(env_hosts)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "5G Remote Hands (dev)")])

    san: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.DNSName(socket.gethostname()),
    ]
    recorded_names: list[str] = []

    for item in sorted(combined):
        try:
            ip_obj = ipaddress.ip_address(item)
            san.append(x509.IPAddress(ip_obj))
            recorded_names.append(str(ip_obj))
        except ValueError:
            san.append(x509.DNSName(item))
            recorded_names.append(item)

    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    KEY.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return recorded_names


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ip", action="append", default=[], help="extra IP or DNS hostname to include (repeatable)")
    args = ap.parse_args()
    ips = make_cert(args.ip)
    print(f"Wrote {CERT} and {KEY} for: {', '.join(ips)}")

