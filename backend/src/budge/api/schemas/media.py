"""What an upload answers with.

Deliberately not a URL. §7.6: «в сообщениях ездят идентификаторы, а не
URL» — the client is handed the address and builds its own request from it,
so the route can move without every stored response going stale.
"""

from pydantic import BaseModel, ConfigDict


class UploadedMediaBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    # The digest the server computed from the bytes it received (ruling 2).
    # The operator attaches it to a category in a second step, which is
    # where plan 6's `AddImageBody.media_sha256` takes it.
    media_sha256: str
    content_type: str
    bytes: int
