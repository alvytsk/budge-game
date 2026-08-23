"""§8's «админка»: the library an operator keeps between shows.

Every write goes through `LibraryCatalogue` and never through a model —
§5.3 makes that structural, and `test_no_write_outside_the_catalogue`
parses this module along with every other to keep it true.

No route deletes. §5.3: «контент удаляется только мягко, флагом
`is_active`», which is also why `PUT …/active` exists in both directions:
the operator who switched a theme off after three editions expects to
switch it back on.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from podvinsya.api.principal import require_host
from podvinsya.api.schemas.library import (
    AddImageBody,
    CategoryDetailBody,
    CategorySummaryBody,
    CreateCategoryBody,
    EditCategoryBody,
    EditImageBody,
    ImageBody,
    ReadinessBody,
    ReorderImagesBody,
    SetActiveBody,
    ThinCategoryBody,
)
from podvinsya.api.settings import ApiSettings
from podvinsya.library.catalogue import (
    CategoryRow,
    ImageRow,
    LibraryCatalogue,
    UnknownCategory,
    UnknownImage,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/library", tags=["library"], dependencies=[Depends(require_host)])


def _catalogue(request: Request) -> LibraryCatalogue:
    catalogue: LibraryCatalogue = request.app.state.catalogue
    return catalogue


def _settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.settings
    return settings


def _summary(row: CategoryRow) -> CategorySummaryBody:
    return CategorySummaryBody(
        id=row.id,
        title=row.title,
        is_secret=row.is_secret,
        is_active=row.is_active,
        version=row.version,
        active_image_count=row.active_image_count,
    )


def _image(row: ImageRow) -> ImageBody:
    return ImageBody(
        id=row.id,
        media_sha256=row.media_sha256,
        answer_text=row.answer_text,
        position=row.position,
        is_active=row.is_active,
    )


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=what)


@router.get("/categories")
async def list_categories(request: Request) -> list[CategorySummaryBody]:
    """Every category, active or not.

    An operator who switched a theme off has to be able to switch it back
    on, which means seeing it here.
    """
    return [_summary(row) for row in await _catalogue(request).list_categories()]


@router.post("/categories", status_code=status.HTTP_201_CREATED)
async def create_category(body: CreateCategoryBody, request: Request) -> CategorySummaryBody:
    return _summary(
        await _catalogue(request).create_category(body.title, is_secret=body.is_secret)
    )


@router.get("/categories/{category_id}")
async def category_detail(category_id: UUID, request: Request) -> CategoryDetailBody:
    try:
        detail = await _catalogue(request).category_detail(category_id)
    except UnknownCategory:
        raise _not_found("no such category") from None
    return CategoryDetailBody(
        category=_summary(detail.category), images=tuple(_image(row) for row in detail.images)
    )


@router.put("/categories/{category_id}")
async def edit_category(
    category_id: UUID, body: EditCategoryBody, request: Request
) -> CategoryDetailBody:
    """A semantic edit (§5.3): the catalogue bumps `version`."""
    try:
        await _catalogue(request).edit_category(
            category_id, title=body.title, is_secret=body.is_secret
        )
    except UnknownCategory:
        raise _not_found("no such category") from None
    return await category_detail(category_id, request)


@router.put("/categories/{category_id}/active")
async def set_category_active(
    category_id: UUID, body: SetActiveBody, request: Request
) -> CategoryDetailBody:
    """§5.3's soft delete, and its undo. Deliberately not a semantic edit —
    see `LibraryCatalogue.set_category_active`."""
    try:
        await _catalogue(request).set_category_active(category_id, is_active=body.is_active)
    except UnknownCategory:
        raise _not_found("no such category") from None
    return await category_detail(category_id, request)


@router.post("/categories/{category_id}/images", status_code=status.HTTP_201_CREATED)
async def add_image(category_id: UUID, body: AddImageBody, request: Request) -> ImageBody:
    try:
        row = await _catalogue(request).add_image(
            category_id, media_sha256=body.media_sha256, answer_text=body.answer_text
        )
    except UnknownCategory:
        raise _not_found("no such category") from None
    return _image(row)


@router.put("/images/{image_id}")
async def edit_image(image_id: UUID, body: EditImageBody, request: Request) -> ImageBody:
    try:
        await _catalogue(request).edit_image(
            image_id, media_sha256=body.media_sha256, answer_text=body.answer_text
        )
    except UnknownImage:
        raise _not_found("no such image") from None
    return await _reread_image(request, image_id)


@router.put("/images/{image_id}/active")
async def set_image_active(
    image_id: UUID, body: SetActiveBody, request: Request
) -> ImageBody:
    """Soft delete for one picture — and, unlike a category's flag, a
    semantic edit: it changes what can be drawn, which is exactly what
    §5.3's lock protects."""
    try:
        await _catalogue(request).set_image_active(image_id, is_active=body.is_active)
    except UnknownImage:
        raise _not_found("no such image") from None
    return await _reread_image(request, image_id)


@router.put("/categories/{category_id}/images/order")
async def reorder_images(
    category_id: UUID, body: ReorderImagesBody, request: Request
) -> CategoryDetailBody:
    try:
        await _catalogue(request).reorder_images(category_id, body.image_ids)
    except UnknownCategory:
        raise _not_found("no such category") from None
    except UnknownImage as mismatch:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(mismatch)
        ) from None
    return await category_detail(category_id, request)


@router.get("/readiness")
async def readiness(request: Request, cells: int = 0) -> ReadinessBody:
    """§8's soft warning, answered in one call.

    «Отбор на партию: из активных, без повторов, число равно числу клеток
    поля» — so a board of `cells` cells needs `cells - players` ordinary
    categories plus one secret each. The player count is not known here, so
    `ready` is answered against the strictest reading: enough ordinary
    categories for every cell. It refuses nothing either way (§8 asks for
    «мягкое предупреждение»), and an operator who wants the show to go on
    can start it.
    """
    settings = _settings(request)
    rows = await _catalogue(request).list_categories()
    active = [row for row in rows if row.is_active]
    ordinary = [row for row in active if not row.is_secret]
    secrets = [row for row in active if row.is_secret]
    thin = [
        ThinCategoryBody(
            id=row.id, title=row.title, active_image_count=row.active_image_count
        )
        for row in active
        if row.active_image_count < settings.thin_image_threshold
    ]
    return ReadinessBody(
        cells=cells,
        threshold=settings.thin_image_threshold,
        ordinary_available=len(ordinary),
        secrets_available=len(secrets),
        thin=tuple(thin),
        ready=len(ordinary) >= cells,
    )


async def _reread_image(request: Request, image_id: UUID) -> ImageBody:
    """One image, read back through the catalogue after a write.

    Read back rather than reconstructed from the request body: `position`
    and `is_active` are not in any edit body, and a response that guessed
    them would make the admin screen trust a value the server invented.
    """
    try:
        return _image(await _catalogue(request).image(image_id))
    except UnknownImage:
        raise _not_found("no such image") from None
