# «Подвинься» — доменное ядро. План реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** собрать чистое доменное ядро игры «Подвинься» — состояние, события, `decide`/`evolve` — с полным набором правил под тестами и без единого обращения к I/O.

**Architecture:** функциональное ядро в императивной оболочке. `decide(state, command, ctx) -> tuple[Event, ...]` порождает события либо бросает `Rejected`; `evolve(state, event) -> MatchState` их применяет. Всё состояние иммутабельно. Недетерминизм — время, раздача, порядок картинок — инжектится **значениями** через `DecisionContext`, поэтому домен не видит ни часов, ни генератора случайных чисел, а каждый тест детерминирован.

**Tech Stack:** Python 3.12, `dataclasses` (frozen + slots), pytest, Hypothesis, ruff, mypy в строгом режиме. Ни одной зависимости от БД, сети или файловой системы.

**Spec:** `docs/superpowers/specs/2026-08-22-podvinsya-design.md`

## Global Constraints

- Python **3.12** или новее.
- Пакет называется `podvinsya`, исходники живут в `backend/src/podvinsya/`, тесты в `backend/tests/`.
- Этот план трогает **только** `backend/src/podvinsya/domain/` и `backend/tests/domain/`. Ни одного импорта из `sqlalchemy`, `fastapi`, `asyncio`, `random`, `datetime.now`, `time`. Нарушение ловится тестом в задаче 15.
- Все доменные типы — `@dataclass(frozen=True, slots=True)`. Коллекции в состоянии — `tuple` или `frozenset`, никогда `list` или `dict`.
- `decide` **чистая**: одинаковые `(state, command, ctx)` дают одинаковый результат.
- `decide` бросает `Rejected(reason)` на нелегальную команду и возвращает `()` на команду, которая легальна, но ничего не меняет.
- Умолчания настроек, скопированные из спеки §12: `base_seconds = 60`, `bonus_cap_seconds = 15`, `pass_penalty_seconds = 3`.
- Ограничения поля из спеки §2.1: `W ≥ 3`, `H ≥ 3`, `W·H ≤ 36`, `W·H` делится на число игроков нацело.
- Все имена в коде английские, все сообщения и комментарии — по-английски. Русский остаётся в документации и в пользовательском интерфейсе, который в этом плане не затрагивается.
- mypy запускается со `--strict` и должен проходить чисто после каждой задачи.
- `ruff check` должен проходить чисто после каждой задачи, и правило `E501` включено — предел строки в 100 символов проверяется, а не только декларируется.

---

## Структура файлов

```
backend/
  pyproject.toml                      конфигурация пакета, pytest, ruff, mypy
  src/podvinsya/domain/
    __init__.py                       публичный фасад домена
    ids.py                            NewType-идентификаторы
    board.py                          Cell, BoardSize, геометрия, смежность, связность
    settings.py                       MatchSettings
    budgets.py                        Budgets — остатки таймеров как значение
    state.py                          Player, Group, Duel, MatchState
    events.py                         события
    actions.py                        команды
    context.py                        DecisionContext и планы недетерминизма
    errors.py                         RejectionReason, Rejected
    rules.py                          чистые предикаты и расчёты по правилам
    timing.py                         работа с якорем, дедлайном, списанием
    genesis.py                        create_initial_state
    evolve.py                         evolve
    decide.py                         decide
  tests/domain/
    conftest.py                       построители состояний поверх decide/evolve
    test_board.py                     геометрия и валидация поля
    test_state.py                     настройки, бюджеты, генезис
    test_dispatch.py                  чистота decide, свёртка, инкремент seq
    test_setup.py                     игроки, секреты
    test_deal.py                      раздача и перераздача
    test_start.py                     старт партии и порядок хода
    test_rules.py                     бонус времени, легальные цели
    test_declare.py                   объявление атаки
    test_duel_start.py                старт дуэли, якорь, дедлайн
    test_judge_correct.py             «верно»
    test_judge_pass.py                «пас», в том числе в ноль
    test_pause.py                     пауза и снятие
    test_resolution.py                исход дуэли и слияние групп
    test_elimination.py               выбывание и победа
    test_undo.py                      отмена судейского решения
    test_invariants.py                свойственные тесты и завершаемость
    test_purity.py                    запрет на I/O и недетерминизм
```

Разбиение сделано по ответственности, а не по слоям: `rules.py` держит предикаты правил, `timing.py` — всё, что связано с якорем и списанием, `decide.py` и `evolve.py` остаются диспетчерами, которые эти две вещи склеивают. Файлы, меняющиеся вместе, лежат вместе.

---

### Task 1: Скелет пакета, идентификаторы, геометрия поля

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/src/podvinsya/__init__.py`
- Create: `backend/src/podvinsya/domain/__init__.py`
- Create: `backend/src/podvinsya/domain/ids.py`
- Create: `backend/src/podvinsya/domain/board.py`
- Create: `backend/src/podvinsya/domain/errors.py`
- Test: `backend/tests/domain/test_board.py`

**Interfaces:**
- Consumes: ничего, это первая задача.
- Produces: `MatchId`, `PlayerId`, `GroupId`, `CategoryId`, `ImageId` (все `NewType` над `UUID`); `Cell(col: int, row: int)` как `NamedTuple`; `BoardSize(width: int, height: int)` с методами `cells() -> tuple[Cell, ...]` и `contains(cell: Cell) -> bool`; `orthogonal_neighbours(cell: Cell, board: BoardSize) -> tuple[Cell, ...]`; `groups_are_adjacent(a: frozenset[Cell], b: frozenset[Cell], board: BoardSize) -> bool`; `is_connected(cells: frozenset[Cell]) -> bool`; `validate_board(board: BoardSize, player_count: int) -> None`; `RejectionReason` как `StrEnum`; `Rejected(Exception)` с полем `reason: RejectionReason`.

- [ ] **Step 1: Создать конфигурацию пакета**

`backend/pyproject.toml`:

```toml
[project]
name = "podvinsya"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0", "hypothesis>=6.100", "ruff>=0.5", "mypy>=1.10"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/podvinsya"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.mypy]
strict = true
mypy_path = "src"
packages = ["podvinsya"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "E501"]
```

`E501` is selected deliberately. Ruff's default rule set omits it, so `line-length` alone configures only the formatter and `ruff check` never enforces it — a declared constraint that nothing checks. Adding it to the default four makes the stated limit real without pulling in isort, pyupgrade or bugbear, whose findings are a separate decision.

Создать пустые `backend/src/podvinsya/__init__.py` и `backend/src/podvinsya/domain/__init__.py`.

- [ ] **Step 2: Написать падающий тест геометрии**

`backend/tests/domain/test_board.py`:

```python
import pytest

from podvinsya.domain.board import (
    BoardSize,
    Cell,
    groups_are_adjacent,
    is_connected,
    orthogonal_neighbours,
    validate_board,
)
from podvinsya.domain.errors import Rejected, RejectionReason


def test_board_enumerates_every_cell() -> None:
    board = BoardSize(width=4, height=6)
    cells = board.cells()
    assert len(cells) == 24
    assert len(set(cells)) == 24
    assert Cell(0, 0) in cells
    assert Cell(3, 5) in cells
    assert not board.contains(Cell(4, 0))
    assert not board.contains(Cell(-1, 0))


def test_corner_cell_has_two_neighbours() -> None:
    board = BoardSize(width=4, height=6)
    assert set(orthogonal_neighbours(Cell(0, 0), board)) == {Cell(1, 0), Cell(0, 1)}


def test_interior_cell_has_four_neighbours_and_no_diagonals() -> None:
    board = BoardSize(width=4, height=6)
    neighbours = set(orthogonal_neighbours(Cell(1, 1), board))
    assert neighbours == {Cell(0, 1), Cell(2, 1), Cell(1, 0), Cell(1, 2)}
    assert Cell(0, 0) not in neighbours


def test_groups_touching_orthogonally_are_adjacent() -> None:
    board = BoardSize(width=4, height=6)
    assert groups_are_adjacent(frozenset({Cell(0, 0)}), frozenset({Cell(1, 0)}), board)


def test_groups_touching_only_diagonally_are_not_adjacent() -> None:
    board = BoardSize(width=4, height=6)
    assert not groups_are_adjacent(frozenset({Cell(0, 0)}), frozenset({Cell(1, 1)}), board)


def test_connectivity() -> None:
    assert is_connected(frozenset({Cell(0, 0), Cell(1, 0), Cell(1, 1)}))
    assert not is_connected(frozenset({Cell(0, 0), Cell(2, 0)}))
    assert is_connected(frozenset({Cell(0, 0)}))
    assert is_connected(frozenset())


@pytest.mark.parametrize(
    ("width", "height", "players", "reason"),
    [
        (2, 6, 2, RejectionReason.BOARD_INVALID),
        (6, 2, 2, RejectionReason.BOARD_INVALID),
        (6, 7, 2, RejectionReason.BOARD_INVALID),
        (3, 4, 5, RejectionReason.PLAYER_COUNT_INVALID),
        (3, 4, 0, RejectionReason.PLAYER_COUNT_INVALID),
        (3, 5, 2, RejectionReason.BOARD_NOT_DIVISIBLE),
    ],
)
def test_invalid_boards_are_rejected(
    width: int, height: int, players: int, reason: RejectionReason
) -> None:
    with pytest.raises(Rejected) as excinfo:
        validate_board(BoardSize(width=width, height=height), players)
    assert excinfo.value.reason is reason


@pytest.mark.parametrize(
    ("width", "height", "players"), [(3, 4, 2), (3, 6, 3), (4, 6, 4), (6, 6, 4)]
)
def test_default_boards_are_valid(width: int, height: int, players: int) -> None:
    validate_board(BoardSize(width=width, height=height), players)
```

- [ ] **Step 3: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_board.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'podvinsya.domain.board'`

- [ ] **Step 4: Написать реализацию**

`backend/src/podvinsya/domain/ids.py`:

```python
from typing import NewType
from uuid import UUID

MatchId = NewType("MatchId", UUID)
PlayerId = NewType("PlayerId", UUID)
GroupId = NewType("GroupId", UUID)
CategoryId = NewType("CategoryId", UUID)
ImageId = NewType("ImageId", UUID)
```

`backend/src/podvinsya/domain/errors.py`:

```python
from enum import StrEnum


class RejectionReason(StrEnum):
    BOARD_INVALID = "board_invalid"
    BOARD_NOT_DIVISIBLE = "board_not_divisible"
    WRONG_STATUS = "wrong_status"
    PLAYER_COUNT_INVALID = "player_count_invalid"
    DUPLICATE_PLAYER = "duplicate_player"
    UNKNOWN_PLAYER = "unknown_player"
    SECRET_MISSING = "secret_missing"
    DUPLICATE_CATEGORY = "duplicate_category"
    DEAL_INVALID = "deal_invalid"
    NOT_YOUR_TURN = "not_your_turn"
    UNKNOWN_GROUP = "unknown_group"
    NOT_YOUR_GROUP = "not_your_group"
    TARGET_IS_YOURS = "target_is_yours"
    NOT_ADJACENT = "not_adjacent"
    DUEL_IN_PROGRESS = "duel_in_progress"
    NO_DUEL = "no_duel"
    DUEL_NOT_DECLARED = "duel_not_declared"
    DUEL_NOT_RUNNING = "duel_not_running"
    DUEL_PAUSED = "duel_paused"
    DUEL_NOT_PAUSED = "duel_not_paused"
    NOTHING_TO_UNDO = "nothing_to_undo"
    IMAGES_EXHAUSTED = "images_exhausted"


class Rejected(Exception):
    """A legal-but-refused command. Never a fault: state stays untouched."""

    def __init__(self, reason: RejectionReason) -> None:
        super().__init__(reason.value)
        self.reason = reason
```

`backend/src/podvinsya/domain/board.py`:

```python
from collections import deque
from dataclasses import dataclass
from typing import NamedTuple

from podvinsya.domain.errors import Rejected, RejectionReason

MAX_CELLS = 36
MIN_SIDE = 3


class Cell(NamedTuple):
    col: int
    row: int


@dataclass(frozen=True, slots=True)
class BoardSize:
    width: int
    height: int

    @property
    def cell_count(self) -> int:
        return self.width * self.height

    def cells(self) -> tuple[Cell, ...]:
        return tuple(
            Cell(col, row) for row in range(self.height) for col in range(self.width)
        )

    def contains(self, cell: Cell) -> bool:
        return 0 <= cell.col < self.width and 0 <= cell.row < self.height


_OFFSETS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def orthogonal_neighbours(cell: Cell, board: BoardSize) -> tuple[Cell, ...]:
    candidates = (Cell(cell.col + dc, cell.row + dr) for dc, dr in _OFFSETS)
    return tuple(c for c in candidates if board.contains(c))


def groups_are_adjacent(
    left: frozenset[Cell], right: frozenset[Cell], board: BoardSize
) -> bool:
    return any(
        neighbour in right for cell in left for neighbour in orthogonal_neighbours(cell, board)
    )


def is_connected(cells: frozenset[Cell]) -> bool:
    if len(cells) <= 1:
        return True
    start = next(iter(cells))
    seen = {start}
    queue = deque([start])
    while queue:
        cell = queue.popleft()
        for dc, dr in _OFFSETS:
            neighbour = Cell(cell.col + dc, cell.row + dr)
            if neighbour in cells and neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return len(seen) == len(cells)


def validate_board(board: BoardSize, player_count: int) -> None:
    if board.width < MIN_SIDE or board.height < MIN_SIDE:
        raise Rejected(RejectionReason.BOARD_INVALID)
    if board.cell_count > MAX_CELLS:
        raise Rejected(RejectionReason.BOARD_INVALID)
    if player_count < 2 or player_count > 4:
        raise Rejected(RejectionReason.PLAYER_COUNT_INVALID)
    if board.cell_count % player_count != 0:
        raise Rejected(RejectionReason.BOARD_NOT_DIVISIBLE)
```

Порядок проверок в `validate_board` существенен и должен остаться таким: сначала геометрия, затем допустимость числа игроков, и только потом делимость. Пять игроков недопустимы независимо от поля, и сообщать про них «поле не делится» — вводить ведущего в заблуждение. Этот же порядок делает деление на ноль недостижимым: `player_count = 0` отсеивается проверкой диапазона до того, как дело дойдёт до `%`. Переставлять проверки, чтобы делимость шла первой, нельзя — это потребует отдельного стража от деления на ноль, существующего только ради перестановки.

Обрати внимание: `is_connected` намеренно не берёт `BoardSize`. Связность определяется только соседством самих клеток, и группа не может выйти за поле, потому что её клетки в него уже входят.

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `cd backend && python -m pytest tests/domain/test_board.py -v && python -m mypy`
Expected: PASS, mypy чисто.

- [ ] **Step 6: Коммит**

```bash
git add backend/pyproject.toml backend/src/podvinsya backend/tests/domain/test_board.py
git commit -m "feat(domain): board geometry, ids, rejection reasons"
```

---

### Task 2: Типы состояния, настройки, бюджеты, генезис

**Files:**
- Create: `backend/src/podvinsya/domain/settings.py`
- Create: `backend/src/podvinsya/domain/budgets.py`
- Create: `backend/src/podvinsya/domain/state.py`
- Create: `backend/src/podvinsya/domain/genesis.py`
- Test: `backend/tests/domain/test_state.py`

**Interfaces:**
- Consumes: `ids`, `board` из задачи 1.
- Produces: `MatchSettings(base_seconds=60, bonus_cap_seconds=15, pass_penalty_seconds=3)`; `Budgets` с `get(player) -> int`, `with_value(player, ms) -> Budgets`, `charge(player, ms) -> Budgets`, `players() -> tuple[PlayerId, ...]`, конструктор `Budgets.of(mapping)`; `Player`, `Group`, `Duel`, `DuelPhase`, `MatchStatus`, `MatchState`; `create_initial_state(match_id, board, settings) -> MatchState`.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/domain/test_state.py`:

```python
from uuid import uuid4

