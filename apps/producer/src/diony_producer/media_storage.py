"""Local vault plus optional S3/R2 media delivery adapters."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _segment(value: str) -> str:
    clean = _SAFE.sub("-", value.strip()).strip(".-")
    if not clean or clean in {".", ".."}:
        raise ValueError("Storage path segments must have a portable name")
    return clean


def media_storage_key(
    workspace_id: str,
    project_id: str,
    *,
    version: int,
    category: str,
    filename: str,
) -> str:
    if version < 1:
        raise ValueError("Media versions start at one")
    name = PurePosixPath(filename).name
    if name != filename or name in {"", ".", ".."}:
        raise ValueError("Media filename must not contain a path")
    return "/".join(
        [
            "workspaces",
            _segment(workspace_id),
            "projects",
            _segment(project_id),
            f"v{version}",
            _segment(category),
            _segment(name),
        ]
    )


class S3CompatibleStorage:
    """Boto3-backed opt-in mirror for AWS S3 or Cloudflare R2."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("An S3-compatible bucket is required")
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.region = region
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError("Install Diony Producer with its boto3 dependency") from exc
            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
            )
        self.client = client

    @staticmethod
    def _validate_key(key: str) -> str:
        path = PurePosixPath(key)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("Unsafe object key")
        return str(path)

    def upload(self, source: Path, key: str, *, content_type: str) -> None:
        if not source.is_file():
            raise FileNotFoundError(source)
        self.client.upload_file(
            str(source),
            self.bucket,
            self._validate_key(key),
            ExtraArgs={"ContentType": content_type, "Metadata": {"diony-lineage": "preserved"}},
        )

    def signed_get_url(self, key: str, *, expires_seconds: int = 900) -> str:
        ttl = max(60, min(int(expires_seconds), 86_400))
        return str(
            self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": self._validate_key(key)},
                ExpiresIn=ttl,
            )
        )

    def delete_many(self, keys: list[str]) -> int:
        safe = sorted({self._validate_key(key) for key in keys})
        if not safe:
            return 0
        for offset in range(0, len(safe), 1000):
            batch = safe[offset : offset + 1000]
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
        return len(safe)
