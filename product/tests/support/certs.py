"""Real X.509 material for the internal mTLS tests.

Certificates are generated into a temporary directory per test session: a CA, a
server certificate with ``IP:127.0.0.1``/``DNS:localhost`` SANs, and one client
certificate per internal role (the role is the certificate's Organization, which
is exactly what the server checks). A second, unrelated CA plus a client
certificate it signs is generated on demand for the "wrong CA" handshake test.

Nothing is committed and nothing is logged: the tests write PEM files under
``tmp_path`` and remove them with the temporary directory.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROLES = ("maintenance", "supervisor", "worker")


@dataclass(frozen=True)
class Certificate:
    cert: Path
    key: Path


@dataclass(frozen=True)
class CertificateAuthority:
    cert: Path
    key: Path


@dataclass(frozen=True)
class ClientCertificate:
    role: str
    cert: Path
    key: Path


@dataclass(frozen=True)
class StackCertificates:
    """Everything the API and the maintenance client need for one test stack."""

    ca: CertificateAuthority
    server: Certificate
    clients: dict[str, ClientCertificate]
    internal_addr: str
    internal_url: str

    def client(self, role: str) -> ClientCertificate:
        return self.clients[role]


def _private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _write_key(path: Path, key) -> None:  # noqa: ANN001
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _write_certificate(path: Path, certificate: x509.Certificate) -> None:
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def _validity() -> tuple[dt.datetime, dt.datetime]:
    now = dt.datetime.now(dt.timezone.utc)
    return now - dt.timedelta(minutes=5), now + dt.timedelta(days=2)


def make_ca(directory: Path, common_name: str = "Orchara internal test CA") -> CertificateAuthority:
    directory.mkdir(parents=True, exist_ok=True)
    key = _private_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
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
    cert_path = directory / f"{common_name.replace(' ', '-').lower()}-ca.pem"
    key_path = directory / f"{common_name.replace(' ', '-').lower()}-ca-key.pem"
    _write_certificate(cert_path, certificate)
    _write_key(key_path, key)
    return CertificateAuthority(cert=cert_path, key=key_path)


def _load_ca_key(authority: CertificateAuthority):
    return serialization.load_pem_private_key(authority.key.read_bytes(), password=None)


def make_server_certificate(authority: CertificateAuthority, directory: Path) -> Certificate:
    directory.mkdir(parents=True, exist_ok=True)
    key = _private_key()
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "orchara-internal"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "orchara-platform"),
    ])
    not_before, not_after = _validity()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(authority_cert(authority).subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
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
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(_load_ca_key(authority), hashes.SHA256())
    )
    cert_path = directory / "server.pem"
    key_path = directory / "server-key.pem"
    _write_certificate(cert_path, certificate)
    _write_key(key_path, key)
    return Certificate(cert=cert_path, key=key_path)


def make_client_certificate(
    authority: CertificateAuthority, directory: Path, role: str
) -> ClientCertificate:
    directory.mkdir(parents=True, exist_ok=True)
    key = _private_key()
    # The Organization is the internal role; the API refuses a certificate with
    # zero or several organizations, or one outside the fixed set.
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"orchara-{role}"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, role),
    ])
    not_before, not_after = _validity()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(authority_cert(authority).subject)
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
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(_load_ca_key(authority), hashes.SHA256())
    )
    cert_path = directory / f"client-{role}.pem"
    key_path = directory / f"client-{role}-key.pem"
    _write_certificate(cert_path, certificate)
    _write_key(key_path, key)
    return ClientCertificate(role=role, cert=cert_path, key=key_path)


def authority_cert(authority: CertificateAuthority) -> x509.Certificate:
    return x509.load_pem_x509_certificate(authority.cert.read_bytes())


def make_stack_certificates(directory: Path, internal_addr: str) -> StackCertificates:
    """Create CA + server + one client certificate per internal role."""
    directory.mkdir(parents=True, exist_ok=True)
    authority = make_ca(directory / "ca")
    server = make_server_certificate(authority, directory / "server")
    clients = {
        role: make_client_certificate(authority, directory / "clients", role)
        for role in ROLES
    }
    return StackCertificates(
        ca=authority,
        server=server,
        clients=clients,
        internal_addr=internal_addr,
        internal_url=f"https://{internal_addr}",
    )


def unique_internal_addr() -> tuple[str, int]:
    """A free loopback port for the internal listener."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    return f"127.0.0.1:{port}", port