from podvinsya.domain.board import BoardSize
from podvinsya.domain.budgets import Budgets
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import MatchId, PlayerId
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchStatus


def test_default_settings_match_the_spec() -> None:
    settings = MatchSettings()
    assert settings.base_seconds == 60
    assert settings.bonus_cap_seconds == 15
    assert settings.pass_penalty_seconds == 3


def test_budgets_are_immutable_values() -> None:
    alice, bob = PlayerId(uuid4()), PlayerId(uuid4())
    budgets = Budgets.of({alice: 60_000, bob: 60_000})
    charged = budgets.charge(alice, 4_200)
    assert budgets.get(alice) == 60_000, "original must not be mutated"
    assert charged.get(alice) == 55_800
    assert charged.get(bob) == 60_000
    assert set(charged.players()) == {alice, bob}


def test_charging_below_zero_clamps_to_zero() -> None:
    alice = PlayerId(uuid4())
    budgets = Budgets.of({alice: 2_000})
    assert budgets.charge(alice, 5_000).get(alice) == 0


def test_genesis_produces_an_empty_setup_match() -> None:
    match_id = MatchId(uuid4())
    board = BoardSize(width=4, height=6)
    state = create_initial_state(match_id, board, MatchSettings())

    assert state.id == match_id
    assert state.seq == 0
    assert state.status is MatchStatus.SETUP
    assert state.board == board
    assert state.players == ()
    assert state.groups == {}
    assert state.duel is None
    assert state.winner is None
    assert state.played_categories == frozenset()
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'podvinsya.domain.settings'`

- [ ] **Step 3: Написать реализацию**

`backend/src/podvinsya/domain/settings.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MatchSettings:
    base_seconds: int = 60
    bonus_cap_seconds: int = 15
    pass_penalty_seconds: int = 3

    @property
    def base_ms(self) -> int:
        return self.base_seconds * 1000

    @property
    def bonus_cap_ms(self) -> int:
        return self.bonus_cap_seconds * 1000

    @property
    def pass_penalty_ms(self) -> int:
        return self.pass_penalty_seconds * 1000
```

`backend/src/podvinsya/domain/budgets.py`:

```python
from collections.abc import Mapping
from dataclasses import dataclass

from podvinsya.domain.ids import PlayerId


@dataclass(frozen=True, slots=True)
class Budgets:
    """Remaining duel time per player, as an immutable value.

    Stored as ordered pairs rather than a mapping so the whole value stays
    hashable and serialises to the event log without ambiguity.
    """

    entries: tuple[tuple[PlayerId, int], ...]

    @classmethod
    def of(cls, values: Mapping[PlayerId, int]) -> "Budgets":
        return cls(tuple(values.items()))

    def get(self, player: PlayerId) -> int:
        for owner, remaining in self.entries:
            if owner == player:
                return remaining
        raise KeyError(player)

    def players(self) -> tuple[PlayerId, ...]:
        return tuple(owner for owner, _ in self.entries)

    def with_value(self, player: PlayerId, remaining_ms: int) -> "Budgets":
        clamped = max(0, remaining_ms)
        return Budgets(
            tuple(
                (owner, clamped if owner == player else value)
                for owner, value in self.entries
            )
        )

    def charge(self, player: PlayerId, amount_ms: int) -> "Budgets":
        return self.with_value(player, self.get(player) - amount_ms)
```

`backend/src/podvinsya/domain/state.py`:

```python
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from podvinsya.domain.board import BoardSize, Cell
from podvinsya.domain.budgets import Budgets
from podvinsya.domain.ids import CategoryId, GroupId, ImageId, MatchId, PlayerId
from podvinsya.domain.settings import MatchSettings


class MatchStatus(StrEnum):
    SETUP = "setup"
    RUNNING = "running"
    FINISHED = "finished"


class DuelPhase(StrEnum):
    DECLARED = "declared"
    RUNNING = "running"


@dataclass(frozen=True, slots=True)
class Player:
    id: PlayerId
    name: str
    colour: str
    eliminated: bool = False


@dataclass(frozen=True, slots=True)
class Group:
    id: GroupId
    owner: PlayerId
    category: CategoryId
    cells: frozenset[Cell]
    revealed: bool


@dataclass(frozen=True, slots=True)
class Duel:
    attacker: PlayerId
    defender: PlayerId
    attacking_group: GroupId
    defending_group: GroupId
    category: CategoryId
    image_order: tuple[ImageId, ...]
    index: int
    answering: PlayerId
    budgets: Budgets
    anchor: datetime | None
    phase: DuelPhase

    @property
    def paused(self) -> bool:
        return self.phase is DuelPhase.RUNNING and self.anchor is None

    def opponent_of(self, player: PlayerId) -> PlayerId:
        return self.defender if player == self.attacker else self.attacker


@dataclass(frozen=True, slots=True)
class MatchState:
    id: MatchId
    seq: int
    status: MatchStatus
    board: BoardSize
    settings: MatchSettings
    player_count: int = 0
    players: tuple[Player, ...] = ()
    secrets: Mapping[PlayerId, CategoryId] = field(default_factory=dict)
    turn_order: tuple[PlayerId, ...] = ()
    turn_index: int = 0
    round_no: int = 0
    groups: Mapping[GroupId, Group] = field(default_factory=dict)
    played_categories: frozenset[CategoryId] = frozenset()
    duel: Duel | None = None
    winner: PlayerId | None = None

    def player(self, player_id: PlayerId) -> Player:
        for candidate in self.players:
            if candidate.id == player_id:
                return candidate
        raise KeyError(player_id)

    def current_player(self) -> PlayerId:
        return self.turn_order[self.turn_index]

    def groups_of(self, player: PlayerId) -> tuple[Group, ...]:
        return tuple(g for g in self.groups.values() if g.owner == player)

    def active_players(self) -> tuple[Player, ...]:
        return tuple(p for p in self.players if not p.eliminated)
```

`backend/src/podvinsya/domain/genesis.py`:

```python
from podvinsya.domain.board import BoardSize
from podvinsya.domain.ids import MatchId
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchState, MatchStatus


def create_initial_state(
    match_id: MatchId, board: BoardSize, settings: MatchSettings
) -> MatchState:
    """The genesis constructor. Recovery is fold(create_initial_state(...), events).

    This lives in production code on purpose: a genesis constructor that exists
    only as a test fixture means recovery is not actually "fold the log".
    """
    return MatchState(
        id=match_id,
        seq=0,
        status=MatchStatus.SETUP,
        board=board,
        settings=settings,
    )
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd backend && python -m pytest tests/domain/test_state.py -v && python -m mypy`
Expected: PASS, mypy чисто.

- [ ] **Step 5: Коммит**

```bash
git add backend/src/podvinsya/domain backend/tests/domain/test_state.py
git commit -m "feat(domain): state types, budgets value, genesis constructor"
```

---

### Task 3: Команды, события, контекст, диспетчеры

**Files:**
- Create: `backend/src/podvinsya/domain/actions.py`
- Create: `backend/src/podvinsya/domain/events.py`
- Create: `backend/src/podvinsya/domain/context.py`
- Create: `backend/src/podvinsya/domain/decide.py`
- Create: `backend/src/podvinsya/domain/evolve.py`
- Test: `backend/tests/domain/test_dispatch.py`

**Interfaces:**
- Consumes: всё из задач 1–2.
- Produces: типы-объединения `Command` и `Event`; `DecisionContext(now: datetime, deal: DealPlan | None = None, image_order: tuple[ImageId, ...] | None = None)`; `DealPlan(cells: tuple[DealtCell, ...])`; `DealtCell(cell, owner, category, group_id, revealed)`; `decide(state: MatchState, command: Command, ctx: DecisionContext) -> tuple[Event, ...]`; `evolve(state: MatchState, event: Event) -> MatchState`; `fold(state, events) -> MatchState`.

Эта задача создаёт полный набор типов и диспетчеры, которые пока умеют ровно одно событие — `MatchCreated`. Остальные ветки добавляются задачами 4–14. Это сделано намеренно: тип-объединения должны существовать целиком с самого начала, иначе каждая следующая задача правит один и тот же файл ради добавления одного варианта, и mypy не может проверить исчерпывающность.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/domain/test_dispatch.py`:

```python
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from podvinsya.domain.actions import CreateMatch
from podvinsya.domain.board import BoardSize
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.events import MatchCreated
from podvinsya.domain.evolve import evolve, fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import MatchId
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchStatus

NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)


def test_create_match_emits_match_created() -> None:
    match_id = MatchId(uuid4())
    board = BoardSize(width=4, height=6)
    state = create_initial_state(match_id, board, MatchSettings())

    command = CreateMatch(board=board, settings=MatchSettings(), player_count=4)
    events = decide(state, command, DecisionContext(now=NOW))

    assert events == (MatchCreated(board=board, settings=MatchSettings(), player_count=4),)


def test_evolve_increments_seq_for_every_event() -> None:
    state = create_initial_state(MatchId(uuid4()), BoardSize(4, 6), MatchSettings())
    created = MatchCreated(board=BoardSize(4, 6), settings=MatchSettings(), player_count=4)
    evolved = evolve(state, created)
    assert evolved.seq == 1
    assert evolved.status is MatchStatus.SETUP
    assert evolved.player_count == 4


def test_fold_applies_events_in_order() -> None:
    state = create_initial_state(MatchId(uuid4()), BoardSize(4, 6), MatchSettings())
    created = MatchCreated(board=BoardSize(4, 6), settings=MatchSettings(), player_count=4)
    folded = fold(state, [created])
    assert folded.seq == 1


def test_decide_is_pure() -> None:
    state = create_initial_state(MatchId(uuid4()), BoardSize(4, 6), MatchSettings())
    command = CreateMatch(board=BoardSize(4, 6), settings=MatchSettings(), player_count=4)
    ctx = DecisionContext(now=NOW)
    assert decide(state, command, ctx) == decide(state, command, ctx)


def test_unknown_event_is_a_type_error_not_a_silent_noop() -> None:
    state = create_initial_state(MatchId(uuid4()), BoardSize(4, 6), MatchSettings())
    with pytest.raises(NotImplementedError):
        evolve(state, object())  # type: ignore[arg-type]
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_dispatch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'podvinsya.domain.actions'`

- [ ] **Step 3: Написать команды и события**

`backend/src/podvinsya/domain/actions.py`:

```python
from dataclasses import dataclass

from podvinsya.domain.board import BoardSize
from podvinsya.domain.ids import CategoryId, GroupId, PlayerId
from podvinsya.domain.settings import MatchSettings


@dataclass(frozen=True, slots=True)
class CreateMatch:
    board: BoardSize
    settings: MatchSettings
    player_count: int


@dataclass(frozen=True, slots=True)
class AddPlayer:
    player_id: PlayerId
    name: str
    colour: str


@dataclass(frozen=True, slots=True)
class AssignSecret:
    player_id: PlayerId
    category: CategoryId


@dataclass(frozen=True, slots=True)
class DealBoard:
    pass


@dataclass(frozen=True, slots=True)
class StartMatch:
    pass


@dataclass(frozen=True, slots=True)
class DeclareAttack:
    attacking_group: GroupId
    defending_group: GroupId


@dataclass(frozen=True, slots=True)
class StartDuel:
    pass


@dataclass(frozen=True, slots=True)
class JudgeCorrect:
    pass


@dataclass(frozen=True, slots=True)
class JudgePass:
    pass


@dataclass(frozen=True, slots=True)
class PauseDuel:
    pass


@dataclass(frozen=True, slots=True)
class ResumeDuel:
    pass


@dataclass(frozen=True, slots=True)
class UndoLastJudgement:
    pass


@dataclass(frozen=True, slots=True)
class ExpireTimer:
    deadline_id: int


Command = (
    CreateMatch
    | AddPlayer
    | AssignSecret
    | DealBoard
    | StartMatch
    | DeclareAttack
    | StartDuel
    | JudgeCorrect
    | JudgePass
    | PauseDuel
    | ResumeDuel
    | UndoLastJudgement
    | ExpireTimer
)
```

`backend/src/podvinsya/domain/events.py`:

```python
from dataclasses import dataclass
from datetime import datetime

from podvinsya.domain.board import BoardSize, Cell
from podvinsya.domain.budgets import Budgets
from podvinsya.domain.context import DealtCell
from podvinsya.domain.ids import CategoryId, GroupId, ImageId, PlayerId
from podvinsya.domain.settings import MatchSettings


@dataclass(frozen=True, slots=True)
class MatchCreated:
    board: BoardSize
    settings: MatchSettings
    player_count: int


@dataclass(frozen=True, slots=True)
class PlayerAdded:
    player_id: PlayerId
    name: str
    colour: str


@dataclass(frozen=True, slots=True)
class SecretAssigned:
    player_id: PlayerId
    category: CategoryId


@dataclass(frozen=True, slots=True)
class BoardDealt:
    cells: tuple[DealtCell, ...]


@dataclass(frozen=True, slots=True)
class MatchStarted:
    turn_order: tuple[PlayerId, ...]


@dataclass(frozen=True, slots=True)
class AttackDeclared:
    attacker: PlayerId
    defender: PlayerId
    attacking_group: GroupId
    defending_group: GroupId
    category: CategoryId
    image_order: tuple[ImageId, ...]
    budgets: Budgets


@dataclass(frozen=True, slots=True)
class DuelStarted:
    anchor: datetime


@dataclass(frozen=True, slots=True)
class AnswerAccepted:
    player: PlayerId
    image_index: int
    charged_ms: int
    next_answering: PlayerId
    anchor: datetime


@dataclass(frozen=True, slots=True)
class PassUsed:
    player: PlayerId
    image_index: int
    charged_ms: int
    penalty_ms: int
    anchor: datetime | None


@dataclass(frozen=True, slots=True)
class DuelPaused:
    charged_ms: int


@dataclass(frozen=True, slots=True)
class DuelResumed:
    anchor: datetime


@dataclass(frozen=True, slots=True)
class JudgementUndone:
    undone_seq: int
    budgets: Budgets
    answering: PlayerId
    image_index: int
    anchor: datetime


@dataclass(frozen=True, slots=True)
class DuelResolved:
    winner: PlayerId
    loser: PlayerId
    surviving_group: GroupId
    absorbed_group: GroupId
    absorbed_cells: frozenset[Cell]
    burned_category: CategoryId


@dataclass(frozen=True, slots=True)
class PlayerEliminated:
    player_id: PlayerId


@dataclass(frozen=True, slots=True)
class MatchWon:
    player_id: PlayerId


Event = (
    MatchCreated
    | PlayerAdded
    | SecretAssigned
    | BoardDealt
    | MatchStarted
    | AttackDeclared
    | DuelStarted
    | AnswerAccepted
    | PassUsed
    | DuelPaused
    | DuelResumed
    | JudgementUndone
    | DuelResolved
    | PlayerEliminated
    | MatchWon
)
```

`backend/src/podvinsya/domain/context.py`:

```python
from dataclasses import dataclass
from datetime import datetime

from podvinsya.domain.board import Cell
from podvinsya.domain.ids import CategoryId, GroupId, ImageId, PlayerId


@dataclass(frozen=True, slots=True)
class DealtCell:
    cell: Cell
    owner: PlayerId
    category: CategoryId
    group_id: GroupId
    revealed: bool


@dataclass(frozen=True, slots=True)
class DealPlan:
    cells: tuple[DealtCell, ...]


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Every non-deterministic input the domain needs, supplied as a value.

    The domain never reads a clock and never draws a random number: the caller
    resolves both and hands the results in, which is what makes decide() pure
    and every test deterministic.
    """

    now: datetime
    deal: DealPlan | None = None
    image_order: tuple[ImageId, ...] | None = None
```

