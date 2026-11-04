"""Probe configuration: non-secret flags, credentials from the environment only.

A command line is visible to every local user (process list, shell history,
evidence quoting), so access keys and secret keys are accepted **only** through
``F15A_S3_*`` environment variables. Non-secret target data may come from flags so
an operator can point the probe at a candidate without exporting the world; flags
win over the environment.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_REGION = "us-east-1"
DEFAULT_PREFIX = "orchara-probe"
DEFAULT_TIMEOUT = 10.0
DEFAULT_SHORT_TTL = 5
DEFAULT_EXPIRY_SKEW = 0.0
DEFAULT_RETRIES = 2

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


class ConfigError(Exception):
    """A configuration/environment failure: the probe must exit 2, not pass."""

    def __init__(self, code: str, message: str, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.missing = list(missing or [])

    def as_dict(self) -> dict:
        payload = {"code": self.code, "message": self.message}
        if self.missing:
            payload["missing"] = self.missing
        return payload


class ProbeEnvironmentError(Exception):
    """The configured target could not be used (network/TLS/HTTP transport)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class Target:
    scheme: str
    hostname: str  # raw host used for the TCP connection
    host_header: str  # exactly what the Host header and SigV4 host use
    port: int
    base_path: str  # "" or a prefix such as "/s3"
    region: str
    bucket: str
    tenant_prefix: str
    access_key: str
    secret_key: str
    session_token: str | None
    provision_access_key: str | None
    provision_secret_key: str | None
    provision_session_token: str | None
    timeout: float
    short_ttl: int
    expiry_skew: float
    retries: int
    ca_file: str | None
    label: str

    @property
    def endpoint(self) -> str:
        return f"{self.scheme}://{self.host_header}{self.base_path}"

    @property
    def provision_configured(self) -> bool:
        return bool(self.provision_access_key and self.provision_secret_key)

    def public_dict(self) -> dict:
        """Evidence-safe projection: no credential values, no secret lengths."""
        return {
            "endpoint": self.endpoint,
            "endpoint_host": self.host_header,
            "scheme": self.scheme,
            "bucket": self.bucket,
            "region": self.region,
            "tenant_prefix": self.tenant_prefix,
            "addressing_style": "path",
            "short_ttl_seconds": self.short_ttl,
            "expiry_skew_seconds": self.expiry_skew,
            "timeout_seconds": self.timeout,
            "retries": self.retries,
            "ca_file_configured": bool(self.ca_file),
            "credentials_present": {
                "runtime": True,
                "provisioning": self.provision_configured,
            },
            "profile": self.label,
        }


