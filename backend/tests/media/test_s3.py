"""The store, against a real S3-compatible server."""

import asyncio

import pytest

from podvinsya.media.digest import digest_of
from podvinsya.media.s3 import S3MediaStore
from podvinsya.services.ports import MediaUnavailable
from support.db import S3_ACCESS_KEY, S3_BUCKET, S3_ENDPOINT, S3_SECRET_KEY

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
OTHER = b"\x89PNG\r\n\x1a\n" + b"different"

# Port 1 is reserved and nothing listens on it, so the connection is
# refused immediately rather than hanging until a TCP timeout.
CLOSED_PORT = "http://127.0.0.1:1"


def unreachable_store() -> S3MediaStore:
    return S3MediaStore(
        endpoint=CLOSED_PORT,
        access_key=S3_ACCESS_KEY,
        secret_key=S3_SECRET_KEY,
        bucket=S3_BUCKET,
    )


async def test_bytes_come_back_exactly_as_they_went_in(
    clean_bucket: None, store: S3MediaStore
) -> None:
    digest = await store.put(PNG)
    assert await store.get(digest) == PNG


async def test_the_digest_it_returns_is_the_digest_of_the_bytes(
    clean_bucket: None, store: S3MediaStore
) -> None:
    """§7.6: the address is the content.

    Kills on: keying on anything else — a uuid, a counter — which would
    still round-trip, and would silently stop being content addressing."""
    assert await store.put(PNG) == digest_of(PNG)


async def test_storing_the_same_bytes_twice_is_one_object(
    clean_bucket: None, store: S3MediaStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling 3: content addressing makes an upload idempotent by
    construction, so the second `put` writes nothing.

    The write is counted rather than inferred from the result — the digests
    match and the bytes round-trip whether or not the object was rewritten,
    so an assertion on either would pass against a store that wrote every
    time. `put_object` is the subject here, which is why the test reaches
    for it.

    Kills on: writing unconditionally — harmless against MinIO today, and
    wrong the moment the bucket is versioned, replicated or billed by
    write."""
    writes = 0
    original = store._client.put_object

    def counted(**kwargs: object) -> object:
        nonlocal writes
        writes += 1
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store._client, "put_object", counted)

    first = await store.put(PNG)
    second = await store.put(PNG)

    assert first == second
    assert writes == 1
    assert await store.get(first) == PNG


async def test_a_digest_nobody_stored_is_none_not_an_error(
    clean_bucket: None, store: S3MediaStore
) -> None:
    """Kills on: letting `NoSuchKey` escape — a mistyped digest would reach
    the operator as a 500, which reads as a broken server rather than as a
    picture that is not there."""
    assert await store.get(digest_of(b"never uploaded")) is None
    assert await store.exists(digest_of(b"never uploaded")) is False


async def test_exists_is_true_for_what_was_stored(
    clean_bucket: None, store: S3MediaStore
) -> None:
    digest = await store.put(OTHER)
    assert await store.exists(digest) is True


async def test_an_unreachable_store_raises_rather_than_returning_none() -> None:
    """The distinction §10 depends on.

    Kills on: catching everything and returning `None` — an outage would
    hide behind a 404, the operator would go looking in the library for a
    picture that is there, and the health check would be the only place it
    could surface, except that it would report healthy too."""
    store = unreachable_store()
    with pytest.raises(MediaUnavailable):
        await store.get(digest_of(PNG))
    with pytest.raises(MediaUnavailable):
        await store.exists(digest_of(PNG))
    with pytest.raises(MediaUnavailable):
        await store.put(PNG)


async def test_health_is_true_against_the_live_store(
    s3_bucket: None, store: S3MediaStore
) -> None:
    assert await store.healthy() is True


async def test_health_is_false_against_a_closed_port() -> None:
    """§10. Kills on: raising out of `healthy` — a degraded node would
    answer with a 500 and a stack trace instead of the signal a load
    balancer reads."""
    assert await unreachable_store().healthy() is False


async def test_no_call_blocks_the_event_loop(
    clean_bucket: None, store: S3MediaStore
) -> None:
    """The constraint §6.1 states for the command loop, extended to the
    process: a synchronous S3 call anywhere in it stalls every WebSocket
    writer, which mid-show is the stage screen going blank.

    A sentinel task is scheduled and must have run by the time the upload
    returns. `to_thread` yields to the loop; a direct boto3 call does
    not."""
    ran = asyncio.Event()

    async def sentinel() -> None:
        ran.set()

    task = asyncio.create_task(sentinel())
    await store.put(PNG)

    assert ran.is_set(), "the upload never yielded: it ran on the event loop"
    await task


async def test_a_second_store_sees_what_the_first_wrote(
    clean_bucket: None, store: S3MediaStore
) -> None:
    """The store holds no state of its own — the bytes are in the bucket.

    Kills on: an in-process cache standing in for the store, which would
    pass every test above and hold nothing after a restart."""
    digest = await store.put(PNG)
    fresh = S3MediaStore(
        endpoint=S3_ENDPOINT,
        access_key=S3_ACCESS_KEY,
        secret_key=S3_SECRET_KEY,
        bucket=S3_BUCKET,
    )
    assert await fresh.get(digest) == PNG


async def test_a_bucket_that_does_not_exist_is_unavailable_not_absent(
    s3_bucket: None,
) -> None:
    """A bucket that is not there is a misconfigured deployment or an
    outage, never a picture nobody uploaded.

    Kills on: putting `NoSuchBucket` in the "absent" set — a node pointed
    at the wrong bucket would answer every fetch with a quiet 404, report
    itself unhealthy only through `head_bucket`, and send the operator
    looking for the pictures in the library."""
    misconfigured = S3MediaStore(
        endpoint=S3_ENDPOINT,
        access_key=S3_ACCESS_KEY,
        secret_key=S3_SECRET_KEY,
        bucket="a-bucket-that-was-never-created",
    )

    with pytest.raises(MediaUnavailable):
        await misconfigured.get(digest_of(PNG))
    with pytest.raises(MediaUnavailable):
        await misconfigured.exists(digest_of(PNG))
    assert await misconfigured.healthy() is False