- [ ] **Step 4: Написать диспетчеры**

`backend/src/podvinsya/domain/decide.py`:

```python
from podvinsya.domain.actions import Command, CreateMatch
from podvinsya.domain.board import validate_board
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.state import MatchState, MatchStatus


def decide(state: MatchState, command: Command, ctx: DecisionContext) -> tuple[Event, ...]:
    match command:
        case CreateMatch():
            return _create_match(state, command)
        case _:
            raise NotImplementedError(type(command).__name__)


def _create_match(state: MatchState, command: CreateMatch) -> tuple[Event, ...]:
    if state.seq != 0:
        raise Rejected(RejectionReason.WRONG_STATUS)
    validate_board(command.board, command.player_count)
    return (
        MatchCreated(
            board=command.board,
            settings=command.settings,
            player_count=command.player_count,
        ),
    )
```

`backend/src/podvinsya/domain/evolve.py`:

```python
from collections.abc import Iterable
from dataclasses import replace

from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.state import MatchState


def evolve(state: MatchState, event: Event) -> MatchState:
    match event:
        case MatchCreated():
            evolved = replace(
                state,
                board=event.board,
                settings=event.settings,
                player_count=event.player_count,
            )
        case _:
            raise NotImplementedError(type(event).__name__)
    return replace(evolved, seq=state.seq + 1)


def fold(state: MatchState, events: Iterable[Event]) -> MatchState:
    for event in events:
        state = evolve(state, event)
    return state
```

Поле `player_count` уже объявлено в `MatchState` задачей 2 — здесь оно впервые заполняется.

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `cd backend && python -m pytest tests/domain/test_dispatch.py -v && python -m mypy`
Expected: PASS, mypy чисто.

- [ ] **Step 6: Коммит**

```bash
git add backend/src/podvinsya/domain backend/tests/domain/test_dispatch.py
git commit -m "feat(domain): commands, events, decision context, decide/evolve dispatchers"
```

---

### Task 4: Игроки и секреты

**Files:**
- Modify: `backend/src/podvinsya/domain/decide.py`
- Modify: `backend/src/podvinsya/domain/evolve.py`
- Create: `backend/tests/domain/conftest.py`
- Test: `backend/tests/domain/test_setup.py`

**Interfaces:**
- Consumes: `decide`, `evolve`, `AddPlayer`, `AssignSecret`, `PlayerAdded`, `SecretAssigned`.
- Produces: фикстуру `apply(state, command, ctx=...) -> MatchState`, которая прогоняет `decide` и сворачивает результат; фикстуру `setup_state(player_count=4) -> MatchState` — партия с добавленными игроками и назначенными секретами, построенная **через `decide`/`evolve`**, а не конструированием состояния руками.

- [ ] **Step 1: Написать построители тестов**

`backend/tests/domain/conftest.py`:

```python
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from podvinsya.domain.actions import AddPlayer, AssignSecret, Command, CreateMatch
from podvinsya.domain.board import BoardSize
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.evolve import fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import CategoryId, MatchId, PlayerId
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchState

BASE_TIME = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
COLOURS = ("#e5484d", "#3b82f6", "#22c55e", "#a855f7")
BOARDS = {2: BoardSize(3, 4), 3: BoardSize(3, 6), 4: BoardSize(4, 6)}


def at(seconds: float) -> datetime:
    return BASE_TIME + timedelta(seconds=seconds)


def apply(
    state: MatchState,
    command: Command,
    *,
    now: datetime = BASE_TIME,
    **ctx_kwargs: object,
) -> MatchState:
    ctx = DecisionContext(now=now, **ctx_kwargs)  # type: ignore[arg-type]
    return fold(state, decide(state, command, ctx))


@pytest.fixture
def created_state() -> MatchState:
    state = create_initial_state(MatchId(uuid4()), BOARDS[4], MatchSettings())
    return apply(state, CreateMatch(board=BOARDS[4], settings=MatchSettings(), player_count=4))


def build_setup_state(player_count: int = 4) -> tuple[MatchState, tuple[PlayerId, ...]]:
    """A match with all players added and all secrets assigned, built through decide/evolve."""
    board = BOARDS[player_count]
    state = create_initial_state(MatchId(uuid4()), board, MatchSettings())
    state = apply(
        state, CreateMatch(board=board, settings=MatchSettings(), player_count=player_count)
    )
    players = tuple(PlayerId(uuid4()) for _ in range(player_count))
    for index, player_id in enumerate(players):
        state = apply(
            state,
            AddPlayer(player_id=player_id, name=f"P{index + 1}", colour=COLOURS[index]),
        )
        state = apply(state, AssignSecret(player_id=player_id, category=CategoryId(uuid4())))
    return state, players


@pytest.fixture
def setup_state() -> tuple[MatchState, tuple[PlayerId, ...]]:
    return build_setup_state(4)
```

- [ ] **Step 2: Написать падающий тест**

`backend/tests/domain/test_setup.py`:

```python
from uuid import uuid4

import pytest

from podvinsya.domain.actions import AddPlayer, AssignSecret
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.ids import CategoryId, PlayerId
from podvinsya.domain.state import MatchState

from .conftest import BASE_TIME, apply, build_setup_state


def test_adding_a_player_records_name_and_colour(created_state: MatchState) -> None:
    player_id = PlayerId(uuid4())
    state = apply(created_state, AddPlayer(player_id=player_id, name="Кира", colour="#a855f7"))
    assert len(state.players) == 1
    player = state.player(player_id)
    assert player.name == "Кира"
    assert player.colour == "#a855f7"
    assert player.eliminated is False


def test_adding_the_same_player_twice_is_rejected(created_state: MatchState) -> None:
    player_id = PlayerId(uuid4())
    state = apply(created_state, AddPlayer(player_id=player_id, name="Кира", colour="#a855f7"))
    with pytest.raises(Rejected) as excinfo:
        apply(state, AddPlayer(player_id=player_id, name="Кира", colour="#3b82f6"))
    assert excinfo.value.reason is RejectionReason.DUPLICATE_PLAYER


def test_adding_more_players_than_declared_is_rejected(created_state: MatchState) -> None:
    state = created_state
    for index in range(4):
        state = apply(
            state, AddPlayer(player_id=PlayerId(uuid4()), name=f"P{index}", colour="#fff")
        )
    with pytest.raises(Rejected) as excinfo:
        apply(state, AddPlayer(player_id=PlayerId(uuid4()), name="fifth", colour="#000"))
    assert excinfo.value.reason is RejectionReason.PLAYER_COUNT_INVALID


def test_secret_is_bound_to_its_owner(created_state: MatchState) -> None:
    player_id = PlayerId(uuid4())
    category = CategoryId(uuid4())
    state = apply(created_state, AddPlayer(player_id=player_id, name="Дед", colour="#22c55e"))
    state = apply(state, AssignSecret(player_id=player_id, category=category))
    assert state.secrets[player_id] == category


def test_secret_for_unknown_player_is_rejected(created_state: MatchState) -> None:
    with pytest.raises(Rejected) as excinfo:
        apply(
            created_state,
            AssignSecret(player_id=PlayerId(uuid4()), category=CategoryId(uuid4())),
        )
    assert excinfo.value.reason is RejectionReason.UNKNOWN_PLAYER


def test_reassigning_a_secret_replaces_it(created_state: MatchState) -> None:
    player_id = PlayerId(uuid4())
    first, second = CategoryId(uuid4()), CategoryId(uuid4())
    state = apply(created_state, AddPlayer(player_id=player_id, name="Дед", colour="#22c55e"))
    state = apply(state, AssignSecret(player_id=player_id, category=first))
    state = apply(state, AssignSecret(player_id=player_id, category=second))
    assert state.secrets[player_id] == second


def test_reassigning_the_same_secret_emits_nothing(created_state: MatchState) -> None:
    player_id = PlayerId(uuid4())
    category = CategoryId(uuid4())
    state = apply(created_state, AddPlayer(player_id=player_id, name="Дед", colour="#22c55e"))
    state = apply(state, AssignSecret(player_id=player_id, category=category))

    command = AssignSecret(player_id=player_id, category=category)
    assert decide(state, command, DecisionContext(now=BASE_TIME)) == (), (
        "legal but unchanged must produce no event — neither a rejection nor a duplicate"
    )

    unchanged = apply(state, command)
    assert unchanged.seq == state.seq
    assert unchanged.secrets[player_id] == category


def test_two_players_cannot_share_a_secret_category(created_state: MatchState) -> None:
    alice, bob = PlayerId(uuid4()), PlayerId(uuid4())
    category = CategoryId(uuid4())
    state = apply(created_state, AddPlayer(player_id=alice, name="A", colour="#fff"))
    state = apply(state, AddPlayer(player_id=bob, name="B", colour="#000"))
    state = apply(state, AssignSecret(player_id=alice, category=category))
    with pytest.raises(Rejected) as excinfo:
        apply(state, AssignSecret(player_id=bob, category=category))
    assert excinfo.value.reason is RejectionReason.DUPLICATE_CATEGORY


def test_setup_builder_produces_a_complete_setup() -> None:
    state, players = build_setup_state(4)
    assert len(state.players) == 4
    assert set(state.secrets) == set(players)
```

- [ ] **Step 3: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_setup.py -v`
Expected: FAIL — `NotImplementedError: AddPlayer`

- [ ] **Step 4: Добавить ветки в decide**

В `decide.py` добавить в `match` до `case _`:

```python
        case AddPlayer():
            return _add_player(state, command)
        case AssignSecret():
            return _assign_secret(state, command)
```

и функции:

```python
def _require_setup(state: MatchState) -> None:
    if state.status is not MatchStatus.SETUP:
        raise Rejected(RejectionReason.WRONG_STATUS)


def _add_player(state: MatchState, command: AddPlayer) -> tuple[Event, ...]:
    _require_setup(state)
    if any(p.id == command.player_id for p in state.players):
        raise Rejected(RejectionReason.DUPLICATE_PLAYER)
    if len(state.players) >= state.player_count:
        raise Rejected(RejectionReason.PLAYER_COUNT_INVALID)
    return (PlayerAdded(command.player_id, command.name, command.colour),)


def _assign_secret(state: MatchState, command: AssignSecret) -> tuple[Event, ...]:
    _require_setup(state)
    if not any(p.id == command.player_id for p in state.players):
        raise Rejected(RejectionReason.UNKNOWN_PLAYER)
    for owner, category in state.secrets.items():
        if category == command.category and owner != command.player_id:
            raise Rejected(RejectionReason.DUPLICATE_CATEGORY)
    if state.secrets.get(command.player_id) == command.category:
        return ()
    return (SecretAssigned(command.player_id, command.category),)
```

Последняя ветка — пример «легально, но ничего не меняет»: повторное назначение того же секрета возвращает пустой кортеж, а не событие.

- [ ] **Step 5: Добавить ветки в evolve**

```python
        case PlayerAdded():
            evolved = replace(
                state,
                players=(*state.players, Player(event.player_id, event.name, event.colour)),
            )
        case SecretAssigned():
            evolved = replace(
                state,
                secrets={**state.secrets, event.player_id: event.category},
            )
```

- [ ] **Step 6: Убедиться, что тесты проходят и закоммитить**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy`
Expected: PASS

```bash
git add backend/src/podvinsya/domain backend/tests/domain
git commit -m "feat(domain): add players and assign secrets"
```

---

### Task 5: Раздача и перераздача

**Files:**
- Modify: `backend/src/podvinsya/domain/decide.py`
- Modify: `backend/src/podvinsya/domain/evolve.py`
- Modify: `backend/tests/domain/conftest.py`
- Test: `backend/tests/domain/test_deal.py`

**Interfaces:**
- Consumes: `DealBoard`, `BoardDealt`, `DealPlan`, `DealtCell`.
- Produces: фикстуру `build_dealt_state(player_count=4)` в `conftest.py`, возвращающую `(state, players, deal)`; функцию `make_deal(board, players, secrets) -> DealPlan` там же — детерминированный планировщик раздачи для тестов.

`decide` **не строит** раздачу: она приходит значением в `ctx.deal`. Домен её только проверяет. Это единственный способ оставить `decide` чистой при случайной раздаче.

- [ ] **Step 1: Дописать построитель раздачи в conftest**

Добавить в `backend/tests/domain/conftest.py`:

```python
from collections import Counter

from podvinsya.domain.actions import DealBoard
from podvinsya.domain.context import DealPlan, DealtCell
from podvinsya.domain.ids import GroupId


def make_deal(
    board: BoardSize,
    players: tuple[PlayerId, ...],
    secrets: dict[PlayerId, CategoryId],
) -> DealPlan:
    """Deterministic deal on a Latin-square pattern, secret on each owner's first cell.

    Owner is (col + row) % n, so no two orthogonally adjacent cells share an
    owner and every neighbour is a legal target from move one. A plain
    round-robin over the row-major cell order would hand each player a solid
    column whenever the board width is a multiple of the player count, and
    then "the cell below is a legal target" stops being true.

    Even counts hold for the three default boards in BOARDS; the assert at the
    end makes any other board loud rather than silently lopsided.
    """
    n = len(players)
    cells = board.cells()
    per_player = len(cells) // n
    seen: set[PlayerId] = set()
    dealt: list[DealtCell] = []
    for cell in cells:
        owner = players[(cell.col + cell.row) % n]
        is_first_for_owner = owner not in seen
        seen.add(owner)
        category = secrets[owner] if is_first_for_owner else CategoryId(uuid4())
        dealt.append(
            DealtCell(
                cell=cell,
                owner=owner,
                category=category,
                group_id=GroupId(uuid4()),
                revealed=not is_first_for_owner,
            )
        )
    counts = Counter(d.owner for d in dealt)
    assert set(counts.values()) == {per_player}, f"uneven deal: {counts}"
    return DealPlan(cells=tuple(dealt))


def build_dealt_state(player_count: int = 4) -> tuple[MatchState, tuple[PlayerId, ...]]:
    state, players = build_setup_state(player_count)
    deal = make_deal(state.board, players, dict(state.secrets))
    return apply(state, DealBoard(), deal=deal), players


@pytest.fixture
def dealt_state() -> tuple[MatchState, tuple[PlayerId, ...]]:
    return build_dealt_state(4)
```

- [ ] **Step 2: Написать падающий тест**

`backend/tests/domain/test_deal.py`:

