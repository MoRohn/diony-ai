from __future__ import annotations

from pathlib import Path

from diony_producer.media_storage import S3CompatibleStorage, media_storage_key


class _FakeS3:
    def __init__(self) -> None:
        self.uploads: list[tuple] = []

    def upload_file(self, source, bucket, key, ExtraArgs=None):
        self.uploads.append((source, bucket, key, ExtraArgs))

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return f"https://objects.example/{Params['Bucket']}/{Params['Key']}?ttl={ExpiresIn}"


def test_s3_compatible_storage_uses_portable_project_version_keys(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"owned-media")
    key = media_storage_key(
        "workspace-one", "project-one", version=3, category="shots", filename="clip.mp4"
    )
    assert key == "workspaces/workspace-one/projects/project-one/v3/shots/clip.mp4"
    fake = _FakeS3()
    storage = S3CompatibleStorage(
        bucket="diony-media",
        endpoint_url="https://account.r2.cloudflarestorage.com",
        region="auto",
        client=fake,
    )
    storage.upload(source, key, content_type="video/mp4")
    signed = storage.signed_get_url(key, expires_seconds=600)
    assert fake.uploads[0][1:3] == ("diony-media", key)
    assert "ttl=600" in signed
