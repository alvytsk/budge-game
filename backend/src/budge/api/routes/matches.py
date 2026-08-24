"""Assembling a match, listing matches, and the snapshot a console loads.

Ruling 5's line, exactly: `CreateMatch`, `AddPlayer`, `AssignSecret`,
`DealBoard`, `StartMatch` and `ResetMatch` are REST; everything from
`DeclareAttack` onwards is the socket's. Every route here funnels into the
same `CommandGateway` and the same `outcomes.py` mapping the socket uses.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from budge.api.outcomes import http_outcome
from budge.api.principal import require_host
from budge.api.projection import project_host
from budge.api.schemas.rest import (
    AddPlayerBody,
    AssignSecretBody,
    CreateMatchBody,
    MatchSummaryBody,
    PlayerSummaryBody,
    ResetMatchBody,
    SnapshotBody,
)
from budge.api.security import mint_stage_token
from budge.api.services import Services
from budge.api.settings import ApiSettings
from budge.db.errors import MatchNotFound
from budge.domain.actions import (
    AddPlayer,
    AssignSecret,
    Command,
    DealBoard,
    ResetMatch,
    StartMatch,
)
from budge.domain.board import BoardSize
from budge.domain.ids import CategoryId, MatchId, PlayerId
from budge.domain.settings import MatchSettings

router = APIRouter(prefix="/api/matches", tags=["matches"], dependencies=[Depends(require_host)])


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def _settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.settings
    return settings


async def _run(request: Request, match_id: MatchId, command: Command) -> JSONResponse:
    """One command, through the one gateway, answered by the one mapping."""
    outcome = await _services(request).gateway.submit(match_id, command)
    result = http_outcome(outcome)
    return JSONResponse(dict(result.body), status_code=result.status)


@router.post("")
async def create_match(body: CreateMatchBody, request: Request) -> JSONResponse:
    """Create a match and hand back its stage link.

    The token is minted here and nowhere else. Ruling 7 derives it rather
    than storing it, so there is no row to read it back from — this
    response is its entire lifecycle, and since it does not expire, once is
    enough.
    """
    services = _services(request)
    match_id, outcome = await services.lifecycle.create(
        BoardSize(width=body.board.width, height=body.board.height),
        MatchSettings(
            base_seconds=body.settings.base_seconds,
            bonus_cap_seconds=body.settings.bonus_cap_seconds,
            pass_penalty_seconds=body.settings.pass_penalty_seconds,
        ),
        body.player_count,
    )
    result = http_outcome(outcome)
    if result.status != 200:
        return JSONResponse(dict(result.body), status_code=result.status)
    return JSONResponse(
        {
            **result.body,
            "match_id": str(match_id),
            "stage_token": mint_stage_token(_settings(request).secret_key, match_id),
        },
        status_code=201,
    )


@router.post("/{match_id}/players")
async def add_player(match_id: UUID, body: AddPlayerBody, request: Request) -> JSONResponse:
    return await _run(
        request,
        MatchId(match_id),
        AddPlayer(player_id=PlayerId(body.player_id), name=body.name, colour=body.colour),
    )


@router.post("/{match_id}/secrets")
async def assign_secret(match_id: UUID, body: AssignSecretBody, request: Request) -> JSONResponse:
    return await _run(
        request,
        MatchId(match_id),
        AssignSecret(player_id=PlayerId(body.player_id), category=CategoryId(body.category)),
    )


@router.post("/{match_id}/deal")
async def deal_board(match_id: UUID, request: Request) -> JSONResponse:
    """§3.4: «Повторный `DealBoard` — это и есть кнопка "перераздать"», so
    this route is deliberately not idempotent and deliberately repeatable."""
    return await _run(request, MatchId(match_id), DealBoard())


@router.post("/{match_id}/start")
async def start_match(match_id: UUID, request: Request) -> JSONResponse:
    return await _run(request, MatchId(match_id), StartMatch())


@router.post("/{match_id}/reset")
async def reset_match(match_id: UUID, body: ResetMatchBody, request: Request) -> JSONResponse:
    """§A.8: an assembly command, so REST.

    It can be pressed from RUNNING, and that does not move the boundary:
    the socket owns commands *inside* a duel, from `DeclareAttack` onward,
    while this one returns the match to SETUP and belongs beside `deal` and
    `start`. The next frame reaches subscribers the ordinary way —
    `MatchRuntime._publish` broadcasts it to the hub like any other command.
    """
    return await _run(request, MatchId(match_id), ResetMatch(keep_roster=body.keep_roster))


@router.get("")
async def list_matches(request: Request) -> list[MatchSummaryBody]:
    """Ruling 14: §5.2's read model, read at last."""
    summaries = await _services(request).lifecycle.summaries()
    return [
        MatchSummaryBody(
            id=summary.id,
            status=summary.status,
            winner_id=UUID(summary.winner_id) if summary.winner_id is not None else None,
            last_seq=summary.last_seq,
            players=tuple(
                PlayerSummaryBody(name=name, colour=colour, eliminated=eliminated)
                for name, colour, eliminated in summary.players
            ),
        )
        for summary in summaries
    ]


@router.get("/{match_id}")
async def snapshot(match_id: UUID, request: Request) -> SnapshotBody:
    """§7.4's «снапшот для первичной загрузки», built by the same
    `project_host` the socket uses — a console that loads and a console
    that reconnects must not have two shapes to handle."""
    services = _services(request)
    try:
        state = await services.lifecycle.state_of(MatchId(match_id))
    except MatchNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no such match"
        ) from None
    frame = await project_host(
        state,
        now=services.clock.now(),
        events=(),
        directory=services.directory,
    )
    return SnapshotBody(
        frame=frame,
        stage_token=mint_stage_token(_settings(request).secret_key, MatchId(match_id)),
    )