```python
from collections import Counter
from dataclasses import replace
from uuid import uuid4

import pytest

from podvinsya.domain.actions import DealBoard
from podvinsya.domain.context import DealPlan, DealtCell
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.ids import GroupId

from .conftest import apply, build_dealt_state, build_setup_state, make_deal


def test_deal_covers_every_cell_exactly_once() -> None:
    state, _ = build_dealt_state(4)
    covered = [cell for group in state.groups.values() for cell in group.cells]
    assert sorted(covered) == sorted(state.board.cells())


def test_every_player_gets_the_same_number_of_cells() -> None:
    state, players = build_dealt_state(4)
    counts = Counter(
        group.owner for group in state.groups.values() for _ in group.cells
    )
    assert set(counts.values()) == {state.board.cell_count // len(players)}


def test_each_cell_starts_as_its_own_group_with_a_distinct_category() -> None:
    state, _ = build_dealt_state(4)
    assert len(state.groups) == state.board.cell_count
    assert all(len(group.cells) == 1 for group in state.groups.values())
    categories = [group.category for group in state.groups.values()]
    assert len(set(categories)) == len(categories)


def test_each_secret_lands_on_its_owner_and_starts_unrevealed() -> None:
    state, players = build_dealt_state(4)
    for player_id in players:
        secret = state.secrets[player_id]
        holder = next(g for g in state.groups.values() if g.category == secret)
        assert holder.owner == player_id
        assert holder.revealed is False


def test_non_secret_groups_start_revealed() -> None:
    state, _ = build_dealt_state(4)
    secrets = set(state.secrets.values())
    for group in state.groups.values():
        if group.category not in secrets:
            assert group.revealed is True


def test_redeal_replaces_the_previous_deal_entirely() -> None:
    state, players = build_dealt_state(4)
    first_group_ids = set(state.groups)
    second = make_deal(state.board, players, dict(state.secrets))
    state = apply(state, DealBoard(), deal=second)
    assert set(state.groups).isdisjoint(first_group_ids)
    assert len(state.groups) == state.board.cell_count


def test_deal_without_a_plan_is_rejected() -> None:
    state, _ = build_setup_state(4)
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard())
    assert excinfo.value.reason is RejectionReason.DEAL_INVALID


def test_deal_missing_a_cell_is_rejected() -> None:
    state, players = build_setup_state(4)
    plan = make_deal(state.board, players, dict(state.secrets))
    truncated = DealPlan(cells=plan.cells[:-1])
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=truncated)
    assert excinfo.value.reason is RejectionReason.DEAL_INVALID


def test_deal_with_a_duplicate_category_is_rejected() -> None:
    state, players = build_setup_state(4)
    plan = make_deal(state.board, players, dict(state.secrets))
    clashing = DealPlan(
        cells=(
            *plan.cells[:-1],
            DealtCell(
                cell=plan.cells[-1].cell,
                owner=plan.cells[-1].owner,
                category=plan.cells[0].category,
                group_id=GroupId(uuid4()),
                revealed=True,
            ),
        )
    )
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=clashing)
    assert excinfo.value.reason is RejectionReason.DEAL_INVALID


def test_deal_with_a_secret_on_the_wrong_owner_is_rejected() -> None:
    state, players = build_setup_state(4)
    plan = make_deal(state.board, players, dict(state.secrets))
    secret_of_first = state.secrets[players[0]]

    # Swap owners between the first player's secret cell and one plain cell of the
    # second player. Simply moving the secret across would unbalance the per-player
    # counts and trip the earlier balance guard, leaving the secret-ownership guard
    # untested while the test still passed on the same DEAL_INVALID reason.
    secret_index = next(i for i, c in enumerate(plan.cells) if c.category == secret_of_first)
    plain_index = next(
        i
        for i, c in enumerate(plan.cells)
        if c.owner == players[1] and c.category not in state.secrets.values()
    )
    cells = list(plan.cells)
    cells[secret_index] = replace(cells[secret_index], owner=players[1])
    cells[plain_index] = replace(cells[plain_index], owner=players[0])

    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=tuple(cells)))
    assert excinfo.value.reason is RejectionReason.DEAL_INVALID


def test_deal_before_every_secret_is_assigned_is_rejected() -> None:
    from podvinsya.domain.actions import AddPlayer, CreateMatch
    from podvinsya.domain.board import BoardSize
    from podvinsya.domain.genesis import create_initial_state
    from podvinsya.domain.ids import MatchId, PlayerId
    from podvinsya.domain.settings import MatchSettings

    board = BoardSize(4, 6)
    state = create_initial_state(MatchId(uuid4()), board, MatchSettings())
    state = apply(state, CreateMatch(board=board, settings=MatchSettings(), player_count=4))
    players = tuple(PlayerId(uuid4()) for _ in range(4))
    for index, player_id in enumerate(players):
        state = apply(state, AddPlayer(player_id=player_id, name=f"P{index}", colour="#fff"))
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=()))
    assert excinfo.value.reason is RejectionReason.SECRET_MISSING
```

- [ ] **Step 3: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_deal.py -v`
Expected: FAIL — `NotImplementedError: DealBoard`

- [ ] **Step 4: Реализовать ветку decide**

В `decide.py`:

```python
        case DealBoard():
            return _deal_board(state, ctx)
