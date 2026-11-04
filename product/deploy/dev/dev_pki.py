#!/usr/bin/env python3
"""Dev-only X.509 material for the platform's internal mTLS listener.

The internal listener (`ORCHARA_INTERNAL_*`) is certificate-authenticated and the
API refuses a client certificate whose Organization is not one of the fixed
internal roles. A clean development environment therefore needs a CA, a server
certificate for 127.0.0.1/localhost and one client certificate per role before
`cmd/maintenance` can claim work.

This module is **development plumbing**, not a production PKI:

* keys are generated on demand, written under the git-ignored dev state root and
  never printed, logged or committed;
* validity is short (two days) and there is no revocation or rotation story;
* it is a deployer convenience so the documented bring-up is reproducible on a
  machine with Python, not a replacement for a real secret manager.

Usage::

    python deploy/dev/dev_pki.py --dir .devstack/certs
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import sys
from dataclasses import dataclass
from pathlib import Path

ROLES = ("maintenance", "supervisor", "worker")
DEFAULT_HOST = "127.0.0.1"


@dataclass(frozen=True)
class DevCertificates:
    """Paths to the dev CA, server certificate and per-role client certificates."""

    directory: Path
    ca_cert: Path
    ca_key: Path
    server_cert: Path
    server_key: Path
    client_certs: dict[str, Path]
    client_keys: dict[str, Path]

    def client(self, role: str) -> tuple[Path, Path]:
        return self.client_certs[role], self.client_keys[role]


def _require_cryptography():  # noqa: ANN202 - returns the module
    try:
        from cryptography import x509  # noqa: F401
        from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric import ec  # noqa: F401
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "dev_pki needs the 'cryptography' package (used by the product test "
            "environment); install it with: python -m pip install cryptography\n"
            f"import error: {exc}"
        ) from exc


def _private_key():
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1())


def _write_key(path: Path, key) -> None:  # noqa: ANN001
    from cryptography.hazmat.primitives import serialization

    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows
        pass


def _write_certificate(path: Path, certificate) -> None:  # noqa: ANN001
    from cryptography.hazmat.primitives import serialization

    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def _validity() -> tuple[dt.datetime, dt.datetime]:
    now = dt.datetime.now(dt.timezone.utc)
    return now - dt.timedelta(minutes=5), now + dt.timedelta(days=2)


def _load_ca_key(authority):
    from cryptography.hazmat.primitives import serialization

    return serialization.load_pem_private_key(authority.read_bytes(), password=None)


def _make_ca(directory: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID

    key = _private_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "orchara dev internal CA")])
    not_before, not_after = _validity()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "ca.pem"
    key_path = directory / "ca-key.pem"
    _write_certificate(cert_path, certificate)
    _write_key(key_path, key)
    return cert_path, key_path


def _load_certificate(path: Path):
    from cryptography import x509

    return x509.load_pem_x509_certificate(path.read_bytes())


def _make_server(directory: Path, ca_cert: Path, ca_key: Path, host: str) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = _private_key()
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "orchara-dev-internal"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "orchara-platform"),
        ]
    )
    not_before, not_after = _validity()
    san = [x509.DNSName("localhost")]
    try:
        san.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:  # a DNS hostname, not an IP
        san.append(x509.DNSName(host))
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(_load_certificate(ca_cert).subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(_load_ca_key(ca_key), hashes.SHA256())
    )
    cert_path = directory / "server.pem"
    key_path = directory / "server-key.pem"
    _write_certificate(cert_path, certificate)
    _write_key(key_path, key)
    return cert_path, key_path


def _make_client(
    directory: Path, ca_cert: Path, ca_key: Path, role: str
) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = _private_key()
    # The Organization is the internal role; the API refuses zero/several
    # organizations and any value outside the fixed set.
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, f"orchara-dev-{role}"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, role),
        ]
    )
    not_before, not_after = _validity()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(_load_certificate(ca_cert).subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .sign(_load_ca_key(ca_key), hashes.SHA256())
    )
    cert_path = directory / f"client-{role}.pem"
    key_path = directory / f"client-{role}-key.pem"
    _write_certificate(cert_path, certificate)
    _write_key(key_path, key)
    return cert_path, key_path


def ensure(
    directory: Path, *, host: str = DEFAULT_HOST, roles: tuple[str, ...] = ROLES
) -> tuple[DevCertificates, bool]:
    """Create the dev certificate set if absent; return it and whether it was created.

    An existing set is reused so repeated `bringup.py up` runs do not invalidate
    a running maintenance process. Deleting the directory forces regeneration.
    """
    _require_cryptography()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    server_cert = directory / "server.pem"
    server_key = directory / "server-key.pem"
    ca_cert = directory / "ca.pem"
    ca_key = directory / "ca-key.pem"
    wanted = all(
        (directory / f"client-{role}.pem").is_file()
        and (directory / f"client-{role}-key.pem").is_file()
        for role in roles
    )
    if server_cert.is_file() and server_key.is_file() and ca_cert.is_file() and ca_key.is_file() and wanted:
        return (
            DevCertificates(
                directory=directory,
                ca_cert=ca_cert,
                ca_key=ca_key,
                server_cert=server_cert,
                server_key=server_key,
                client_certs={role: directory / f"client-{role}.pem" for role in roles},
                client_keys={role: directory / f"client-{role}-key.pem" for role in roles},
            ),
            False,
        )

    ca_cert, ca_key = _make_ca(directory)
    server_cert, server_key = _make_server(directory, ca_cert, ca_key, host)
    client_certs: dict[str, Path] = {}
    client_keys: dict[str, Path] = {}
    for role in roles:
        cert, key = _make_client(directory, ca_cert, ca_key, role)
        client_certs[role] = cert
        client_keys[role] = key
    return (
        DevCertificates(
            directory=directory,
            ca_cert=ca_cert,
            ca_key=ca_key,
            server_cert=server_cert,
            server_key=server_key,
            client_certs=client_certs,
            client_keys=client_keys,
        ),
        True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="generate the dev internal-mTLS certificate set")
    parser.add_argument("--dir", required=True, help="directory to write the PEM files into")
    parser.add_argument("--host", default=DEFAULT_HOST, help="server certificate SAN host")
    parser.add_argument(
        "--force", action="store_true", help="delete and regenerate an existing set"
    )
    args = parser.parse_args(argv)
    directory = Path(args.dir)
    if args.force:
        for path in directory.glob("*.pem"):
            path.unlink()
    certificates, created = ensure(directory, host=args.host)
    state = "created" if created else "reused"
    print(f"dev internal mTLS material {state} under {certificates.directory}")
    for name in ("ca", "server"):
        print(f"  {name}: {certificates.directory / (name + '.pem')}")
    for role in sorted(certificates.client_certs):
        print(f"  client {role}: {certificates.client_certs[role]}")
    print("private keys are in the same directory and are never printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
