"""Shared real-stack state and job capability client.

The stack describes existing resources; fixtures still own their lifecycle.
Client calls use the real HTTP/mTLS routes and never infer a Run terminal state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from support import f00b
from support import stack as st
from support.certs import StackCertificates

@dataclass
class Stack:
    """Real API, database, object store and identities for signed capability tests."""

    server: st.ApiServer
    dsn: str
    dbname: str
    object_dir: Path
    certificates: StackCertificates
    binaries: dict[str, Path]
    signing_key: str

    def client(self) -> st.HttpClient:
        return st.HttpClient(self.server.base_url)

    def internal_client(
        self, role: str = "maintenance", certificates: StackCertificates | None = None
    ) -> "JobCapabilityClient":
        certificates = certificates or self.certificates
        return JobCapabilityClient(
            certificates.internal_url,
            certificates.ca.cert,
            certificates.client(role),
        )


class JobCapabilityClient(f00b.InternalClient):
    """The real mTLS client for job-scoped artifacts and model calls."""

    def upload(
        self,
        job_id: str,
        attempt_token: str,
        *,
        media_type: str = "application/json",
        size_bytes: int = 64,
        purpose: str = "report",
        **kwargs,
    ):
        return self.post(
            f"/internal/v1/background/jobs/{job_id}/artifact-uploads",
            {
                "attempt_token": attempt_token,
                "media_type": media_type,
                "size_bytes": size_bytes,
                "purpose": purpose,
            },
            **kwargs,
        )

    def complete(self, job_id: str, artifact_id: str, attempt_token: str, sha256: str, **kwargs):
        return self.post(
            f"/internal/v1/background/jobs/{job_id}/artifacts/{artifact_id}/complete",
            {"attempt_token": attempt_token, "sha256": sha256},
            **kwargs,
        )

    def download(self, job_id: str, attempt_token: str, artifact_id: str, **kwargs):
        return self.post(
            f"/internal/v1/background/jobs/{job_id}/artifact-downloads",
            {"attempt_token": attempt_token, "artifact_id": artifact_id},
            **kwargs,
        )

    def artifact_status(self, job_id: str, attempt_token: str, artifact_id: str, **kwargs):
        return self.post(
            f"/internal/v1/background/jobs/{job_id}/artifact-status",
            {"attempt_token": attempt_token, "artifact_id": artifact_id},
            **kwargs,
        )

    def model_call(
        self,
        job_id: str,
        attempt_token: str,
        *,
        logical_key: str,
        model_profile_version_id: str,
        params: dict | None = None,
        **kwargs,
    ):
        return self.post(
            f"/internal/v1/background/jobs/{job_id}/model-calls",
            {
                "attempt_token": attempt_token,
                "logical_key": logical_key,
                "model_profile_version_id": model_profile_version_id,
                "params": params if params is not None else {"task": "model_compatibility"},
            },
            **kwargs,
        )