def _first(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _number(raw: str | float, *, name: str, minimum: float, maximum: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError("config_invalid", f"{name} is not a number") from exc
    if value < minimum or value > maximum:
        raise ConfigError("config_invalid", f"{name} must be between {minimum} and {maximum}")
    return value


def _secret(env: dict[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _validate_endpoint(raw_endpoint: str) -> tuple[str, str, str, int, str]:
    parsed = urlsplit(raw_endpoint.strip())
    if parsed.scheme not in ("http", "https"):
        raise ConfigError(
            "config_invalid", "endpoint must be an http:// or https:// URL"
        )
    if not parsed.hostname:
        raise ConfigError("config_invalid", "endpoint has no host")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ConfigError(
            "config_invalid", "endpoint must not carry credentials, a query or a fragment"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("config_invalid", f"endpoint port is invalid: {exc}") from exc
    port = port or (443 if parsed.scheme == "https" else 80)
    host = parsed.hostname
    connect_host = host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # IPv6 literal in the Host header
    default_port = (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    )
    host_header = host if default_port else f"{host}:{port}"
    base_path = parsed.path.rstrip("/")
    if base_path and ".." in base_path.split("/"):
        raise ConfigError("config_invalid", "endpoint base path must not contain '..'")
    return parsed.scheme, connect_host, host_header, port, base_path


def load_target(args, env: dict[str, str] | None = None) -> Target:
    """Build the probe target or raise :class:`ConfigError` listing what is missing."""
    env = dict(os.environ if env is None else env)
    missing: list[str] = []

    endpoint = _first(getattr(args, "endpoint", None), env.get("F15A_S3_ENDPOINT"))
    bucket = _first(getattr(args, "bucket", None), env.get("F15A_S3_BUCKET"))
    access_key = _secret(env, "F15A_S3_ACCESS_KEY")
    secret_key = _secret(env, "F15A_S3_SECRET_KEY")

    if not endpoint:
        missing.append("endpoint")
    if not bucket:
        missing.append("bucket")
    if not access_key:
        missing.append("access_key")
    if not secret_key:
        missing.append("secret_key")
    if missing:
        raise ConfigError(
            "config_missing",
            "missing required object-store configuration: " + ", ".join(missing),
            missing,
        )

    assert endpoint is not None and bucket is not None and access_key and secret_key
    scheme, connect_host, host_header, port, base_path = _validate_endpoint(endpoint)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", bucket):
        raise ConfigError("config_invalid", "bucket name is not a valid S3 bucket name")

    region = (
        _first(getattr(args, "region", None), env.get("F15A_S3_REGION")) or DEFAULT_REGION
    )
    if not _SLUG.fullmatch(region):
        raise ConfigError("config_invalid", "region is not a valid region name")

    prefix = (
        _first(getattr(args, "tenant_prefix", None), env.get("F15A_PREFIX"))
        or DEFAULT_PREFIX
    ).strip("/")
    segments = prefix.split("/")
    if not prefix or any(segment in ("", ".", "..") for segment in segments):
        raise ConfigError(
            "config_invalid", "tenant prefix must be a non-empty relative key prefix"
        )
    if any(char in prefix for char in "\\?#%"):
        raise ConfigError("config_invalid", "tenant prefix contains a reserved character")

    timeout = _number(
        getattr(args, "timeout", None) or env.get("F15A_S3_TIMEOUT") or DEFAULT_TIMEOUT,
        name="timeout",
        minimum=0.2,
        maximum=300,
    )
    short_ttl = int(
        _number(
            getattr(args, "short_ttl", None) or DEFAULT_SHORT_TTL,
            name="short_ttl",
            minimum=5,
            maximum=900,
        )
    )
    if len(access_key) < 3 or len(secret_key) < 8:
        raise ConfigError(
            "config_invalid", "access key or secret key is implausibly short"
        )
    expiry_skew = _number(
        getattr(args, "expiry_skew", None)
        if getattr(args, "expiry_skew", None) is not None
        else env.get("F15A_S3_EXPIRY_SKEW") or DEFAULT_EXPIRY_SKEW,
        name="expiry_skew",
        minimum=0,
        maximum=900,
    )
    retries = int(
        _number(
            getattr(args, "retries", None)
            if getattr(args, "retries", None) is not None
            else env.get("F15A_S3_RETRIES") or DEFAULT_RETRIES,
            name="retries",
            minimum=0,
            maximum=5,
        )
    )
    ca_file = _first(getattr(args, "ca_file", None), env.get("F15A_S3_CA_FILE"))
    if ca_file and not Path(ca_file).is_file():
        raise ConfigError("config_invalid", "CA file does not exist")
    label = (
        _first(getattr(args, "profile", None), env.get("F15A_PROFILE")) or "candidate"
    )

    provision_access = _secret(env, "F15A_S3_PROVISION_ACCESS_KEY")
    provision_secret = _secret(env, "F15A_S3_PROVISION_SECRET_KEY")
    if bool(provision_access) != bool(provision_secret):
        raise ConfigError(
            "config_invalid",
            "provisioning credentials must set both access key and secret key",
        )
    if provision_access and (len(provision_access) < 3 or len(provision_secret or "") < 8):
        raise ConfigError(
            "config_invalid", "provisioning access key or secret key is implausibly short"
        )

    return Target(
        scheme=scheme,
        hostname=connect_host,
        host_header=host_header,
        port=port,
        base_path=base_path,
        region=region,
        bucket=bucket,
        tenant_prefix=prefix,
        access_key=access_key,
        secret_key=secret_key,
        session_token=_secret(env, "F15A_S3_SESSION_TOKEN"),
        provision_access_key=provision_access,
        provision_secret_key=provision_secret,
        provision_session_token=_secret(env, "F15A_S3_PROVISION_SESSION_TOKEN"),
        timeout=timeout,
        short_ttl=short_ttl,
        expiry_skew=expiry_skew,
        retries=retries,
        ca_file=ca_file,
        label=label,
    )
