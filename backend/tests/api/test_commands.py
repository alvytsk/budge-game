"""What a client may say — and, more importantly, what it may not.

The load-bearing test here is `test_no_inbound_model_names_an_actor`. It is
the executable form of the global constraint §7.4 states: the client never
says who it is, so no inbound model anywhere may give it a place to.
"""

import inspect
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError, TypeAdapter

from podvinsya.api.schemas import commands as commands_module
from podvinsya.api.schemas.commands import (
    Ack,
    DeclareAttackCommand,
    Envelope,
    JudgeCorrectCommand,
    LiveCommand,
)
from podvinsya.domain.actions import DeclareAttack, ExpireTimer, JudgeCorrect
from podvinsya.domain.ids import GroupId

# Every way a body could claim an identity. §7.4: the principal comes from
# the authenticated session, never from a payload.
ACTOR_PROPERTIES = frozenset(
    {"actor", "sender", "principal", "role", "seat", "as_player", "on_behalf_of", "who"}
)

# The two documented exceptions, both REST and both in Task 9: they name
# the player being *administered* — added to the match, given a secret —
# not the player sending the request. The distinction is the whole point:
# an operator adds four players, and none of those four is the caller.
ADMINISTERED_PLAYER_MODELS = frozenset({"AddPlayerBody", "AssignSecretBody"})

INBOUND_MODULES = [commands_module]


def _models_in(module: Any) -> list[type[BaseModel]]:
    return [
        member
        for _name, member in inspect.getmembers(module, inspect.isclass)
        if issubclass(member, BaseModel) and member.__module__ == module.__name__
    ]


def _property_names(schema: dict[str, Any]) -> set[str]:
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                found.update(str(key) for key in properties)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return found


def test_no_inbound_model_names_an_actor() -> None:
    """§7.4, walked over the generated schema of every inbound model.

    Kills on: adding a "who am I" field to any inbound model — which is
    what makes «названный актор действительно участник» a check that can
    be passed by lying, since the only thing being checked would be a
    string the liar supplied."""
    offenders: dict[str, set[str]] = {}
    for module in INBOUND_MODULES:
        for model in _models_in(module):
            if model.__name__ in ADMINISTERED_PLAYER_MODELS:
                continue
            named = _property_names(model.model_json_schema()) & ACTOR_PROPERTIES
            if named:
                offenders[model.__name__] = named
    assert not offenders


def test_no_inbound_model_names_a_player_at_all() -> None:
    """The sharper version: `player_id` is not in `ACTOR_PROPERTIES`
    because Task 9's two administrative bodies legitimately carry one, but
    no *live* command may. Kills on: `JudgeCorrectCommand(player_id=...)`,
    which would let the console judge on somebody's behalf."""
    for model in _models_in(commands_module):
        assert not (_property_names(model.model_json_schema()) & {"player", "player_id"})


def test_the_actor_property_list_is_not_vacuous() -> None:
    """A typo in `ACTOR_PROPERTIES` would make the two tests above pass
    against anything. This asserts the walk finds a name when one is
    there."""

    class Forged(BaseModel):
        actor: str

    assert _property_names(Forged.model_json_schema()) & ACTOR_PROPERTIES


def test_expire_timer_is_not_a_client_command() -> None:
    """§4.3 and §6.2: the scheduler issues `ExpireTimer`, and honouring the
    `deadline_id` is the runtime's job.

    Kills on: adding it to the union, which would let a client resolve a
    duel by claiming a deadline fired — the domain would then check only
    that the clock had genuinely passed, which an attacker can wait for."""
    domain_commands = {
        model.to_domain().__class__ for model in _live_command_instances()
    }
    assert ExpireTimer not in domain_commands
    with pytest.raises(ValidationError):
        TypeAdapter(LiveCommand).validate_python({"type": "expire_timer", "deadline_id": 1})


def _live_command_instances() -> list[Any]:
    """One instance of every variant in the union, built from its defaults."""
    return [
        DeclareAttackCommand(attacking_group=uuid4(), defending_group=uuid4()),
        *(
            model()
            for model in _models_in(commands_module)
            if model.__name__.endswith("Command") and model is not DeclareAttackCommand
        ),
    ]


def test_every_variant_maps_onto_a_domain_command() -> None:
    """Kills on: a variant whose `to_domain` returns the wrong command —
    `JudgePassCommand` producing a `JudgeCorrect`, say, which would make
    the console's pass button award the answer."""
    assert JudgeCorrectCommand().to_domain() == JudgeCorrect()
    attacking, defending = GroupId(uuid4()), GroupId(uuid4())
    assert DeclareAttackCommand(
        attacking_group=attacking, defending_group=defending
    ).to_domain() == DeclareAttack(attacking_group=attacking, defending_group=defending)


def test_an_envelope_carries_a_correlation_id_that_is_not_an_operation_id() -> None:
    """§5.1: the `operation_id` is minted by `QueuedCommand.issue` and
    nowhere else. Kills on: naming this field `operation_id`, which is how
    a client's value would end up in the log's reconciliation key."""
    envelope = Envelope.model_validate(
        {"correlation_id": "console-17", "command": {"type": "start_duel"}}
    )
    assert envelope.correlation_id == "console-17"
    assert "operation_id" not in Envelope.model_fields


def test_an_envelope_without_a_correlation_id_is_accepted() -> None:
    assert Envelope.model_validate({"command": {"type": "pause_duel"}}).correlation_id is None


def test_an_unknown_command_type_is_refused() -> None:
    with pytest.raises(ValidationError):
        Envelope.model_validate({"command": {"type": "judge_wrong"}})


def test_an_extra_field_is_refused_rather_than_ignored() -> None:
    """Kills on: dropping `extra="forbid"`. A client sending an actor field
    would get a 200 and believe the server had honoured it — the silent
    shape §7.4 warns about."""
    with pytest.raises(ValidationError):
        Envelope.model_validate(
            {"command": {"type": "judge_correct", "player_id": "who-i-say-i-am"}}
        )


def test_an_ack_carries_no_state() -> None:
    """§7.2 puts the whole state in the frame that follows. An ack that
    also carried one would give the console two sources for one truth,
    arriving in whatever order the network chose."""
    assert not ({"state", "frame", "groups", "duel"} & set(Ack.model_fields))
