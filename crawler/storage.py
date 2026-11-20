"""Artifact storage (S3 API). Objects are private; nothing here is ever served directly to the public."""
from __future__ import annotations

import gzip
import hashlib
from typing import Protocol

from common.config import Settings

from .result import Artifact


class StorageError(RuntimeError):
    """Uploading or reading an artefact failed. ``code`` is the S3 error code when known."""

    def __init__(self, operation: str, code: str) -> None:
        super().__init__(f"{operation}: {code}")
        self.operation, self.code = operation, code


class ArtifactStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str, *, content_encoding: str | None = None) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class S3Store:
    def __init__(self, settings: Settings, client=None) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = settings.s3_bucket
        self.client = client or boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key.get_secret_value() if settings.s3_access_key else None,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value() if settings.s3_secret_key else None,
            region_name="us-east-1",
            config=Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=30,
                          s3={"addressing_style": "path"},
                          # Only send/verify checksums when the API requires them: newer boto3 defaults to
                          # streaming CRC trailers that not every S3-compatible store implements.
                          request_checksum_calculation="when_required",
                          response_checksum_validation="when_required"),
        )
        self._bucket_ready = settings.s3_assume_bucket  # the crawler's write-only key cannot check or create it

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)
        self._bucket_ready = True

    def put(self, key: str, data: bytes, content_type: str, *, content_encoding: str | None = None) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        extra = {"ContentEncoding": content_encoding} if content_encoding else {}
        try:
            self._ensure_bucket()
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, **extra)
        except ClientError as exc:
            raise StorageError("put_object", exc.response.get("Error", {}).get("Code", "ClientError")) from exc
        except BotoCoreError as exc:
            raise StorageError("put_object", type(exc).__name__) from exc

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            raise StorageError("delete_object", exc.response.get("Error", {}).get("Code", "ClientError")) from exc
        except BotoCoreError as exc:
            raise StorageError("delete_object", type(exc).__name__) from exc


class MemoryStore:
    """For tests and local development."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str, *, content_encoding: str | None = None) -> None:
        self.objects[key] = (data, content_type)

    def get(self, key: str) -> bytes:
        return self.objects[key][0]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


def save(store: ArtifactStore, key: str, data: bytes, mime_type: str, *, compress: bool = False,
         width: int | None = None, height: int | None = None) -> Artifact:
    """Store ``data`` (gzip-compressed if asked). The hash and size describe the original bytes."""
    digest = hashlib.sha256(data).hexdigest()
    body = gzip.compress(data, mtime=0) if compress else data
    store.put(key, body, mime_type, content_encoding="gzip" if compress else None)
    return Artifact(key=key, sha256=digest, size=len(data), mime_type=mime_type, width=width, height=height)
