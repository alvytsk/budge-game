"""Fixtures for the media suite.

The bucket is created through the S3 API itself rather than by a CLI step
or an init container (the plan's ruling 9) — which is what lets the same
MinIO definition serve `compose.test.yaml` and a GitHub Actions `services:`
block unchanged.
"""

import asyncio
from collections.abc import AsyncIterator

import boto3
import pytest
import pytest_asyncio
from botocore.exceptions import ClientError

from budge.media.s3 import S3MediaStore
from support.db import S3_ACCESS_KEY, S3_BUCKET, S3_ENDPOINT, S3_SECRET_KEY

UNREACHABLE = (
    f"Cannot reach the test object store at {S3_ENDPOINT}.\n"
    "Start it with:  docker compose -f backend/compose.test.yaml up -d\n"
    "These tests fail rather than skip: a silently skipped integration suite "
    "reports green while proving nothing."
)


def _raw_client() -> object:
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name="us-east-1",
    )


def _ensure_bucket() -> None:
    client = _raw_client()
    try:
        client.create_bucket(Bucket=S3_BUCKET)  # type: ignore[attr-defined]
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise


def _empty_bucket() -> None:
    client = _raw_client()
    listed = client.list_objects_v2(Bucket=S3_BUCKET)  # type: ignore[attr-defined]
    for entry in listed.get("Contents", ()):
        client.delete_object(Bucket=S3_BUCKET, Key=entry["Key"])  # type: ignore[attr-defined]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def s3_bucket() -> None:
    try:
        await asyncio.to_thread(_ensure_bucket)
    except Exception as error:  # re-raised as a usable message
        pytest.fail(f"{UNREACHABLE}\n\nunderlying error: {error!r}")


@pytest_asyncio.fixture(loop_scope="session")
async def clean_bucket(s3_bucket: None) -> AsyncIterator[None]:
    # Emptied before the test, not after — the same reasoning `clean_db`
    # gives: cleaning on the way out leaves the store dirty for anything
    # that did not ask for this fixture.
    await asyncio.to_thread(_empty_bucket)
    yield


@pytest.fixture
def store() -> S3MediaStore:
    return S3MediaStore(
        endpoint=S3_ENDPOINT,
        access_key=S3_ACCESS_KEY,
        secret_key=S3_SECRET_KEY,
        bucket=S3_BUCKET,
    )
