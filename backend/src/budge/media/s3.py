"""`MediaStore` over anything that speaks S3 (§10).

`boto3` behind `asyncio.to_thread` rather than `aioboto3` (ruling 1): the
property that matters is that the loop is never blocked, and `to_thread`
delivers it without `aiobotocore`'s exact pin on `botocore`. Media I/O is
low-frequency by nature — an operator loading pictures between shows, one
pack fetched per duel — so a thread per in-flight object is not a cost this
deployment can feel.

Every method is written so that "the store is unreachable" and "that object
is not there" stay different answers. §10 puts the first in the health
check; the second is an ordinary 404. Collapsing them would hide an outage
behind "that picture is missing", and send the operator looking for it in
the library.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from budge.media.digest import digest_of
from budge.services.ports import MediaUnavailable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mypy_boto3_s3.client import S3Client

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Codes that mean "there is no such object", as opposed to "the store did
# not answer". `HeadObject` reports the first as a bare 404.
#
# `NoSuchBucket` is deliberately absent from this set. A bucket that is not
# there is a misconfigured deployment or an outage, not a picture nobody
# uploaded — folding it in would make every fetch a quiet 404 on a node
# pointed at the wrong bucket, and the operator would go looking for the
# pictures in the library.
_ABSENT = frozenset({"NoSuchKey", "404"})

# One retry, briefly. A store that is down stays down for longer than a
# request should wait, and §10 gives the health check the job of noticing —
# a long retry here would turn one outage into a queue of stalled requests.
_CONFIG = Config(
    retries={"max_attempts": 2, "mode": "standard"},
    connect_timeout=3,
    read_timeout=10,
    signature_version="s3v4",
)


class S3MediaStore:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
    ) -> None:
        self._bucket = bucket
        # Built once. `boto3.client` is safe to share across the threads
        # `to_thread` uses for the operations here, and building one per
        # call would pay for a session setup on every picture.
        self._client: "S3Client" = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=_CONFIG,
        )

    def _blocking_exists(self, digest: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=digest)
        except ClientError as error:
            if _code_of(error) not in _ABSENT:
                raise
            # `HeadObject` has no response body, so a missing bucket and a
            # missing object both arrive as a bare 404 — unlike
            # `GetObject`, which names `NoSuchBucket`. Confirming the
            # bucket on the miss path is what keeps those two apart, and
            # it is the difference between telling the operator "no media
            # has been uploaded under that digest" and telling them the
            # store is unavailable. It costs one HEAD, and only ever on a
            # miss.
            if not self._blocking_healthy():
                raise
            return False
        return True

    def _blocking_get(self, digest: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=digest)
        except ClientError as error:
            if _code_of(error) in _ABSENT:
                return None
            raise
        body: bytes = response["Body"].read()
        return body

    def _blocking_put(self, data: bytes) -> str:
        digest = digest_of(data)
        # Ruling 3: content addressing makes this idempotent by
        # construction — the same bytes hash to the same key, so a rewrite
        # would be writing identical bytes over themselves.
        if self._blocking_exists(digest):
            return digest
        self._client.put_object(Bucket=self._bucket, Key=digest, Body=data)
        return digest

    def _blocking_healthy(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception:
            logger.warning("health: the object store is unreachable", exc_info=True)
            return False
        return True

    async def put(self, data: bytes) -> str:
        return await _off_the_loop(self._blocking_put, data)

    async def get(self, digest: str) -> bytes | None:
        return await _off_the_loop(self._blocking_get, digest)

    async def exists(self, digest: str) -> bool:
        return await _off_the_loop(self._blocking_exists, digest)

    async def healthy(self) -> bool:
        """Never raises. §10 wants a signal a load balancer can read, and a
        degraded node that answered with a stack trace would be reported as
        a 500 rather than as unhealthy."""
        return await asyncio.to_thread(self._blocking_healthy)


def _code_of(error: ClientError) -> str:
    code = error.response.get("Error", {}).get("Code", "")
    return str(code)


async def _off_the_loop(work: Callable[..., _T], *arguments: Any) -> _T:
    """Run one blocking S3 call on a worker thread, and translate whatever
    the client raises into this layer's own vocabulary.

    Anything that is not "no such object" — a refused connection, a
    timeout, a bad signature — is `MediaUnavailable`, which the routes map
    to 503 and the health check reports. Letting `botocore`'s own
    exceptions escape would put its class names in the API's error paths.
    """
    try:
        return await asyncio.to_thread(work, *arguments)
    except ClientError as error:
        raise MediaUnavailable(
            f"the object store refused the request: {_code_of(error)}"
        ) from error
    except Exception as error:
        raise MediaUnavailable(
            f"the object store could not be reached: {error!r}"
        ) from error