```

```python
def _deal_board(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    _require_setup(state)
    if len(state.players) != state.player_count:
        raise Rejected(RejectionReason.PLAYER_COUNT_INVALID)
    if any(p.id not in state.secrets for p in state.players):
        raise Rejected(RejectionReason.SECRET_MISSING)
    if ctx.deal is None:
        raise Rejected(RejectionReason.DEAL_INVALID)

    plan = ctx.deal
    cells = [dealt.cell for dealt in plan.cells]
    if sorted(cells) != sorted(state.board.cells()):
        raise Rejected(RejectionReason.DEAL_INVALID)

    categories = [dealt.category for dealt in plan.cells]
    if len(set(categories)) != len(categories):
        raise Rejected(RejectionReason.DEAL_INVALID)

    group_ids = [dealt.group_id for dealt in plan.cells]
    if len(set(group_ids)) != len(group_ids):
        raise Rejected(RejectionReason.DEAL_INVALID)

    per_player = state.board.cell_count // state.player_count
    owners = Counter(dealt.owner for dealt in plan.cells)
    if set(owners) != {p.id for p in state.players} or set(owners.values()) != {per_player}:
        raise Rejected(RejectionReason.DEAL_INVALID)

    secret_owner = {category: owner for owner, category in state.secrets.items()}
    for dealt in plan.cells:
        expected_owner = secret_owner.get(dealt.category)
        if expected_owner is not None:
            if dealt.owner != expected_owner or dealt.revealed:
                raise Rejected(RejectionReason.DEAL_INVALID)
        elif not dealt.revealed:
            raise Rejected(RejectionReason.DEAL_INVALID)
    if len({d.category for d in plan.cells} & set(state.secrets.values())) != len(state.secrets):
        raise Rejected(RejectionReason.DEAL_INVALID)

    return (BoardDealt(cells=plan.cells),)
```

Добавить `from collections import Counter` в начало `decide.py`.

- [ ] **Step 5: Реализовать ветку evolve**

```python
        case BoardDealt():
            evolved = replace(
                state,
                groups={
                    dealt.group_id: Group(
                        id=dealt.group_id,
                        owner=dealt.owner,
                        category=dealt.category,
                        cells=frozenset({dealt.cell}),
                        revealed=dealt.revealed,
                    )
                    for dealt in event.cells
                },
                played_categories=frozenset(),
            )
```

Перераздача просто заменяет весь словарь групп — отдельного события отмены не нужно.

- [ ] **Step 6: Убедиться, что тесты проходят и закоммитить**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy`
Expected: PASS

```bash
git add backend/src/podvinsya/domain backend/tests/domain
git commit -m "feat(domain): validated board deal and redeal"
```

---

### Task 6: Старт партии и порядок хода

**Files:**
- Modify: `backend/src/podvinsya/domain/decide.py`
- Modify: `backend/src/podvinsya/domain/evolve.py`
- Create: `backend/src/podvinsya/domain/rules.py`
- Modify: `backend/tests/domain/conftest.py`
- Test: `backend/tests/domain/test_start.py`

**Interfaces:**
- Consumes: `StartMatch`, `MatchStarted`.
- Produces: `rules.next_turn(state) -> tuple[int, int]` возвращает `(turn_index, round_no)` следующего живого игрока; фикстуру `build_running_state(player_count=4)` в `conftest.py`.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/domain/test_start.py`:

```python
from dataclasses import replace

import pytest

from podvinsya.domain.actions import StartMatch
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.rules import next_turn
from podvinsya.domain.state import MatchStatus

from .conftest import apply, build_dealt_state, build_setup_state


def test_start_switches_status_and_fixes_turn_order() -> None:
    state, players = build_dealt_state(4)
    state = apply(state, StartMatch())
    assert state.status is MatchStatus.RUNNING
    assert state.turn_order == players
    assert state.turn_index == 0
    assert state.round_no == 1
    assert state.current_player() == players[0]


def test_start_before_dealing_is_rejected() -> None:
    state, _ = build_setup_state(4)
    with pytest.raises(Rejected) as excinfo:
        apply(state, StartMatch())
    assert excinfo.value.reason is RejectionReason.DEAL_INVALID


def test_start_twice_is_rejected() -> None:
    state, _ = build_dealt_state(4)
    state = apply(state, StartMatch())
    with pytest.raises(Rejected) as excinfo:
        apply(state, StartMatch())
    assert excinfo.value.reason is RejectionReason.WRONG_STATUS


def test_next_turn_advances_and_wraps_the_round() -> None:
    state, players = build_dealt_state(4)
    state = apply(state, StartMatch())
    index, round_no = next_turn(state)
    assert (index, round_no) == (1, 1)

    at_last = replace(state, turn_index=3)
    assert next_turn(at_last) == (0, 2)


def test_next_turn_skips_eliminated_players() -> None:
    state, players = build_dealt_state(4)
    state = apply(state, StartMatch())
    knocked_out = tuple(
        replace(p, eliminated=(p.id == players[1])) for p in state.players
    )
    state = replace(state, players=knocked_out)
    assert next_turn(state) == (2, 1)


def test_next_turn_falls_back_when_every_player_is_eliminated() -> None:
    state, _ = build_dealt_state(4)
    state = apply(state, StartMatch())

    # The cursor is deliberately moved off (0, 1) first. Left at its starting value,
    # this assertion could not tell "returned the cursor unchanged" apart from a
    # fallback that returned hardcoded zeros — both would be (0, 1).
    wiped = replace(
        state,
        turn_index=2,
        round_no=7,
        players=tuple(replace(p, eliminated=True) for p in state.players),
    )

    # The bounded loop finds nobody and falls through. This must return the cursor
    # unchanged — neither spinning forever nor raising. Nothing else pins that.
    assert next_turn(wiped) == (2, 7)
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_start.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'podvinsya.domain.rules'`

- [ ] **Step 3: Написать rules.next_turn**

`backend/src/podvinsya/domain/rules.py`:

```python
from podvinsya.domain.state import MatchState


def next_turn(state: MatchState) -> tuple[int, int]:
    """Return (turn_index, round_no) for the next living player.

    Eliminated players are skipped. The round number increases whenever the
    cursor wraps past the end of the fixed turn order.
    """
    size = len(state.turn_order)
    index = state.turn_index
    round_no = state.round_no
    for _ in range(size):
        index += 1
        if index >= size:
            index = 0
            round_no += 1
        if not state.player(state.turn_order[index]).eliminated:
            return index, round_no
    return state.turn_index, state.round_no
```

- [ ] **Step 4: Реализовать ветки decide и evolve**

В `decide.py`:

```python
        case StartMatch():
            return _start_match(state)
```

```python
def _start_match(state: MatchState) -> tuple[Event, ...]:
    _require_setup(state)
    if len(state.groups) != state.board.cell_count:
        raise Rejected(RejectionReason.DEAL_INVALID)
    return (MatchStarted(turn_order=tuple(p.id for p in state.players)),)
```

В `evolve.py`:

```python
        case MatchStarted():
            evolved = replace(
                state,
                status=MatchStatus.RUNNING,
                turn_order=event.turn_order,
                turn_index=0,
                round_no=1,
            )
```

- [ ] **Step 5: Дописать построитель в conftest**

```python
from podvinsya.domain.actions import StartMatch


def build_running_state(player_count: int = 4) -> tuple[MatchState, tuple[PlayerId, ...]]:
    state, players = build_dealt_state(player_count)
    return apply(state, StartMatch()), players


@pytest.fixture
def running_state() -> tuple[MatchState, tuple[PlayerId, ...]]:
    return build_running_state(4)
```

- [ ] **Step 6: Убедиться, что тесты проходят и закоммитить**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy`
Expected: PASS

```bash
git add backend/src/podvinsya/domain backend/tests/domain
git commit -m "feat(domain): start match, fixed turn order, elimination-aware rotation"
```

---

### Task 7: Бонус времени и легальные цели

**Files:**
- Modify: `backend/src/podvinsya/domain/rules.py`
- Test: `backend/tests/domain/test_rules.py`

**Interfaces:**
- Consumes: `Group`, `MatchState`, `groups_are_adjacent`, `MatchSettings`.
- Produces: `time_bonus_ms(group: Group, settings: MatchSettings) -> int`; `starting_budget_ms(group: Group, settings: MatchSettings) -> int`; `legal_targets(state: MatchState, attacking_group: GroupId) -> frozenset[GroupId]`; `group_containing(state: MatchState, cell: Cell) -> Group`.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/domain/test_rules.py`:

```python
from dataclasses import replace
from uuid import uuid4

from podvinsya.domain.board import Cell
from podvinsya.domain.ids import CategoryId, GroupId, PlayerId
from podvinsya.domain.rules import (
    group_containing,
    legal_targets,
    starting_budget_ms,
    time_bonus_ms,
)
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import Group

from .conftest import build_running_state


def _group(size: int) -> Group:
    return Group(
        id=GroupId(uuid4()),
        owner=PlayerId(uuid4()),
        category=CategoryId(uuid4()),
        cells=frozenset(Cell(i, 0) for i in range(size)),
        revealed=True,
    )


def test_single_cell_group_gives_no_bonus() -> None:
    assert time_bonus_ms(_group(1), MatchSettings()) == 0


def test_bonus_is_one_second_per_cell_beyond_the_first() -> None:
    assert time_bonus_ms(_group(6), MatchSettings()) == 5_000


def test_bonus_is_capped() -> None:
    settings = MatchSettings()
    assert time_bonus_ms(_group(16), settings) == 15_000
    assert time_bonus_ms(_group(30), settings) == 15_000


def test_starting_budget_is_base_plus_bonus() -> None:
    assert starting_budget_ms(_group(6), MatchSettings()) == 65_000


def test_legal_targets_are_orthogonally_adjacent_enemy_groups() -> None:
    state, players = build_running_state(4)
    corner = group_containing(state, Cell(0, 0))
    targets = legal_targets(state, corner.id)

    right = group_containing(state, Cell(1, 0))
    below = group_containing(state, Cell(0, 1))
    diagonal = group_containing(state, Cell(1, 1))

    assert right.id in targets
    assert below.id in targets
    assert diagonal.id not in targets
    assert all(state.groups[gid].owner != corner.owner for gid in targets)


def test_own_groups_are_never_targets() -> None:
    state, _ = build_running_state(4)
    corner = group_containing(state, Cell(0, 0))
    own = {g.id for g in state.groups.values() if g.owner == corner.owner}
    assert legal_targets(state, corner.id).isdisjoint(own)


def test_merged_group_reaches_further() -> None:
    state, _ = build_running_state(4)
    left = group_containing(state, Cell(0, 0))
    right = group_containing(state, Cell(1, 0))
    merged = replace(left, cells=left.cells | right.cells)
    groups = {gid: g for gid, g in state.groups.items() if gid != right.id}
    groups[merged.id] = merged
    widened = replace(state, groups=groups)

    targets = legal_targets(widened, merged.id)
    assert group_containing(widened, Cell(2, 0)).id in targets
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_rules.py -v`
Expected: FAIL — `ImportError: cannot import name 'time_bonus_ms'`

- [ ] **Step 3: Дописать rules.py**

```python
from podvinsya.domain.board import Cell, groups_are_adjacent
from podvinsya.domain.ids import GroupId
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import Group, MatchState


def time_bonus_ms(group: Group, settings: MatchSettings) -> int:
    """+(N-1) seconds for a group of N cells, capped. Spec §2.5."""
    return min(settings.bonus_cap_ms, (len(group.cells) - 1) * 1000)


def starting_budget_ms(group: Group, settings: MatchSettings) -> int:
    return settings.base_ms + time_bonus_ms(group, settings)


def group_containing(state: MatchState, cell: Cell) -> Group:
    for group in state.groups.values():
        if cell in group.cells:
            return group
    raise KeyError(cell)


def legal_targets(state: MatchState, attacking_group: GroupId) -> frozenset[GroupId]:
    attacker = state.groups[attacking_group]
    return frozenset(
        candidate.id
        for candidate in state.groups.values()
        if candidate.owner != attacker.owner
        and groups_are_adjacent(attacker.cells, candidate.cells, state.board)
    )
```

- [ ] **Step 4: Убедиться, что тесты проходят и закоммитить**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy`
Expected: PASS

```bash
git add backend/src/podvinsya/domain/rules.py backend/tests/domain/test_rules.py
git commit -m "feat(domain): time bonus from the duelling group, legal attack targets"
```

---

### Task 8: Объявление атаки

**Files:**
- Modify: `backend/src/podvinsya/domain/decide.py`
- Modify: `backend/src/podvinsya/domain/evolve.py`
- Modify: `backend/tests/domain/conftest.py`
- Test: `backend/tests/domain/test_declare.py`

**Interfaces:**
- Consumes: `DeclareAttack`, `AttackDeclared`, `legal_targets`, `starting_budget_ms`.
- Produces: фикстуру `build_declared_state()` в `conftest.py`, возвращающую `(state, players, attacking_group_id, defending_group_id)`; константу `IMAGE_POOL` — детерминированный кортеж из 40 `ImageId`.

Объявление атаки раскрывает категорию защитника, тиражирует порядок картинок целиком и считает оба бюджета. Тираж происходит здесь, а не на старте дуэли: иначе окно прогрева для предзагрузки картинок фиктивно (спека §3.5).

- [ ] **Step 1: Дописать построитель в conftest**

```python
from podvinsya.domain.actions import DeclareAttack
from podvinsya.domain.ids import ImageId
from podvinsya.domain.rules import legal_targets

IMAGE_POOL: tuple[ImageId, ...] = tuple(ImageId(uuid4()) for _ in range(40))


def build_declared_state() -> tuple[MatchState, tuple[PlayerId, ...], GroupId, GroupId]:
    state, players = build_running_state(4)
    attacker_id = state.current_player()
    attacking = next(g for g in state.groups.values() if g.owner == attacker_id
                     and legal_targets(state, g.id))
    defending_id = sorted(legal_targets(state, attacking.id))[0]
    state = apply(
        state,
        DeclareAttack(attacking_group=attacking.id, defending_group=defending_id),
        image_order=IMAGE_POOL,
    )
    return state, players, attacking.id, defending_id


@pytest.fixture
def declared_state() -> tuple[MatchState, tuple[PlayerId, ...], GroupId, GroupId]:
    return build_declared_state()
```

`sorted(...)` над множеством `GroupId` требует детерминированного порядка: `GroupId` — `UUID`, они сравнимы, порядок стабилен.

- [ ] **Step 2: Написать падающий тест**

`backend/tests/domain/test_declare.py`:

```python
from uuid import uuid4

import pytest

from podvinsya.domain.actions import DeclareAttack
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.ids import GroupId
from podvinsya.domain.rules import legal_targets, starting_budget_ms
from podvinsya.domain.state import DuelPhase

from .conftest import IMAGE_POOL, apply, build_declared_state, build_running_state


def test_declaring_creates_a_declared_duel_without_starting_the_clock() -> None:
    state, _, attacking, defending = build_declared_state()
    duel = state.duel
    assert duel is not None
    assert duel.phase is DuelPhase.DECLARED
    assert duel.anchor is None
    assert duel.index == 0
    assert duel.attacking_group == attacking
    assert duel.defending_group == defending


def test_the_defender_category_is_played_and_the_attacker_answers_first() -> None:
    state, _, attacking, defending = build_declared_state()
    duel = state.duel
    assert duel is not None
    assert duel.category == state.groups[defending].category
    assert duel.answering == duel.attacker
    assert duel.attacker == state.groups[attacking].owner
    assert duel.defender == state.groups[defending].owner


def test_declaring_reveals_the_defending_group_only() -> None:
    state, players = build_running_state(4)
    attacker_id = state.current_player()
    attacking = next(
        g for g in state.groups.values() if g.owner == attacker_id and not g.revealed
    )
    targets = legal_targets(state, attacking.id)
    defending_id = sorted(targets)[0]
    before_attacker_revealed = attacking.revealed

    state = apply(
        state,
        DeclareAttack(attacking_group=attacking.id, defending_group=defending_id),
        image_order=IMAGE_POOL,
    )

    assert state.groups[defending_id].revealed is True
    assert state.groups[attacking.id].revealed is before_attacker_revealed is False


def test_budgets_come_from_each_side_own_group() -> None:
    state, _, attacking, defending = build_declared_state()
    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(duel.attacker) == starting_budget_ms(
        state.groups[attacking], state.settings
    )
    assert duel.budgets.get(duel.defender) == starting_budget_ms(
        state.groups[defending], state.settings
    )


def test_budgets_are_not_swapped_between_the_sides() -> None:
    from dataclasses import replace

    state, _ = build_running_state(4)
    attacker_id = state.current_player()
    attacking = next(
        g for g in state.groups.values() if g.owner == attacker_id and legal_targets(state, g.id)
    )
    defending_id = sorted(legal_targets(state, attacking.id))[0]
    defending = state.groups[defending_id]

    # Grow the attacking group so the two sides carry different bonuses. Every group is
    # a single cell at declaration time, so both bonuses are zero and a swapped wiring
    # is invisible — this is the only test that can see the difference. The grown group
    # is deliberately not connected: nothing validates connectivity at declaration, and
    # the Latin-square deal leaves a player no same-owner orthogonal neighbour to absorb.
    donor = next(
        g
        for g in state.groups.values()
        if g.owner == attacker_id and g.id not in (attacking.id, defending_id)
    )
    grown = replace(attacking, cells=attacking.cells | donor.cells)
    groups = {gid: g for gid, g in state.groups.items() if gid != donor.id}
    groups[grown.id] = grown
    state = replace(state, groups=groups)

    state = apply(
        state,
        DeclareAttack(attacking_group=grown.id, defending_group=defending_id),
        image_order=IMAGE_POOL,
    )

    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(duel.attacker) == starting_budget_ms(grown, state.settings)
    assert duel.budgets.get(duel.defender) == starting_budget_ms(defending, state.settings)
    assert duel.budgets.get(duel.attacker) != duel.budgets.get(duel.defender), (
        "the sides must differ here, or this test cannot see a swap"
    )


def test_the_whole_image_order_is_drawn_up_front() -> None:
    state, _, _, _ = build_declared_state()
    duel = state.duel
    assert duel is not None
    assert duel.image_order == IMAGE_POOL
    assert len(set(duel.image_order)) == len(duel.image_order)


def test_attacking_out_of_turn_is_rejected() -> None:
    state, players = build_running_state(4)
    current = state.current_player()
    other = next(p for p in players if p != current)

    # The defending group deliberately belongs to the CURRENT player. A guard that
    # compared the defender's owner to the current player — rather than the attacker's —
    # would let this declaration through, so this pairing is what gives the test the
    # power to see that mutation. Any enemy target would satisfy the assertion.
    attacking, defending_id = next(
        (g, target)
        for g in state.groups.values()
        if g.owner == other
        for target in sorted(legal_targets(state, g.id))
        if state.groups[target].owner == current
    )

    with pytest.raises(Rejected) as excinfo:
        apply(
            state,
            DeclareAttack(attacking_group=attacking.id, defending_group=defending_id),
            image_order=IMAGE_POOL,
        )
    assert excinfo.value.reason is RejectionReason.NOT_YOUR_TURN


def test_attacking_your_own_group_is_rejected() -> None:
    state, _ = build_running_state(4)
    attacker_id = state.current_player()
    own = [g for g in state.groups.values() if g.owner == attacker_id]
    with pytest.raises(Rejected) as excinfo:
        apply(state, DeclareAttack(attacking_group=own[0].id, defending_group=own[1].id),
              image_order=IMAGE_POOL)
    assert excinfo.value.reason is RejectionReason.TARGET_IS_YOURS


def test_attacking_a_non_adjacent_group_is_rejected() -> None:
    from podvinsya.domain.board import groups_are_adjacent

    state, _ = build_running_state(4)
    attacker_id = state.current_player()
    attacking = next(g for g in state.groups.values() if g.owner == attacker_id)
    far = next(
        g
        for g in state.groups.values()
        if g.owner != attacker_id
        and not groups_are_adjacent(attacking.cells, g.cells, state.board)
    )
    with pytest.raises(Rejected) as excinfo:
        apply(state, DeclareAttack(attacking_group=attacking.id, defending_group=far.id),
              image_order=IMAGE_POOL)
    assert excinfo.value.reason is RejectionReason.NOT_ADJACENT


def test_unknown_group_is_rejected() -> None:
    state, _ = build_running_state(4)
    attacker_id = state.current_player()
    attacking = next(g for g in state.groups.values() if g.owner == attacker_id)
    with pytest.raises(Rejected) as excinfo:
        apply(state, DeclareAttack(attacking_group=attacking.id, defending_group=GroupId(uuid4())),
              image_order=IMAGE_POOL)
    assert excinfo.value.reason is RejectionReason.UNKNOWN_GROUP


def test_declaring_while_a_duel_exists_is_rejected() -> None:
    state, _, attacking, defending = build_declared_state()
    with pytest.raises(Rejected) as excinfo:
        apply(state, DeclareAttack(attacking_group=attacking, defending_group=defending),
              image_order=IMAGE_POOL)
    assert excinfo.value.reason is RejectionReason.DUEL_IN_PROGRESS


def test_declaring_without_an_image_order_is_rejected() -> None:
    state, _ = build_running_state(4)
    attacker_id = state.current_player()
    attacking = next(g for g in state.groups.values() if g.owner == attacker_id
                     and legal_targets(state, g.id))
    defending_id = sorted(legal_targets(state, attacking.id))[0]
    with pytest.raises(Rejected) as excinfo:
        apply(state, DeclareAttack(attacking_group=attacking.id, defending_group=defending_id))
    assert excinfo.value.reason is RejectionReason.IMAGES_EXHAUSTED
```

- [ ] **Step 3: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_declare.py -v`
Expected: FAIL — `NotImplementedError: DeclareAttack`

- [ ] **Step 4: Реализовать ветку decide**

```python
        case DeclareAttack():
            return _declare_attack(state, command, ctx)
```

```python
def _require_running(state: MatchState) -> None:
    if state.status is not MatchStatus.RUNNING:
        raise Rejected(RejectionReason.WRONG_STATUS)


def _declare_attack(
    state: MatchState, command: DeclareAttack, ctx: DecisionContext
) -> tuple[Event, ...]:
    _require_running(state)
    if state.duel is not None:
        raise Rejected(RejectionReason.DUEL_IN_PROGRESS)
    if command.attacking_group not in state.groups or command.defending_group not in state.groups:
        raise Rejected(RejectionReason.UNKNOWN_GROUP)

    attacking = state.groups[command.attacking_group]
    defending = state.groups[command.defending_group]
    if attacking.owner != state.current_player():
        raise Rejected(RejectionReason.NOT_YOUR_TURN)
    if defending.owner == attacking.owner:
        raise Rejected(RejectionReason.TARGET_IS_YOURS)
    if command.defending_group not in legal_targets(state, command.attacking_group):
        raise Rejected(RejectionReason.NOT_ADJACENT)
    if not ctx.image_order:
        raise Rejected(RejectionReason.IMAGES_EXHAUSTED)

    budgets = Budgets.of(
        {
            attacking.owner: starting_budget_ms(attacking, state.settings),
            defending.owner: starting_budget_ms(defending, state.settings),
        }
    )
    return (
        AttackDeclared(
            attacker=attacking.owner,
            defender=defending.owner,
            attacking_group=attacking.id,
            defending_group=defending.id,
            category=defending.category,
            image_order=ctx.image_order,
            budgets=budgets,
        ),
    )
```

- [ ] **Step 5: Реализовать ветку evolve**

```python
        case AttackDeclared():
            defending = state.groups[event.defending_group]
            evolved = replace(
                state,
                groups={**state.groups, defending.id: replace(defending, revealed=True)},
                duel=Duel(
                    attacker=event.attacker,
                    defender=event.defender,
                    attacking_group=event.attacking_group,
                    defending_group=event.defending_group,
                    category=event.category,
                    image_order=event.image_order,
                    index=0,
                    answering=event.attacker,
                    budgets=event.budgets,
                    anchor=None,
                    phase=DuelPhase.DECLARED,
                ),
            )
```

- [ ] **Step 6: Убедиться, что тесты проходят и закоммитить**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy`
Expected: PASS

```bash
git add backend/src/podvinsya/domain backend/tests/domain
git commit -m "feat(domain): declare attack, reveal defender category, draw image order"
```

---

### Task 9: Старт дуэли, якорь, дедлайн

**Files:**
- Create: `backend/src/podvinsya/domain/timing.py`
- Modify: `backend/src/podvinsya/domain/decide.py`
- Modify: `backend/src/podvinsya/domain/evolve.py`
- Modify: `backend/tests/domain/conftest.py`
- Test: `backend/tests/domain/test_duel_start.py`

**Interfaces:**
- Consumes: `StartDuel`, `DuelStarted`.
- Produces: `timing.elapsed_ms(anchor: datetime | None, now: datetime, remaining_ms: int) -> int`; `timing.deadline_of(duel: Duel) -> datetime | None`; `timing.is_expired(duel: Duel, now: datetime) -> bool`; фикстуру `build_duel_state()` в `conftest.py` → `(state, players, attacking, defending)` с уже запущенными часами на `BASE_TIME`.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/domain/test_duel_start.py`:

```python
from datetime import timedelta

import pytest

from podvinsya.domain.actions import StartDuel
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.state import DuelPhase
from podvinsya.domain.timing import deadline_of, elapsed_ms, is_expired

from .conftest import BASE_TIME, apply, at, build_declared_state, build_duel_state


def test_starting_a_duel_sets_the_anchor_and_runs() -> None:
    state, _, _, _ = build_declared_state()
    state = apply(state, StartDuel(), now=BASE_TIME)
    duel = state.duel
    assert duel is not None
    assert duel.phase is DuelPhase.RUNNING
    assert duel.anchor == BASE_TIME
    assert duel.paused is False


def test_deadline_is_anchor_plus_the_answerer_remaining() -> None:
    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    expected = BASE_TIME + timedelta(milliseconds=duel.budgets.get(duel.answering))
    assert deadline_of(duel) == expected


def test_a_paused_duel_has_no_deadline() -> None:
    from dataclasses import replace

    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    assert deadline_of(replace(duel, anchor=None)) is None


def test_elapsed_is_clamped_at_both_ends() -> None:
    assert elapsed_ms(BASE_TIME, at(4.2), 60_000) == 4_200
    assert elapsed_ms(BASE_TIME, at(-5), 60_000) == 0, "a backwards clock must not add time"
    assert elapsed_ms(BASE_TIME, at(90), 60_000) == 60_000
    assert elapsed_ms(None, at(10), 60_000) == 0


def test_is_expired_only_once_the_budget_is_spent() -> None:
    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    budget_s = duel.budgets.get(duel.answering) / 1000
    assert is_expired(duel, at(budget_s - 0.001)) is False
    assert is_expired(duel, at(budget_s)) is True


def test_starting_a_duel_twice_is_rejected() -> None:
    state, _, _, _ = build_duel_state()
    with pytest.raises(Rejected) as excinfo:
        apply(state, StartDuel())
    assert excinfo.value.reason is RejectionReason.DUEL_NOT_DECLARED


def test_starting_a_duel_without_declaring_is_rejected() -> None:
    from .conftest import build_running_state

    state, _ = build_running_state(4)
    with pytest.raises(Rejected) as excinfo:
        apply(state, StartDuel())
    assert excinfo.value.reason is RejectionReason.NO_DUEL
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_duel_start.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'podvinsya.domain.timing'`

- [ ] **Step 3: Написать timing.py**

```python
from datetime import datetime, timedelta

from podvinsya.domain.state import Duel


def elapsed_ms(anchor: datetime | None, now: datetime, remaining_ms: int) -> int:
    """Time to charge the answering player, clamped into [0, remaining_ms].

    The lower bound guards against a clock stepping backwards: without it a
    negative difference would *give* the player time. The upper bound is its
    pair and never fires silently — reaching it means the timer already
    expired, and decide() resolves the duel instead of applying the command.
    """
    if anchor is None:
        return 0
    delta = int((now - anchor).total_seconds() * 1000)
    return max(0, min(delta, remaining_ms))


def deadline_of(duel: Duel) -> datetime | None:
    if duel.anchor is None:
        return None
    return duel.anchor + timedelta(milliseconds=duel.budgets.get(duel.answering))


def is_expired(duel: Duel, now: datetime) -> bool:
    deadline = deadline_of(duel)
    return deadline is not None and now >= deadline
```

- [ ] **Step 4: Реализовать ветки decide и evolve**

В `decide.py`:

```python
        case StartDuel():
            return _start_duel(state, ctx)
```

```python
def _require_duel(state: MatchState) -> Duel:
    _require_running(state)
    if state.duel is None:
        raise Rejected(RejectionReason.NO_DUEL)
    return state.duel


def _start_duel(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_duel(state)
    if duel.phase is not DuelPhase.DECLARED:
        raise Rejected(RejectionReason.DUEL_NOT_DECLARED)
    return (DuelStarted(anchor=ctx.now),)
```

В `evolve.py`:

```python
        case DuelStarted():
            duel = _duel(state)
            evolved = replace(
                state,
                duel=replace(duel, anchor=event.anchor, phase=DuelPhase.RUNNING),
            )
```

и вспомогательная функция в `evolve.py`:

```python
def _duel(state: MatchState) -> Duel:
    if state.duel is None:
        raise NotImplementedError("event requires an active duel")
    return state.duel
```

- [ ] **Step 5: Дописать построитель в conftest**

```python
from podvinsya.domain.actions import StartDuel


def build_duel_state() -> tuple[MatchState, tuple[PlayerId, ...], GroupId, GroupId]:
    state, players, attacking, defending = build_declared_state()
    return apply(state, StartDuel(), now=BASE_TIME), players, attacking, defending


@pytest.fixture
def duel_state() -> tuple[MatchState, tuple[PlayerId, ...], GroupId, GroupId]:
    return build_duel_state()
```

- [ ] **Step 6: Убедиться, что тесты проходят и закоммитить**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy`
Expected: PASS

```bash
git add backend/src/podvinsya/domain backend/tests/domain
git commit -m "feat(domain): start duel, anchor-based deadline, clamped elapsed time"
```

---

### Task 10: «Верно»

**Files:**
- Modify: `backend/src/podvinsya/domain/decide.py`
- Modify: `backend/src/podvinsya/domain/evolve.py`
- Test: `backend/tests/domain/test_judge_correct.py`

**Interfaces:**
- Consumes: `JudgeCorrect`, `AnswerAccepted`, `timing`.
- Produces: приватные хелперы `_charge(duel, now) -> tuple[Budgets, int]` и `_require_live_duel(state) -> Duel` в `decide.py`. Оба вызываются задачами 11 и 12, поэтому их имена и сигнатуры — контракт, а не локальный выбор.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/domain/test_judge_correct.py`:

```python
import pytest

from podvinsya.domain.actions import JudgeCorrect
from podvinsya.domain.errors import Rejected, RejectionReason

from .conftest import BASE_TIME, apply, at, build_declared_state, build_duel_state


def test_correct_answer_passes_the_turn_and_advances_the_image() -> None:
    state, _, _, _ = build_duel_state()
    before = state.duel
    assert before is not None

    state = apply(state, JudgeCorrect(), now=at(4.2))
    duel = state.duel
    assert duel is not None
    assert duel.answering == before.defender
    assert duel.index == 1
    assert duel.anchor == at(4.2)


def test_only_the_answering_player_is_charged() -> None:
    state, _, _, _ = build_duel_state()
    before = state.duel
    assert before is not None
    attacker_budget = before.budgets.get(before.attacker)
    defender_budget = before.budgets.get(before.defender)

    state = apply(state, JudgeCorrect(), now=at(4.2))
    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(before.attacker) == attacker_budget - 4_200
    assert duel.budgets.get(before.defender) == defender_budget


def test_the_clock_only_runs_for_whoever_is_answering() -> None:
    state, _, _, _ = build_duel_state()
    first = state.duel
    assert first is not None

    state = apply(state, JudgeCorrect(), now=at(10))
    state = apply(state, JudgeCorrect(), now=at(13))
    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(first.attacker) == first.budgets.get(first.attacker) - 10_000
    assert duel.budgets.get(first.defender) == first.budgets.get(first.defender) - 3_000
    assert duel.answering == first.attacker
    assert duel.index == 2


def test_judging_a_declared_but_unstarted_duel_is_rejected() -> None:
    state, _, _, _ = build_declared_state()
    with pytest.raises(Rejected) as excinfo:
        apply(state, JudgeCorrect(), now=BASE_TIME)
    assert excinfo.value.reason is RejectionReason.DUEL_NOT_RUNNING


def test_judging_without_a_duel_is_rejected() -> None:
    from .conftest import build_running_state

    state, _ = build_running_state(4)
    with pytest.raises(Rejected) as excinfo:
        apply(state, JudgeCorrect(), now=BASE_TIME)
    assert excinfo.value.reason is RejectionReason.NO_DUEL
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_judge_correct.py -v`
Expected: FAIL — `NotImplementedError: JudgeCorrect`

- [ ] **Step 3: Реализовать ветку decide**

```python
        case JudgeCorrect():
            return _judge_correct(state, ctx)
```

```python
def _require_live_duel(state: MatchState) -> Duel:
    duel = _require_duel(state)
    if duel.phase is not DuelPhase.RUNNING:
        raise Rejected(RejectionReason.DUEL_NOT_RUNNING)
    if duel.paused:
        raise Rejected(RejectionReason.DUEL_PAUSED)
    return duel


def _charge(duel: Duel, now: datetime) -> tuple[Budgets, int]:
    remaining = duel.budgets.get(duel.answering)
    charged = elapsed_ms(duel.anchor, now, remaining)
    return duel.budgets.charge(duel.answering, charged), charged


def _judge_correct(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_live_duel(state)
    budgets, charged = _charge(duel, ctx.now)
    if budgets.get(duel.answering) == 0:
        return _resolve(state, duel, loser=duel.answering)
    return (
        AnswerAccepted(
            player=duel.answering,
            image_index=duel.index,
            charged_ms=charged,
            next_answering=duel.opponent_of(duel.answering),
            anchor=ctx.now,
        ),
    )
```

`_resolve` реализуется задачей 11 — первой, где таймер реально доходит до нуля. Здесь она нужна только как объявление, чтобы `_judge_correct` типизировался; добавить в `decide.py` временную версию:

```python
def _resolve(state: MatchState, duel: Duel, loser: PlayerId) -> tuple[Event, ...]:
    raise NotImplementedError("duel resolution lands in task 11")
```

Ни один тест этой задачи её не вызывает: бюджеты в них заведомо больше списываемого времени.

- [ ] **Step 4: Реализовать ветку evolve**

```python
        case AnswerAccepted():
            duel = _duel(state)
            evolved = replace(
                state,
                duel=replace(
                    duel,
                    budgets=duel.budgets.charge(event.player, event.charged_ms),
                    answering=event.next_answering,
                    index=duel.index + 1,
                    anchor=event.anchor,
                ),
            )
```

- [ ] **Step 5: Убедиться, что тесты проходят и закоммитить**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy`
Expected: PASS

```bash
git add backend/src/podvinsya/domain backend/tests/domain
git commit -m "feat(domain): judge correct — charge answerer, pass turn, advance image"
```

---

### Task 11: «Пас» в ноль и исход дуэли

**Files:**
- Modify: `backend/src/podvinsya/domain/decide.py`
- Modify: `backend/src/podvinsya/domain/evolve.py`
- Test: `backend/tests/domain/test_judge_pass.py`

**Interfaces:**
- Consumes: `JudgePass`, `PassUsed`, `DuelResolved`, `_charge`, `next_turn`.
- Produces: `_resolve(state: MatchState, duel: Duel, loser: PlayerId) -> tuple[Event, ...]` в `decide.py` — заменяет заглушку из задачи 10. Задача 13 добавит к ней `ExpireTimer`, задача 14 — выбывание и победу.

Это первая задача, где таймер доходит до нуля, поэтому исход дуэли реализуется здесь.

Правило исхода симметрично, и тесты обязаны покрыть **оба** направления: и «нападающий выиграл», и «нападающий проиграл». В обоих случаях проверяется, что объединённая группа несёт категорию **нападающего** и принадлежит **победителю**. Покрыть одно направление и объявить правило проверенным — значит оставить половину его без охраны, потому что перепутанные владелец и тема выглядят одинаково правдоподобно.

Задача 13 разыгрывает те же два исхода через истечение таймера. Это не дублирование: сюда исход приходит через пас в ноль, туда — через `ExpireTimer`, и это два разных входа в `_resolve`.

Переход хода — второе центральное требование этой задачи, и его надо утверждать здесь же, а не откладывать. Он происходит внутри ветки `DuelResolved`, поэтому снимок `turn_index` и `round_no` берётся до разрешения, а после проверяется, что курсор сдвинулся ровно на одного живого игрока. Без этого утверждения выброшенный вызов `next_turn` не уронит ни одного теста. Правило слияния (спека §3.6): атакующая группа выживает как сущность, сохраняя `id`, `category` и `revealed`, поглощает клетки защитника и меняет владельца на победителя. Группа защитника исчезает, её категория уходит в `played_categories`.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/domain/test_judge_pass.py`:

```python
from dataclasses import replace

from podvinsya.domain.actions import JudgePass
from podvinsya.domain.budgets import Budgets

from .conftest import apply, at, build_duel_state


def test_pass_keeps_the_answerer_and_advances_the_image() -> None:
    state, _, _, _ = build_duel_state()
    before = state.duel
    assert before is not None

    state = apply(state, JudgePass(), now=at(2))
    duel = state.duel
    assert duel is not None
    assert duel.answering == before.answering
    assert duel.index == 1
    assert duel.anchor == at(2)


def test_pass_charges_elapsed_time_plus_the_penalty() -> None:
    state, _, _, _ = build_duel_state()
    before = state.duel
    assert before is not None
    start = before.budgets.get(before.answering)

    state = apply(state, JudgePass(), now=at(2))
    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(before.answering) == start - 2_000 - 3_000


def test_pass_that_zeroes_the_timer_loses_the_duel_immediately() -> None:
    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    loser = duel.answering
    thin = replace(duel, budgets=Budgets.of({duel.attacker: 1_000, duel.defender: 60_000}))
    state = replace(state, duel=thin)

    state = apply(state, JudgePass(), now=at(0.2))

    assert state.duel is None, "a duel that ended must be cleared"
    surviving = state.groups[thin.attacking_group]
    assert surviving.owner != loser


def test_pass_is_never_illegal_even_with_less_than_the_penalty_left() -> None:
    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    thin = replace(duel, budgets=Budgets.of({duel.attacker: 500, duel.defender: 60_000}))
    state = replace(state, duel=thin)

    state = apply(state, JudgePass(), now=at(0))

    assert state.duel is None
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_judge_pass.py -v`
Expected: FAIL — `NotImplementedError: JudgePass`

- [ ] **Step 3: Реализовать ветку decide**

```python
        case JudgePass():
            return _judge_pass(state, ctx)
```

```python
def _judge_pass(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_live_duel(state)
    budgets, charged = _charge(duel, ctx.now)
    penalty = state.settings.pass_penalty_ms
    after_penalty = budgets.charge(duel.answering, penalty)

    if after_penalty.get(duel.answering) == 0:
        pass_event = PassUsed(
            player=duel.answering,
            image_index=duel.index,
            charged_ms=charged,
            penalty_ms=penalty,
            anchor=None,
        )
        return (pass_event, *_resolve(state, duel, loser=duel.answering))

    return (
        PassUsed(
            player=duel.answering,
            image_index=duel.index,
            charged_ms=charged,
            penalty_ms=penalty,
            anchor=ctx.now,
        ),
    )
```

- [ ] **Step 4: Реализовать _resolve, заменив заглушку задачи 10**

```python
def _resolve(state: MatchState, duel: Duel, loser: PlayerId) -> tuple[Event, ...]:
    winner = duel.opponent_of(loser)
    attacking = state.groups[duel.attacking_group]
    defending = state.groups[duel.defending_group]
    return (
        DuelResolved(
            winner=winner,
            loser=loser,
            surviving_group=attacking.id,
            absorbed_group=defending.id,
            absorbed_cells=defending.cells,
            burned_category=defending.category,
        ),
    )
```

- [ ] **Step 5: Реализовать ветки evolve**

```python
        case PassUsed():
            duel = _duel(state)
            charged = duel.budgets.charge(event.player, event.charged_ms)
            evolved = replace(
                state,
                duel=replace(
                    duel,
                    budgets=charged.charge(event.player, event.penalty_ms),
                    index=duel.index + 1,
                    anchor=event.anchor,
                ),
            )
        case DuelResolved():
            surviving = state.groups[event.surviving_group]
            merged = replace(
                surviving,
                owner=event.winner,
                cells=surviving.cells | event.absorbed_cells,
            )
            groups = {
                gid: g for gid, g in state.groups.items() if gid != event.absorbed_group
            }
            groups[merged.id] = merged
            after = replace(
                state,
                groups=groups,
                played_categories=state.played_categories | {event.burned_category},
                duel=None,
            )
            turn_index, round_no = next_turn(after)
            evolved = replace(after, turn_index=turn_index, round_no=round_no)
```

Импортировать `next_turn` из `podvinsya.domain.rules` в `evolve.py`.

`Budgets.with_value` уже клампит в ноль, поэтому уход ниже нуля невозможен по построению.

- [ ] **Step 6: Убедиться, что тесты проходят и закоммитить**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy`
Expected: PASS

```bash
git add backend/src/podvinsya/domain backend/tests/domain
git commit -m "feat(domain): judge pass with penalty, immediate loss at zero, duel resolution and group merge"
```

---

### Task 12: Пауза и снятие с паузы

**Files:**
- Modify: `backend/src/podvinsya/domain/decide.py`
- Modify: `backend/src/podvinsya/domain/evolve.py`
- Test: `backend/tests/domain/test_pause.py`

**Interfaces:**
- Consumes: `PauseDuel`, `ResumeDuel`, `DuelPaused`, `DuelResumed`.
- Produces: ничего нового наружу.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/domain/test_pause.py`:

```python
import pytest

from podvinsya.domain.actions import JudgeCorrect, PauseDuel, ResumeDuel
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.timing import deadline_of

from .conftest import apply, at, build_duel_state


def test_pause_freezes_the_clock_and_drops_the_deadline() -> None:
    state, _, _, _ = build_duel_state()
    before = state.duel
    assert before is not None
    start = before.budgets.get(before.answering)

    state = apply(state, PauseDuel(), now=at(5))
    duel = state.duel
    assert duel is not None
    assert duel.paused is True
    assert duel.anchor is None
    assert deadline_of(duel) is None
    assert duel.budgets.get(before.answering) == start - 5_000


def test_time_spent_paused_is_never_charged() -> None:
    state, _, _, _ = build_duel_state()
    before = state.duel
    assert before is not None
    start = before.budgets.get(before.answering)

    state = apply(state, PauseDuel(), now=at(5))
    state = apply(state, ResumeDuel(), now=at(305))
    state = apply(state, JudgeCorrect(), now=at(307))

    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(before.answering) == start - 7_000, (
        "five minutes of pause must cost nothing"
    )


def test_resume_reanchors_to_now() -> None:
    state, _, _, _ = build_duel_state()
    state = apply(state, PauseDuel(), now=at(5))
    state = apply(state, ResumeDuel(), now=at(60))
    duel = state.duel
    assert duel is not None
    assert duel.anchor == at(60)
    assert duel.paused is False


def test_judging_while_paused_is_rejected() -> None:
    state, _, _, _ = build_duel_state()
    state = apply(state, PauseDuel(), now=at(5))
    with pytest.raises(Rejected) as excinfo:
        apply(state, JudgeCorrect(), now=at(6))
    assert excinfo.value.reason is RejectionReason.DUEL_PAUSED


def test_pausing_twice_is_rejected() -> None:
    state, _, _, _ = build_duel_state()
    state = apply(state, PauseDuel(), now=at(5))
    with pytest.raises(Rejected) as excinfo:
        apply(state, PauseDuel(), now=at(6))
    assert excinfo.value.reason is RejectionReason.DUEL_PAUSED


def test_resuming_a_running_duel_is_rejected() -> None:
    state, _, _, _ = build_duel_state()
    with pytest.raises(Rejected) as excinfo:
        apply(state, ResumeDuel(), now=at(5))
    assert excinfo.value.reason is RejectionReason.DUEL_NOT_PAUSED
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_pause.py -v`
Expected: FAIL — `NotImplementedError: PauseDuel`

- [ ] **Step 3: Реализовать ветки decide**

```python
        case PauseDuel():
            return _pause_duel(state, ctx)
        case ResumeDuel():
            return _resume_duel(state, ctx)
```

```python
def _pause_duel(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_live_duel(state)
    _, charged = _charge(duel, ctx.now)
    return (DuelPaused(charged_ms=charged),)


def _resume_duel(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_duel(state)
    if duel.phase is not DuelPhase.RUNNING:
        raise Rejected(RejectionReason.DUEL_NOT_RUNNING)
    if not duel.paused:
        raise Rejected(RejectionReason.DUEL_NOT_PAUSED)
    return (DuelResumed(anchor=ctx.now),)
```

- [ ] **Step 4: Реализовать ветки evolve**

```python
        case DuelPaused():
            duel = _duel(state)
            evolved = replace(
                state,
                duel=replace(
                    duel,
                    budgets=duel.budgets.charge(duel.answering, event.charged_ms),
                    anchor=None,
                ),
            )
        case DuelResumed():
            duel = _duel(state)
            evolved = replace(state, duel=replace(duel, anchor=event.anchor))
```

- [ ] **Step 5: Убедиться, что тесты проходят и закоммитить**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy`
Expected: PASS

```bash
git add backend/src/podvinsya/domain backend/tests/domain
git commit -m "feat(domain): pause and resume, paused time is never charged"
```

---

### Task 13: Истечение таймера и авторитет часов

**Files:**
- Modify: `backend/src/podvinsya/domain/decide.py`
- Test: `backend/tests/domain/test_resolution.py`

**Interfaces:**
- Consumes: `ExpireTimer`, `_resolve` и ветка `DuelResolved` в `evolve` — обе уже реализованы задачей 11.
- Produces: ничего нового наружу.

Здесь добавляется вход в исход дуэли по истечению таймера и правило спеки §4.2: часы авторитетны над опоздавшей командой. Само слияние групп реализовано задачей 11; эти тесты проверяют его на обоих исходах.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/domain/test_resolution.py`:

```python
from dataclasses import replace

from podvinsya.domain.actions import ExpireTimer, JudgeCorrect, JudgePass, PauseDuel
from podvinsya.domain.board import is_connected
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.events import DuelResolved, PassUsed

from .conftest import apply, at, build_declared_state, build_duel_state


def _force_loss(state, loser):  # type: ignore[no-untyped-def]
    """Wind the answering player's clock down so the next ExpireTimer resolves."""
    duel = state.duel
    assert duel is not None
    aimed = replace(duel, answering=loser, budgets=duel.budgets.with_value(loser, 1_000))
    return replace(state, duel=aimed)


def test_attacker_wins_and_takes_the_defending_group() -> None:
    state, _, attacking, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    attacker, defender = duel.attacker, duel.defender
    kept_category = state.groups[attacking].category
    burned_category = state.groups[defending].category
    cells_before = state.groups[attacking].cells | state.groups[defending].cells

    state = apply(_force_loss(state, defender), ExpireTimer(deadline_id=0), now=at(1))

    assert state.duel is None
    assert defending not in state.groups
    merged = state.groups[attacking]
    assert merged.owner == attacker
    assert merged.cells == cells_before
    assert merged.category == kept_category
    assert burned_category in state.played_categories


def test_attacker_loses_and_the_defender_takes_the_attacking_group() -> None:
    state, _, attacking, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    attacker, defender = duel.attacker, duel.defender
    kept_category = state.groups[attacking].category
    cells_before = state.groups[attacking].cells | state.groups[defending].cells

    state = apply(_force_loss(state, attacker), ExpireTimer(deadline_id=0), now=at(1))

    merged = state.groups[attacking]
    assert merged.owner == defender, "the winner takes both groups"
    assert merged.cells == cells_before
    assert merged.category == kept_category, "the attacker category survives regardless of who won"


def test_the_merged_group_keeps_the_attacker_revealed_flag() -> None:
    state, _, attacking, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    before = state.groups[attacking].revealed
    state = apply(_force_loss(state, duel.defender), ExpireTimer(deadline_id=0), now=at(1))
    assert state.groups[attacking].revealed is before


def test_the_merged_group_is_connected() -> None:
    state, _, attacking, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    state = apply(_force_loss(state, duel.defender), ExpireTimer(deadline_id=0), now=at(1))
    assert is_connected(state.groups[attacking].cells)


def test_every_duel_costs_exactly_one_group_and_one_category() -> None:
    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    groups_before = len(state.groups)
    played_before = len(state.played_categories)

    state = apply(_force_loss(state, duel.defender), ExpireTimer(deadline_id=0), now=at(1))

    assert len(state.groups) == groups_before - 1
    assert len(state.played_categories) == played_before + 1


def test_turn_advances_after_a_duel() -> None:
    state, players, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    before = state.current_player()
    state = apply(_force_loss(state, duel.defender), ExpireTimer(deadline_id=0), now=at(1))
    assert state.current_player() != before


def test_a_judging_command_arriving_after_the_deadline_resolves_as_expiry() -> None:
    state, _, attacking, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    late = duel.budgets.get(duel.answering) / 1000 + 5

    state = apply(state, JudgeCorrect(), now=at(late))

    assert state.duel is None, "the clock is authoritative over a late command"
    assert state.groups[attacking].owner == duel.defender


def test_expire_timer_without_a_duel_is_ignored() -> None:
    from .conftest import build_running_state
    from podvinsya.domain.context import DecisionContext
    from podvinsya.domain.decide import decide
    from .conftest import BASE_TIME

    state, _ = build_running_state(4)
    assert decide(state, ExpireTimer(deadline_id=7), DecisionContext(now=BASE_TIME)) == ()


def test_a_late_pass_resolves_without_recording_the_pass() -> None:
    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    late = duel.budgets.get(duel.answering) / 1000 + 5

    events = decide(state, JudgePass(), DecisionContext(now=at(late)))

    # Without the expiry check in _judge_pass the duel still resolves, because the
    # clamped charge zeroes the budget and the penalty keeps it there. What differs is
    # the log: a PassUsed would be recorded for a pass played after the player had
    # already lost. The log is the source of truth and undo walks it, so that entry
    # must not exist.
    assert not any(isinstance(e, PassUsed) for e in events), (
        "a pass arriving after the deadline must not enter the log as a played pass"
    )

    # Who loses matters as much as the absence of the phantom pass. This expiry branch
    # inside _judge_pass is new code, and a swapped loser here would be invisible to a
    # bare isinstance check while handing the duel to the wrong player.
    resolved = next(e for e in events if isinstance(e, DuelResolved))
    assert resolved.loser == duel.answering
    assert resolved.winner == duel.opponent_of(duel.answering)


def test_expire_timer_before_the_deadline_is_ignored() -> None:
    state, _, _, _ = build_duel_state()
    assert decide(state, ExpireTimer(deadline_id=0), DecisionContext(now=at(1))) == ()


def test_expire_timer_on_a_paused_duel_is_ignored() -> None:
    state, _, _, _ = build_duel_state()
    state = apply(state, PauseDuel(), now=at(5))

    # The runtime cancels a deadline task on pause, but a task that already fired can
    # still arrive. Losing a duel to a stale timer while the host has the game frozen
    # is the worst failure this system could have on stage.
    assert decide(state, ExpireTimer(deadline_id=0), DecisionContext(now=at(600))) == ()


def test_expire_timer_on_a_declared_duel_is_ignored() -> None:
    state, _, _, _ = build_declared_state()
    assert decide(state, ExpireTimer(deadline_id=0), DecisionContext(now=at(600))) == ()
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_resolution.py -v`
Expected: FAIL — `NotImplementedError: ExpireTimer`

- [ ] **Step 3: Реализовать ветку ExpireTimer**

Добавить в `match` в `decide.py`:

```python
        case ExpireTimer():
            return _expire_timer(state, ctx)
```

```python
def _expire_timer(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    if state.duel is None:
        return ()
    duel = state.duel
    if duel.phase is not DuelPhase.RUNNING or duel.paused:
        return ()
    if not is_expired(duel, ctx.now):
        return ()
    return _resolve(state, duel, loser=duel.answering)
```

`ExpireTimer` возвращает пустой кортеж вместо отказа: устаревшее срабатывание таймера — штатная ситуация, а не ошибка ведущего.

В `_judge_correct` и `_judge_pass` добавить проверку авторитета часов **перед** списанием:

```python
    if is_expired(duel, ctx.now):
        return _resolve(state, duel, loser=duel.answering)
```

Проверка стоит **до** списания: опоздавшее «Верно» не воскрешает проигранную дуэль, и исход не зависит от того, кто выиграл гонку — `ExpireTimer` или судейская команда.

- [ ] **Step 4: Убедиться, что тесты проходят и закоммитить**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy`
Expected: PASS

```bash
git add backend/src/podvinsya/domain backend/tests/domain
git commit -m "feat(domain): expire timer, clock authority over late judging commands"
```

---

### Task 14: Выбывание и победа

**Files:**
- Modify: `backend/src/podvinsya/domain/decide.py`
- Modify: `backend/src/podvinsya/domain/evolve.py`
- Test: `backend/tests/domain/test_elimination.py`

**Interfaces:**
- Consumes: `PlayerEliminated`, `MatchWon`, `_resolve`.
- Produces: ничего нового наружу. `_resolve` теперь дописывает к `DuelResolved` события выбывания и победы.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/domain/test_elimination.py`:

```python
from dataclasses import replace

from podvinsya.domain.actions import ExpireTimer
from podvinsya.domain.state import MatchStatus

from .conftest import apply, at, build_duel_state


def _leave_only(state, player, keep_group_id):  # type: ignore[no-untyped-def]
    """Strip a player down to a single group so the next loss eliminates them."""
    groups = {
        gid: g for gid, g in state.groups.items()
        if g.owner != player or gid == keep_group_id
    }
    other = next(g.owner for g in groups.values() if g.owner != player)
    for gid, g in list(state.groups.items()):
        if g.owner == player and gid != keep_group_id:
            groups[gid] = replace(g, owner=other)
    return replace(state, groups=groups)


def test_losing_your_last_group_eliminates_you() -> None:
    state, _, attacking, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    state = _leave_only(state, duel.defender, defending)
    aimed = replace(duel, answering=duel.defender,
                    budgets=duel.budgets.with_value(duel.defender, 1_000))
    state = replace(state, duel=aimed)

    state = apply(state, ExpireTimer(deadline_id=0), now=at(2))

    assert state.player(duel.defender).eliminated is True
    assert state.groups_of(duel.defender) == ()


def test_a_player_with_groups_left_is_not_eliminated() -> None:
    state, _, _, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    aimed = replace(duel, answering=duel.defender,
                    budgets=duel.budgets.with_value(duel.defender, 1_000))
    state = apply(replace(state, duel=aimed), ExpireTimer(deadline_id=0), now=at(2))
    assert state.player(duel.defender).eliminated is False


def test_the_last_player_standing_wins_and_the_match_finishes() -> None:
    state, players, attacking, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    bystanders = [p for p in players if p not in (duel.attacker, duel.defender)]

    groups = dict(state.groups)
    for gid, group in list(groups.items()):
        if group.owner in bystanders:
            groups[gid] = replace(group, owner=duel.attacker)
    players_tuple = tuple(
        replace(p, eliminated=p.id in bystanders) for p in state.players
    )
    state = replace(state, groups=groups, players=players_tuple)
    state = _leave_only(state, duel.defender, defending)

    aimed = replace(duel, answering=duel.defender,
                    budgets=duel.budgets.with_value(duel.defender, 1_000))
    state = apply(replace(state, duel=aimed), ExpireTimer(deadline_id=0), now=at(2))

    assert state.status is MatchStatus.FINISHED
    assert state.winner == duel.attacker
    assert {g.owner for g in state.groups.values()} == {duel.attacker}


def test_a_finished_match_refuses_further_attacks() -> None:
    import pytest

    from podvinsya.domain.actions import DeclareAttack
    from podvinsya.domain.errors import Rejected, RejectionReason

    state, players, attacking, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    bystanders = [p for p in players if p not in (duel.attacker, duel.defender)]
    groups = {
        gid: (replace(g, owner=duel.attacker) if g.owner in bystanders else g)
        for gid, g in state.groups.items()
    }
    players_tuple = tuple(replace(p, eliminated=p.id in bystanders) for p in state.players)
    state = _leave_only(
        replace(state, groups=groups, players=players_tuple), duel.defender, defending
    )
    aimed = replace(duel, answering=duel.defender,
                    budgets=duel.budgets.with_value(duel.defender, 1_000))
    state = apply(replace(state, duel=aimed), ExpireTimer(deadline_id=0), now=at(2))

    with pytest.raises(Rejected) as excinfo:
        apply(state, DeclareAttack(attacking_group=attacking, defending_group=attacking))
    assert excinfo.value.reason is RejectionReason.WRONG_STATUS
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_elimination.py -v`
Expected: FAIL — `assert state.player(...).eliminated is True` не выполняется

- [ ] **Step 3: Дописать _resolve**

```python
def _resolve(state: MatchState, duel: Duel, loser: PlayerId) -> tuple[Event, ...]:
    winner = duel.opponent_of(loser)
    attacking = state.groups[duel.attacking_group]
    defending = state.groups[duel.defending_group]
    resolved = DuelResolved(
        winner=winner,
        loser=loser,
        surviving_group=attacking.id,
        absorbed_group=defending.id,
        absorbed_cells=defending.cells,
        burned_category=defending.category,
    )

    loser_groups_left = sum(
        1
        for gid, group in state.groups.items()
        if group.owner == loser and gid not in (attacking.id, defending.id)
    )
    if loser_groups_left > 0:
        return (resolved,)

    eliminated = PlayerEliminated(player_id=loser)
    survivors = [p.id for p in state.players if not p.eliminated and p.id != loser]
    if len(survivors) == 1:
        return (resolved, eliminated, MatchWon(player_id=survivors[0]))
    return (resolved, eliminated)
```

Хитрость в подсчёте: после слияния обе группы дуэли принадлежат победителю, поэтому обе исключаются из подсчёта оставшихся групп проигравшего.

- [ ] **Step 4: Реализовать ветки evolve**

Порядок событий в пачке важен. `DuelResolved` продвигает ход **до** того, как `PlayerEliminated` пометит выбывшего, поэтому курсор может остаться стоять на игроке, который только что вылетел. Ветка `PlayerEliminated` обязана это починить, иначе партия зависнет на ходе выбывшего.

```python
        case PlayerEliminated():
            marked = replace(
                state,
                players=tuple(
                    replace(p, eliminated=True) if p.id == event.player_id else p
                    for p in state.players
                ),
            )
            if marked.player(marked.current_player()).eliminated:
                turn_index, round_no = next_turn(marked)
                evolved = replace(marked, turn_index=turn_index, round_no=round_no)
            else:
                evolved = marked
        case MatchWon():
            evolved = replace(
                state,
                status=MatchStatus.FINISHED,
                winner=event.player_id,
            )
```

- [ ] **Step 5: Убедиться, что тесты проходят и закоммитить**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy`
Expected: PASS

```bash
git add backend/src/podvinsya/domain backend/tests/domain
git commit -m "feat(domain): elimination on losing the last group, victory for the last player standing"
```

---

### Task 15: Отмена судейского решения

**Files:**
- Modify: `backend/src/podvinsya/domain/decide.py`
- Modify: `backend/src/podvinsya/domain/evolve.py`
- Modify: `backend/src/podvinsya/domain/context.py`
- Test: `backend/tests/domain/test_undo.py`

**Interfaces:**
- Consumes: `UndoLastJudgement`, `JudgementUndone`.
- Produces: расширение `DecisionContext` полем `duel_journal: tuple[JournalEntry, ...] = ()`; `JournalEntry(seq: int, budgets: Budgets, answering: PlayerId, image_index: int)` в `context.py` — снимок состояния дуэли **перед** каждым судейским событием, который поставляет рантайм.

Отмена реализуется компенсирующим событием, несущим полные восстановленные значения. `evolve` по нему делает присваивание без арифметики, поэтому цепочка отмен не накапливает ошибку. Домен не читает лог сам: снимки приходят значением в контексте, как и всё остальное недетерминированное.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/domain/test_undo.py`:

```python
import pytest

from podvinsya.domain.actions import JudgeCorrect, JudgePass, UndoLastJudgement
from podvinsya.domain.context import JournalEntry
from podvinsya.domain.errors import Rejected, RejectionReason

from .conftest import apply, at, build_duel_state


def _snapshot(state, seq: int) -> JournalEntry:  # type: ignore[no-untyped-def]
    duel = state.duel
    assert duel is not None
    return JournalEntry(
        seq=seq,
        budgets=duel.budgets,
        answering=duel.answering,
        image_index=duel.index,
    )


def test_undo_restores_budgets_answerer_and_image() -> None:
    state, _, _, _ = build_duel_state()
    snapshot = _snapshot(state, seq=state.seq)
    before = state.duel
    assert before is not None

    state = apply(state, JudgeCorrect(), now=at(4.2))
    state = apply(state, UndoLastJudgement(), now=at(6), duel_journal=(snapshot,))

    duel = state.duel
    assert duel is not None
    assert duel.answering == before.answering
    assert duel.index == before.index
    assert duel.budgets == before.budgets


def test_time_between_the_mistake_and_the_undo_is_not_charged() -> None:
    state, _, _, _ = build_duel_state()
    snapshot = _snapshot(state, seq=state.seq)
    before = state.duel
    assert before is not None
    start = before.budgets.get(before.answering)

    state = apply(state, JudgeCorrect(), now=at(4.2))
    state = apply(state, UndoLastJudgement(), now=at(30), duel_journal=(snapshot,))

    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(before.answering) == start, (
        "the operator fixing their own mistake must not cost the player"
    )


def test_undo_reanchors_so_the_clock_restarts_from_now() -> None:
    state, _, _, _ = build_duel_state()
    snapshot = _snapshot(state, seq=state.seq)
    state = apply(state, JudgeCorrect(), now=at(4.2))
    state = apply(state, UndoLastJudgement(), now=at(30), duel_journal=(snapshot,))
    duel = state.duel
    assert duel is not None
    assert duel.anchor == at(30)


def test_undo_walks_back_a_chain() -> None:
    state, _, _, _ = build_duel_state()
    first = _snapshot(state, seq=state.seq)
    original = state.duel
    assert original is not None

    state = apply(state, JudgeCorrect(), now=at(3))
    second = _snapshot(state, seq=state.seq)
    state = apply(state, JudgePass(), now=at(6))

    state = apply(state, UndoLastJudgement(), now=at(7), duel_journal=(first, second))
    duel = state.duel
    assert duel is not None
    assert duel.index == 1

    state = apply(state, UndoLastJudgement(), now=at(8), duel_journal=(first,))
    duel = state.duel
    assert duel is not None
    assert duel.index == 0
    assert duel.budgets == original.budgets


def test_undo_with_nothing_to_undo_is_rejected() -> None:
    state, _, _, _ = build_duel_state()
    with pytest.raises(Rejected) as excinfo:
        apply(state, UndoLastJudgement(), now=at(3), duel_journal=())
    assert excinfo.value.reason is RejectionReason.NOTHING_TO_UNDO


def test_undo_without_a_duel_is_rejected() -> None:
    from .conftest import build_running_state

    state, _ = build_running_state(4)
    with pytest.raises(Rejected) as excinfo:
        apply(state, UndoLastJudgement(), now=at(3))
    assert excinfo.value.reason is RejectionReason.NO_DUEL
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_undo.py -v`
Expected: FAIL — `ImportError: cannot import name 'JournalEntry'`

- [ ] **Step 3: Расширить контекст**

В `context.py`:

```python
from podvinsya.domain.budgets import Budgets


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """State of the duel immediately before one judging event.

    The runtime derives these from the event log and hands them in; the domain
    never reads the log itself.
    """

    seq: int
    budgets: Budgets
    answering: PlayerId
    image_index: int
```

и добавить в `DecisionContext`:

```python
    duel_journal: tuple[JournalEntry, ...] = ()
```

- [ ] **Step 4: Реализовать decide и evolve**

В `decide.py`:

```python
        case UndoLastJudgement():
            return _undo(state, ctx)
```

```python
def _undo(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_duel(state)
    if duel.phase is not DuelPhase.RUNNING:
        raise Rejected(RejectionReason.DUEL_NOT_RUNNING)
    if not ctx.duel_journal:
        raise Rejected(RejectionReason.NOTHING_TO_UNDO)
    entry = ctx.duel_journal[-1]
    return (
        JudgementUndone(
            undone_seq=entry.seq,
            budgets=entry.budgets,
            answering=entry.answering,
            image_index=entry.image_index,
            anchor=ctx.now,
        ),
    )
```

В `evolve.py`:

```python
        case JudgementUndone():
            duel = _duel(state)
            evolved = replace(
                state,
                duel=replace(
                    duel,
                    budgets=event.budgets,
                    answering=event.answering,
                    index=event.image_index,
                    anchor=event.anchor,
                ),
            )
```

Ни одного арифметического действия: восстановление — присваивание записанных значений.

- [ ] **Step 5: Убедиться, что тесты проходят и закоммитить**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy`
Expected: PASS

```bash
git add backend/src/podvinsya/domain backend/tests/domain
git commit -m "feat(domain): undo the last judgement via a compensating event"
```

---

### Task 16: Инварианты, завершаемость, чистота

**Files:**
- Create: `backend/src/podvinsya/domain/__init__.py` — переписать как публичный фасад
- Test: `backend/tests/domain/test_invariants.py`
- Test: `backend/tests/domain/test_purity.py`

**Interfaces:**
- Consumes: весь домен.
- Produces: `podvinsya.domain.check_invariants(state: MatchState) -> None` — бросает `AssertionError` с внятным сообщением; фасадные реэкспорты `decide`, `evolve`, `fold`, `create_initial_state`, `Rejected`, `RejectionReason`.

Это задача, ради которой существовал весь план: три инварианта спеки §2.8 и вытекающая из них завершаемость проверяются не примерами, а на случайных легальных партиях.

- [ ] **Step 1: Написать падающий тест инвариантов**

`backend/tests/domain/test_invariants.py`:

```python
import random

import pytest

from podvinsya.domain import check_invariants
from podvinsya.domain.actions import DeclareAttack, JudgeCorrect, StartDuel
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.evolve import fold
from podvinsya.domain.rules import legal_targets
from podvinsya.domain.state import MatchState, MatchStatus

from .conftest import IMAGE_POOL, BASE_TIME, at, build_running_state


def _play_one_duel(state: MatchState, rng: random.Random, clock: float) -> tuple[MatchState, float]:
    attacker = state.current_player()
    options = [
        (group.id, target)
        for group in state.groups.values()
        if group.owner == attacker
        for target in sorted(legal_targets(state, group.id))
    ]
    assert options, "a player who does not own the whole board always has a legal target"
    attacking, defending = rng.choice(options)

    ctx = DecisionContext(now=at(clock), image_order=IMAGE_POOL)
    state = fold(state, decide(state, DeclareAttack(attacking, defending), ctx))
    check_invariants(state)

    state = fold(state, decide(state, StartDuel(), DecisionContext(now=at(clock))))
    duel = state.duel
    assert duel is not None

    if rng.random() < 0.5:
        # A cheap correct answer hands the turn to the defender, so it is the
        # defender who then runs out and the attacker who wins. Without this
        # branch the attacker would lose every single duel and the test would
        # only ever exercise one of the two outcomes.
        clock += 1
        state = fold(state, decide(state, JudgeCorrect(), DecisionContext(now=at(clock))))
        check_invariants(state)
        duel = state.duel
        assert duel is not None

    # Hand the clock straight past the answering side's remaining budget.
    clock += duel.budgets.get(duel.answering) / 1000 + 1
    state = fold(state, decide(state, JudgeCorrect(), DecisionContext(now=at(clock))))
    check_invariants(state)
    return state, clock + 1


@pytest.mark.parametrize("seed", range(25))
def test_invariants_hold_across_a_whole_random_match(seed: int) -> None:
    rng = random.Random(seed)
    state, _ = build_running_state(4)
    check_invariants(state)

    clock = 0.0
    duels = 0
    while state.status is MatchStatus.RUNNING:
        state, clock = _play_one_duel(state, rng, clock)
        duels += 1
        assert duels <= state.board.cell_count, "a match must not outrun its category supply"

    assert state.status is MatchStatus.FINISHED
    assert state.winner is not None


@pytest.mark.parametrize("seed", range(25))
def test_a_match_always_terminates_within_cells_minus_one_duels(seed: int) -> None:
    rng = random.Random(seed)
    state, _ = build_running_state(4)
    limit = state.board.cell_count - 1

    clock = 0.0
    duels = 0
    while state.status is MatchStatus.RUNNING:
        state, clock = _play_one_duel(state, rng, clock)
        duels += 1

    assert duels <= limit
    assert state.winner is not None
    assert {g.owner for g in state.groups.values()} == {state.winner}


@pytest.mark.parametrize("player_count", [2, 3, 4])
def test_every_supported_player_count_terminates(player_count: int) -> None:
    rng = random.Random(player_count)
    state, _ = build_running_state(player_count)
    clock = 0.0
    while state.status is MatchStatus.RUNNING:
        state, clock = _play_one_duel(state, rng, clock)
    assert state.winner is not None
    assert len(state.groups) + len(state.played_categories) == state.board.cell_count


def test_groups_and_unplayed_categories_fall_in_lockstep() -> None:
    rng = random.Random(99)
    state, _ = build_running_state(4)
    total = state.board.cell_count

    clock = 0.0
    while state.status is MatchStatus.RUNNING:
        played = len(state.played_categories)
        assert len(state.groups) + played == total
        state, clock = _play_one_duel(state, rng, clock)
    assert len(state.groups) + len(state.played_categories) == total
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && python -m pytest tests/domain/test_invariants.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_invariants'`

- [ ] **Step 3: Написать фасад с check_invariants**

`backend/src/podvinsya/domain/__init__.py`:

```python
from podvinsya.domain.board import is_connected
from podvinsya.domain.decide import decide
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.evolve import evolve, fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.state import MatchState, MatchStatus

__all__ = [
    "Rejected",
    "RejectionReason",
    "check_invariants",
    "create_initial_state",
    "decide",
    "evolve",
    "fold",
]


def check_invariants(state: MatchState) -> None:
    """The three invariants of spec §2.8. Cheap enough to assert after every event."""
    if state.status is MatchStatus.SETUP and not state.groups:
        return

    covered = [cell for group in state.groups.values() for cell in group.cells]
    expected = state.board.cells()
    assert len(covered) == len(expected), (
        f"partition broken: {len(covered)} cells covered, board has {len(expected)}"
    )
    assert set(covered) == set(expected), "partition broken: cells overlap or are missing"

    for group in state.groups.values():
        assert is_connected(group.cells), f"group {group.id} is not orthogonally connected"

    categories = [group.category for group in state.groups.values()]
    assert len(set(categories)) == len(categories), "two groups share a category"
    assert set(categories).isdisjoint(state.played_categories), (
        "a played category is still on the board"
    )
    assert len(state.groups) + len(state.played_categories) == state.board.cell_count, (
        "groups and unplayed categories no longer fall in lockstep"
    )
```

- [ ] **Step 4: Написать тест чистоты**

`backend/tests/domain/test_purity.py`:

```python
import ast
import pathlib

import pytest

DOMAIN = pathlib.Path(__file__).resolve().parents[2] / "src" / "podvinsya" / "domain"
FORBIDDEN_MODULES = {
    "asyncio", "random", "secrets", "time", "os", "socket", "pathlib",
    "sqlalchemy", "fastapi", "httpx", "requests",
}


def _module_files() -> list[pathlib.Path]:
    return sorted(DOMAIN.glob("*.py"))


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_domain_imports_no_io_or_randomness(path: pathlib.Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & FORBIDDEN_MODULES), (
        f"{path.name} imports {sorted(imported & FORBIDDEN_MODULES)}"
    )


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_domain_never_reads_a_clock(path: pathlib.Path) -> None:
    source = path.read_text(encoding="utf-8")
    assert "datetime.now" not in source, f"{path.name} reads a clock; inject ctx.now instead"
    assert "utcnow" not in source, f"{path.name} reads a clock; inject ctx.now instead"


def test_decide_is_deterministic_for_the_same_inputs() -> None:
    from podvinsya.domain.actions import DeclareAttack
    from podvinsya.domain.context import DecisionContext
    from podvinsya.domain.decide import decide
    from podvinsya.domain.rules import legal_targets

    from .conftest import IMAGE_POOL, BASE_TIME, build_running_state

    state, _ = build_running_state(4)
    attacker = state.current_player()
    attacking = next(
        g for g in state.groups.values() if g.owner == attacker and legal_targets(state, g.id)
    )
    defending = sorted(legal_targets(state, attacking.id))[0]
    command = DeclareAttack(attacking_group=attacking.id, defending_group=defending)
    ctx = DecisionContext(now=BASE_TIME, image_order=IMAGE_POOL)

    assert decide(state, command, ctx) == decide(state, command, ctx)
```

- [ ] **Step 5: Убедиться, что всё проходит**

Run: `cd backend && python -m pytest tests/domain -v && python -m mypy && python -m ruff check src tests`
Expected: PASS, mypy и ruff чисто.

- [ ] **Step 6: Коммит**

```bash
git add backend/src/podvinsya/domain/__init__.py backend/tests/domain
git commit -m "test(domain): invariants, termination bound, purity guards"
```

---

## Что этот план намеренно не делает

Ничего из перечисленного не входит в доменное ядро и получит собственные планы:

- персистентность, кодек событий, миграции;
- рантайм, очередь команд, планировщик дедлайнов, политика отказов, восстановление с автопаузой;
- REST, WebSocket, проекции на двух зрителей, аутентификация;
- генерация контрактов;
- библиотека контента, медиа, отбор категорий на партию;
- экран сцены и пульт ведущего;
- compose, Caddy, бэкапы.

Домен после этого плана — самостоятельная библиотека без зависимостей, которую можно прогнать на случайной партии и получить победителя.
