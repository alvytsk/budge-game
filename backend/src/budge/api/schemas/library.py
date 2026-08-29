"""The admin surface's bodies and responses (§8's «админка»).

Two bodies here name a category or an image by id in the URL, and none
names a caller: §7.4's rule holds exactly as it does everywhere else, and
plan 5's `test_no_inbound_model_names_an_actor` walks this module too.

`media_sha256` carries the one property that is true of a digest whether or
not anything has been stored under it (§7.6: «медиа контент-адресуемо по
sha256»). The pattern mirrors the check constraint on `images` so the API
refuses a bad digest with a 422 rather than letting the database refuse it
with a 500.
"""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class LibraryBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CreateCategoryBody(LibraryBody):
    title: str
    is_secret: bool = False


class EditCategoryBody(LibraryBody):
    title: str
    is_secret: bool


class SetActiveBody(LibraryBody):
    """§5.3's soft delete, and its undo. There is no DELETE route."""

    is_active: bool


class AddImageBody(LibraryBody):
    media_sha256: SHA256
    answer_text: str


class EditImageBody(LibraryBody):
    media_sha256: SHA256
    answer_text: str


class ReorderImagesBody(LibraryBody):
    """Every image of the category, in the order wanted.

    A partial list is refused rather than applied: `position` is what the
    admin screen renders by, and renumbering a subset would leave two
    images sharing a position.
    """

    image_ids: list[UUID]


class Response(BaseModel):
    model_config = ConfigDict(frozen=True)


class CategorySummaryBody(Response):
    id: UUID
    title: str
    is_secret: bool
    is_active: bool
    version: int
    # §8's «мягкое предупреждение» is read off this. Active images only —
    # an inactive one cannot be drawn, so counting it would report a
    # category as stocked when it is not.
    active_image_count: int


class ImageBody(Response):
    id: UUID
    media_sha256: str
    answer_text: str
    position: int
    is_active: bool


class CategoryDetailBody(Response):
    category: CategorySummaryBody
    images: tuple[ImageBody, ...]


class ThinCategoryBody(Response):
    id: UUID
    title: str
    active_image_count: int


class ReadinessBody(Response):
    """§8's soft warning, in one call the setup screen can render.

    It refuses nothing. §8 asks for «мягкое предупреждение, если у какой-то
    из них картинок меньше настраиваемого порога» — a warning, not a gate,
    and the operator remains the one who decides whether the show goes on.

    §B: the verdict counts both pools. Ordinary categories need to number
    `cells - players` — the remaining cells carry the players' secrets —
    and secret categories need to number exactly `players`, because
    `AssignSecret` requires distinct categories.
    """

    cells: int
    players: int
    threshold: int
    ordinary_available: int
    secrets_available: int
    thin: tuple[ThinCategoryBody, ...]
    ready: bool
