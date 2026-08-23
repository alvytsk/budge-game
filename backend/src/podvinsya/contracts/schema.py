"""The contract, as one JSON Schema document.

§7.6 makes the *schemas* the contract, so this is built from the models
rather than from FastAPI's OpenAPI document (ruling 4): the OpenAPI adds
paths, status codes and the framework's own error model, all of which
change shape on a FastAPI upgrade that changed no contract at all.

`ROOTS` is a literal, not a scan (ruling 3). The failure mode of a literal
is forgetting to add to it, and that failure has a test with a name:
`test_every_schema_model_is_reachable_from_a_root`.
"""

from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import JsonSchemaMode, models_json_schema

from podvinsya.api.schemas.commands import Ack, Envelope
from podvinsya.api.schemas.frames import HostFrame, StageFrame
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
from podvinsya.api.schemas.media import UploadedMediaBody
from podvinsya.api.schemas.rest import (
    AddPlayerBody,
    AssignSecretBody,
    CreatedMatchBody,
    CreateMatchBody,
    LoginBody,
    MatchSummaryBody,
    OutcomeBody,
    SnapshotBody,
)

# Ruling 5: a frame's schema answers "what will the server send", a body's
# answers "what may a client send". `models_json_schema` takes the mode per
# model, so the two questions are asked separately rather than collapsed.
ROOTS: tuple[tuple[type[BaseModel], JsonSchemaMode], ...] = (
    (StageFrame, "serialization"),
    (HostFrame, "serialization"),
    (Ack, "serialization"),
    (CreatedMatchBody, "serialization"),
    (MatchSummaryBody, "serialization"),
    (SnapshotBody, "serialization"),
    (OutcomeBody, "serialization"),
    (CategorySummaryBody, "serialization"),
    (CategoryDetailBody, "serialization"),
    (ImageBody, "serialization"),
    (ReadinessBody, "serialization"),
    (ThinCategoryBody, "serialization"),
    (UploadedMediaBody, "serialization"),
    (Envelope, "validation"),
    (LoginBody, "validation"),
    (CreateMatchBody, "validation"),
    (AddPlayerBody, "validation"),
    (AssignSecretBody, "validation"),
    (CreateCategoryBody, "validation"),
    (EditCategoryBody, "validation"),
    (SetActiveBody, "validation"),
    (AddImageBody, "validation"),
    (EditImageBody, "validation"),
    (ReorderImagesBody, "validation"),
)

REF_TEMPLATE = "#/$defs/{model}"


def contract_schema() -> dict[str, Any]:
    """One document, one `$defs`, every root resolved into it.

    `models_json_schema` is what makes this one document rather than twelve:
    asking each model separately would produce twelve copies of `CellFrame`,
    and the emitted TypeScript would carry twelve names for one type.
    """
    _mapping, definitions = models_json_schema(list(ROOTS), ref_template=REF_TEMPLATE)
    return dict(definitions)
