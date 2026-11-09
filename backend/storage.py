"""Private S3 object operations; no client, credentials or network on import.

Only opaque UUID keys enter this module. Application URLs and user supplied
filenames are deliberately never used as object keys or bucket permissions.
"""
import base64
import hashlib
import re

from flask import current_app


_KEY = re.compile(r"files/[0-9a-f]{32}\Z")
_CHUNK = 128 * 1024


def _check_key(key):
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("Invalid private object key")
    return key


def _client():
    """Share a client within one Flask app, without creating it at startup."""
    app = current_app._get_current_object()
    client = app.extensions.get("zhihe.s3_client")
    if client is None:
        import boto3
        from botocore.config import Config
        args = {"region_name": app.config["S3_REGION"],
                "config": Config(s3={"addressing_style": "path"},
                                 retries={"mode": "standard", "max_attempts": 3},
                                 connect_timeout=5, read_timeout=30)}
        if app.config.get("S3_ENDPOINT_URL"):
            args["endpoint_url"] = app.config["S3_ENDPOINT_URL"]
        if app.config.get("S3_ACCESS_KEY_ID"):
            args["aws_access_key_id"] = app.config["S3_ACCESS_KEY_ID"]
            args["aws_secret_access_key"] = app.config["S3_SECRET_ACCESS_KEY"]
            if app.config.get("S3_SESSION_TOKEN"):
                args["aws_session_token"] = app.config["S3_SESSION_TOKEN"]
        client = boto3.client("s3", **args)
        app.extensions["zhihe.s3_client"] = client
    return client


def _bucket():
    bucket = current_app.config.get("S3_BUCKET")
    if not bucket:
        raise ValueError("S3_BUCKET is required to access S3 assets")
    return bucket


def _verify_metadata(payload, size, digest):
    if (int(payload.get("ContentLength", -1)) != size or
            payload.get("Metadata", {}).get("sha256") != digest):
        raise IOError("Private object size or checksum metadata mismatch")


def put_file(key, path, size, digest):
    """Upload a validated staged file and confirm the committed object exists.

    AWS verifies ChecksumSHA256 during upload. S3-compatible endpoints may
    lack checksum-header support: after HEAD, stream the entire object back
    and verify its SHA-256 before the registry row becomes ready.
    """
    _check_key(key)
    options = {"Bucket": _bucket(), "Key": key, "ContentLength": size,
               "ContentType": "application/octet-stream", "Metadata": {"sha256": digest}}
    endpoint = current_app.config.get("S3_ENDPOINT_URL")
    if not endpoint:
        options["ChecksumSHA256"] = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
    with open(path, "rb") as staged:
        _client().put_object(Body=staged, **options)
    _verify_metadata(_client().head_object(Bucket=_bucket(), Key=key), size, digest)
    if endpoint:
        # A HEAD response repeats caller-provided metadata; checking GET bytes
        # catches servers that silently ignore checksum headers. No local copy.
        read_to_file(key, None, size, digest, size)


def read_to_file(key, output, size, digest, maximum):
    """Fetch and verify an object into a caller-owned temporary *disk* file."""
    _check_key(key)
    if not isinstance(size, int) or not 0 <= size <= maximum or not isinstance(digest, str):
        raise IOError("Invalid private object metadata")
    obj = _client().get_object(Bucket=_bucket(), Key=key)
    body = obj["Body"]
    try:
        _verify_metadata(obj, size, digest)
        checksum = hashlib.sha256()
        actual = 0
        while True:
            chunk = body.read(_CHUNK)
            if not chunk:
                break
            actual += len(chunk)
            if actual > size or actual > maximum:
                raise IOError("Private object exceeds recorded length")
            if output is not None:
                output.write(chunk)
            checksum.update(chunk)
        if actual != size or checksum.hexdigest() != digest:
            raise IOError("Private object contents do not match recorded checksum")
        if output is not None:
            output.seek(0)
    finally:
        body.close()


def delete_file(key):
    """Delete an owned key; S3 DELETE is idempotent for an absent object."""
    _check_key(key)
    _client().delete_object(Bucket=_bucket(), Key=key)
