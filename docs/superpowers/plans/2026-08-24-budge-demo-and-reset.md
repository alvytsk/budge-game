# Демо-партия и сброс — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** довести систему до состояния, в котором механику можно проверить одной командой: сброс партии в домене, честный вердикт готовности библиотеки, и `budge seed-demo`, собирающий играбельную партию с нуля.

**Architecture:** сброс — новое доменное событие `MatchReset(keep_roster)` в append-only логе; `evolve` возвращает состояние к полям генезиса, рантайм не меняется вовсе (планировщик взводится от состояния). Демо ходит по настоящему HTTP API, чеканя себе сессионную куку тем же `mint_session`, что и роут логина, — так не приходится дублировать граф сервисов из `api/app.py`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Pydantic 2, pytest; React 19, TanStack Query/Router, vitest, msw, Tailwind 4.

**Spec:** `docs/superpowers/specs/2026-08-24-budge-demo-and-reset-design.md` (аддендум к `2026-08-22-budge-design.md`)

## Global Constraints

- **Язык интерфейса — русский.** Любая строка, которую видит оператор, — по-русски. Комментарии и docstring'и в коде — по-английски, как во всём репозитории.
- **Библиотеку пишет ровно один модуль** — `src/budge/library/catalogue.py`. Тест `tests/library/test_write_paths.py::test_no_write_outside_the_catalogue` парсит каждый модуль под `src/budge/` и падает на любом `session.add(Category(...))` / `update(Image)` вне него. Демо пишет библиотеку только через HTTP-роуты.
- **Wire-имена событий — замороженный литерал.** Новое событие добавляется в `WIRE_NAMES` в `src/budge/db/codec/registry.py`; менять существующее значение — миграция данных.
- **Контракт TypeScript генерируется, а не пишется.** После правки любой модели в `src/budge/api/schemas/` выполнить `budge export-types`; CI валит расхождение.
- **Никаких новых зависимостей.** Картинки генерируются на `zlib` + `struct` из stdlib; HTTP в демо — `urllib.request`. Pillow, httpx и requests в рантайм-зависимостях не появляются (httpx остаётся dev-only).
- **Домен чист.** `decide` не читает часы и не генерирует случайные числа; всё недетерминированное приходит значениями в `DecisionContext`.
- **Границы транспорта (ruling 5).** REST — команды сборки: `CreateMatch`, `AddPlayer`, `AssignSecret`, `DealBoard`, `StartMatch` и теперь `ResetMatch`. Сокету принадлежит всё от `DeclareAttack` и дальше.
- **Значения по умолчанию §12:** `base_seconds=60`, `bonus_cap_seconds=15`, `pass_penalty_seconds=3`.
- **Команды проверки:** бэкенд — `cd backend && ruff check && mypy && pytest`; фронт — `cd frontend && pnpm check && pnpm test`.

---

### Task 1: `ResetMatch` в доменном ядре

Домен — единственное место, где решается, что такое сброс. Всё остальное в плане на этом стоит.

**Files:**
- Modify: `backend/src/budge/domain/actions.py`
- Modify: `backend/src/budge/domain/events.py`
- Modify: `backend/src/budge/domain/decide.py`
- Modify: `backend/src/budge/domain/evolve.py`
- Test: `backend/tests/domain/test_reset.py` (создать)

**Interfaces:**
- Consumes: `MatchState`, `MatchStatus`, `decide`, `evolve` — как есть.
- Produces:
  - `budge.domain.actions.ResetMatch(keep_roster: bool)`, член union'а `Command`
  - `budge.domain.events.MatchReset(keep_roster: bool)`, член union'а `Event`
  - `decide(state, ResetMatch(...), ctx) -> tuple[MatchReset] | tuple[()]`

- [ ] **Step 1: Написать падающие тесты**

Создать `backend/tests/domain/test_reset.py`:

```python
"""§A: сброс возвращает партию в начало, не стирая лог.

Это новое событие в append-only логе, а не удаление старых: то, что было
сыграно, остаётся сыгранным. Сбрасывается состояние, не память.
"""

from dataclasses import replace
from uuid import uuid4

from budge.domain.actions import AddPlayer, AssignSecret, DealBoard, ResetMatch, StartMatch
from budge.domain.events import MatchReset
from budge.domain.ids import CategoryId, PlayerId
from budge.domain.state import MatchState, MatchStatus

from .conftest import apply, build_dealt_state, build_duel_state, build_running_state


def test_a_full_reset_gives_back_the_state_a_match_is_created_in(
    created_state: MatchState,
) -> None:
    """§A.3: остаются `board`, `settings` и `player_count` — ровно то, что
    задал `CreateMatch`, — и больше ничего.

    Kills on: сброс, забывающий обнулить любое из полей партии. Поле в
    поле, а не по списку известных: новое поле в `MatchState`, о котором
    сброс не узнал, валит этот тест.
    """
    state, _ = build_dealt_state(4)
    reset = apply(state, ResetMatch(keep_roster=False))
    assert replace(reset, seq=0) == replace(created_state, seq=0, id=reset.id)


def test_a_full_reset_drops_the_roster_and_the_secrets() -> None:
    state, _ = build_dealt_state(4)
    reset = apply(state, ResetMatch(keep_roster=False))
    assert reset.players == ()
    assert dict(reset.secrets) == {}
    assert reset.status is MatchStatus.SETUP
    assert dict(reset.groups) == {}


def test_a_reset_that_keeps_the_roster_keeps_players_and_their_secrets() -> None:
    state, players = build_dealt_state(4)
    secrets_before = dict(state.secrets)
    reset = apply(state, ResetMatch(keep_roster=True))
    assert tuple(p.id for p in reset.players) == players
    assert dict(reset.secrets) == secrets_before
    assert dict(reset.groups) == {}
    assert reset.status is MatchStatus.SETUP


def test_a_kept_roster_comes_back_with_nobody_eliminated() -> None:
    """`active_players()` фильтрует по этому флагу. Ростер, сохранённый
    вместе с отметками о выбывании, дал бы партию, которая начинается с уже
    выбывшими игроками и рассыпается на первом же `next_turn`.

    Kills on: `players` перенесённый как есть, без снятия `eliminated`.
    """
    state, players = build_running_state(4)
    state = replace(
        state,
        players=tuple(
            replace(person, eliminated=True) if person.id == players[0] else person
            for person in state.players
        ),
    )
    reset = apply(state, ResetMatch(keep_roster=True))
    assert [person.eliminated for person in reset.players] == [False] * 4


def test_a_reset_from_the_middle_of_a_duel_leaves_no_duel() -> None:
    state, _, _, _ = build_duel_state()
    assert state.duel is not None
    reset = apply(state, ResetMatch(keep_roster=True))
    assert reset.duel is None
    assert reset.status is MatchStatus.SETUP


def test_a_kept_roster_can_be_dealt_and_started_again() -> None:
    """Ради чего кнопка и существует: «эту же партию ещё раз»."""
    from support.streams import make_deal

    state, players = build_running_state(4)
    reset = apply(state, ResetMatch(keep_roster=True))
    deal = make_deal(reset.board, players, dict(reset.secrets))
    dealt = apply(reset, DealBoard(), deal=deal)
    started = apply(dealt, StartMatch())
    assert started.status is MatchStatus.RUNNING
    assert len(started.groups) == started.board.cell_count


def test_resetting_a_match_that_is_already_at_the_beginning_writes_nothing(
    created_state: MatchState,
) -> None:
    """§A.4, по прецеденту `AssignSecret`: оператор, дважды нажавший
    «Сбросить», не должен получать ошибку за то, что добился желаемого.

    Kills on: безусловный `MatchReset` — лог рос бы на событие за каждое
    нажатие, и `noop` в API никогда бы не возвращался.
    """
    from budge.domain.context import DecisionContext
    from budge.domain.decide import decide

    from .conftest import BASE_TIME

    events = decide(created_state, ResetMatch(keep_roster=False), DecisionContext(now=BASE_TIME))
    assert events == ()


def test_keeping_the_roster_of_an_untouched_setup_writes_nothing(
    created_state: MatchState,
) -> None:
    from budge.domain.context import DecisionContext
    from budge.domain.decide import decide

    from .conftest import BASE_TIME

    state = apply(
        created_state,
        AddPlayer(player_id=PlayerId(uuid4()), name="Аня", colour="#e4572e"),
    )
    events = decide(state, ResetMatch(keep_roster=True), DecisionContext(now=BASE_TIME))
    assert events == ()


def test_a_full_reset_of_a_setup_with_a_roster_does_write(
    created_state: MatchState,
) -> None:
    """Тот же SETUP, тот же ростер — но флаг другой, и стирать есть что."""
    from budge.domain.context import DecisionContext
    from budge.domain.decide import decide

    from .conftest import BASE_TIME

    player_id = PlayerId(uuid4())
    state = apply(created_state, AddPlayer(player_id=player_id, name="Аня", colour="#e4572e"))
    state = apply(state, AssignSecret(player_id=player_id, category=CategoryId(uuid4())))
    events = decide(state, ResetMatch(keep_roster=False), DecisionContext(now=BASE_TIME))
    assert events == (MatchReset(keep_roster=False),)


def test_reset_is_legal_in_every_status() -> None:
    """§A.4. Механика проверяется в середине партии, а не после неё."""
    for build in (build_dealt_state, build_running_state):
        state, _ = build(4)
        assert apply(state, ResetMatch(keep_roster=True)).status is MatchStatus.SETUP
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd backend && pytest tests/domain/test_reset.py -v`
Expected: FAIL — `ImportError: cannot import name 'ResetMatch' from 'budge.domain.actions'`

- [ ] **Step 3: Добавить команду**

В `backend/src/budge/domain/actions.py`, после `class StartMatch`:

```python
@dataclass(frozen=True, slots=True)
class ResetMatch:
    """§A: вернуть партию в начало. `keep_roster` отвечает на два разных
    вопроса одной командой — «эту же партию ещё раз» и «с нуля»."""

    keep_roster: bool
```

и в union `Command`, после `StartMatch`:

```python
Command = (
    CreateMatch
    | AddPlayer
    | AssignSecret
    | DealBoard
    | StartMatch
    | ResetMatch
    | DeclareAttack
    ...
```

- [ ] **Step 4: Добавить событие**

В `backend/src/budge/domain/events.py`, после `class MatchStarted`:

```python
@dataclass(frozen=True, slots=True)
class MatchReset:
    keep_roster: bool
```

и в union `Event`, после `MatchStarted`:

```python
Event = (
    MatchCreated
    | PlayerAdded
    | SecretAssigned
    | BoardDealt
    | MatchStarted
    | MatchReset
    | AttackDeclared
    ...
```

- [ ] **Step 5: Добавить ветку в `decide`**

В `backend/src/budge/domain/decide.py` — импорты (`ResetMatch` в списке из `actions`, `MatchReset` в списке из `events`), ветка в `match command:` сразу после `case StartMatch()`:

```python
        case ResetMatch():
            return _reset_match(state, command)
```

и функция после `_start_match`:

```python
def _reset_match(state: MatchState, command: ResetMatch) -> tuple[Event, ...]:
    """§A.4: легальна в любой фазе, и пустой переход, когда сбрасывать нечего.

    Отказ здесь был бы хуже пустого события: оператор, дважды нажавший
    «Сбросить», получил бы ошибку за то, что добился желаемого. Прецедент —
    `_assign_secret`, который возвращает `()` на повторном назначении.
    """
    already_at_the_beginning = (
        state.status is MatchStatus.SETUP
        and not state.groups
        and state.duel is None
        and state.winner is None
        and (command.keep_roster or (not state.players and not state.secrets))
    )
    if already_at_the_beginning:
        return ()
    return (MatchReset(keep_roster=command.keep_roster),)
```

- [ ] **Step 6: Добавить ветку в `evolve`**

В `backend/src/budge/domain/evolve.py` — импорт `MatchReset`, ветка после `case MatchStarted()`:

```python
        case MatchReset():
            evolved = replace(
                state,
                status=MatchStatus.SETUP,
                # `eliminated` снимается: `active_players()` фильтрует по
                # нему, и сохранённый ростер с отметками о выбывании дал бы
                # партию, начинающуюся с уже выбывшими игроками.
                players=(
                    tuple(replace(person, eliminated=False) for person in state.players)
                    if event.keep_roster
                    else ()
                ),
                secrets=dict(state.secrets) if event.keep_roster else {},
                turn_order=(),
                turn_index=0,
                round_no=0,
                groups={},
                played_categories=frozenset(),
                duel=None,
                winner=None,
            )
```

- [ ] **Step 7: Прогнать тесты**

Run: `cd backend && pytest tests/domain/test_reset.py -v`
Expected: PASS, все 10.

- [ ] **Step 8: Прогнать инварианты §2.8 через сброс**

Сброс обязан быть переходом, после которого партия остаётся легальной. Свойственные тесты уже гоняют случайные партии — сброс вставляется в случайную точку такой партии.

В `backend/tests/domain/test_invariants.py` дописать, рядом с существующими параметризованными по `seed` тестами:

```python
@pytest.mark.parametrize("seed", range(25))
def test_invariants_survive_a_reset_dropped_into_a_random_match(seed: int) -> None:
    """§A.3: после сброса групп ноль, а это то же состояние, в котором
    партия и так находится между `CreateMatch` и `DealBoard`.

    Kills on: сброс, оставляющий за собой половину доски — например,
    забывший `played_categories`, — биекция групп и неразыгранных категорий
    сломалась бы на первой же раздаче после него.
    """
    from budge.domain.actions import DealBoard, ResetMatch, StartMatch
    from support.streams import make_deal

    rng = random.Random(seed)
    state, players = build_running_state(4)
    clock = 0.0
    for _ in range(rng.randint(1, 4)):
        if state.status is not MatchStatus.RUNNING:
            break
        state, clock = _play_one_duel(state, rng, clock)

    state = fold(
        state,
        decide(state, ResetMatch(keep_roster=True), DecisionContext(now=at(clock))),
    )
    check_invariants(state)

    deal = make_deal(state.board, players, dict(state.secrets))
    state = fold(state, decide(state, DealBoard(), DecisionContext(now=at(clock), deal=deal)))
    check_invariants(state)
    state = fold(state, decide(state, StartMatch(), DecisionContext(now=at(clock))))
    check_invariants(state)
    assert state.status is MatchStatus.RUNNING
```

Run: `cd backend && pytest tests/domain/test_invariants.py -q`
Expected: PASS, все 25 параметров.

- [ ] **Step 9: Прогнать весь доменный слой и линтеры**

Run: `cd backend && ruff check && mypy && pytest tests/domain -q`
Expected: PASS. `tests/domain/test_dispatch.py` может требовать записи о новой команде — если падает с сообщением о неполном покрытии union'а, дописать `ResetMatch` в его таблицу по образцу соседних строк.

- [ ] **Step 10: Коммит**

```bash
git add backend/src/budge/domain backend/tests/domain/test_reset.py
git commit -m "feat: reset a match back to its beginning"
```

---

### Task 2: `MatchReset` через кодек, читающую модель и планировщик

Событие, которое не переживает круг через базу, — это событие, которого нет. Здесь оно получает wire-имя, попадает в золотой поток, учит читающую модель откатываться и подтверждает обещание §A.5 о том, что рантайму для сброса ничего не нужно.

**Files:**
- Modify: `backend/src/budge/db/codec/registry.py`
- Modify: `backend/src/budge/db/projection.py`
- Modify: `backend/tests/support/streams.py`
- Modify: `backend/tests/support/test_streams.py:75-95`
- Modify: `backend/tests/codec/golden/rich_stream.json` (перегенерируется)
- Modify: `backend/tests/db/test_projection.py` (правка `_play_whole_match` + два теста)
- Test: `backend/tests/runtime/test_scheduler.py` (дополнить)

**Interfaces:**
- Consumes: `budge.domain.events.MatchReset` из задачи 1.
- Produces: wire-имя `"match.reset"`; `build_rich_stream()` содержит два `MatchReset` — с `keep_roster=True` и `keep_roster=False`.

- [ ] **Step 1: Убедиться, что кодек уже падает**

Run: `cd backend && pytest tests/codec -q`
Expected: FAIL — `test_every_event_in_the_union_has_a_wire_name` и `test_the_round_trip_covers_the_whole_event_union`. Это сигнал: реестр и золотой поток обязаны знать о каждом событии union'а, и добавление в задаче 1 их уже сломало.

- [ ] **Step 2: Зарегистрировать wire-имя**

В `backend/src/budge/db/codec/registry.py` — импорт `MatchReset` из `budge.domain.events` (в алфавитном порядке списка) и строка в `WIRE_NAMES` после `MatchStarted`:

```python
    MatchStarted: "match.started",
    MatchReset: "match.reset",
```

- [ ] **Step 3: Прогнать реестр**

Run: `cd backend && pytest tests/codec/test_registry.py -q`
Expected: PASS.

- [ ] **Step 4: Довести золотой поток двумя сбросами**

В `backend/tests/support/streams.py` — импорт `ResetMatch` из `budge.domain.actions`, и в конец `build_rich_stream`, сразу после того как матч дошёл до `MatchWon` (перед `return Recorded(...)`):

```python
    # §A: обе формы флага, в одном потоке. Сначала «эту же партию ещё
    # раз» — ростер остаётся, — потом «с нуля», который его убирает.
    # Порядок обязателен: полный сброс после полного сброса был бы пустым
    # переходом и не написал бы ничего.
    recorder.apply(ResetMatch(keep_roster=True), now=now)
    recorder.apply(ResetMatch(keep_roster=False), now=now)
```

Точное имя переменной времени взять из последних строк функции (`now` в текущем коде — там, где живёт результат `_expire`).

- [ ] **Step 5: Поправить ожидаемую форму потока**

В `backend/tests/support/test_streams.py` — в словаре `expected_counts` добавить строку и поправить сумму:

```python
        "MatchWon": 1,
        "MatchReset": 2,
    }
    assert Counter(names) == Counter(expected_counts)
    assert len(names) == sum(expected_counts.values()) == 46
```

Докстринг того же теста дополнить предложением:

```
    `MatchReset` — 2: §A требует обе формы флага в потоке, и они
    приписаны в конец, после того как партия доиграна.
```

- [ ] **Step 6: Перегенерировать золотой файл**

```bash
cd backend && python - <<'PY'
import json
from pathlib import Path
import support.streams as streams
from budge.db.codec import encode
from support.streams import build_rich_stream, deterministic_uuid4

streams.uuid4 = deterministic_uuid4()
rows = [
    {"type": t, "schema_version": v, "payload": p}
    for t, v, p in (encode(e) for e in build_rich_stream().events)
]
target = Path("tests/codec/golden/rich_stream.json")
target.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"{len(rows)} events written")
PY
```

Expected: `46 events written`. Если `python` не видит `support` — запускать через `python -m pytest --collect-only -q` один раз, чтобы `pythonpath` из `pyproject.toml` подхватился, либо префиксовать `PYTHONPATH=src:tests`.

- [ ] **Step 7: Прогнать кодек**

Run: `cd backend && pytest tests/codec tests/support -q`
Expected: PASS. Если golden-тест падает на порядке ключей — сверить формат записи с тем, что читает `test_codec.py` (`type`, `schema_version`, `payload`).

- [ ] **Step 8: Поправить тест проекции, у которого сменился хвост**

Два сброса в конце потока меняют его терминальное состояние: партия кончается не победой, а сборкой без ростера. Один существующий тест на этом падает — и падает правильно.

Run: `cd backend && pytest tests/db/test_projection.py -q`
Expected: FAIL в `test_the_projection_follows_the_match_to_its_end` — `setup != finished`.

Починить его усечением до `MatchWon` — тем же приёмом, каким соседний тест усекается до `MatchStarted`. В `backend/tests/db/test_projection.py` заменить `_play_whole_match` вызовом с границей и добавить параметр:

```python
async def _play_whole_match(
    sessions: async_sessionmaker[AsyncSession], *, until: type | None = None
) -> Recorded:
    """Записать поток в базу целиком — или до первого события типа `until`
    включительно.

    Граница нужна с тех пор, как поток кончается сбросами (§A): «проекция
    дошла до конца партии» — утверждение про `MatchWon`, и снимок,
    взятый после сброса, о нём ничего не говорит.
    """
    recorded = build_rich_stream()
    events = list(recorded.events)
    if until is not None:
        cut = next(i for i, event in enumerate(events) if isinstance(event, until))
        events = events[: cut + 1]
    created = events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(recorded.state.id, created, operation_id="op-create")
    uow = UnitOfWork(sessions)
    for offset, event in enumerate(events[1:]):
        async with uow.begin() as tx:
            await tx.append(
                recorded.state.id,
                expected_last_seq=offset + 1,
                events=(event,),
                operation_id=f"op-{offset}",
            )
    return recorded
```

и в `test_the_projection_follows_the_match_to_its_end` заменить первую строку на:

```python
    recorded = await _play_whole_match(sessions, until=MatchWon)
```

добавив `MatchWon` к импорту из `budge.domain.events`.

**Осторожно с остальными вызовами `_play_whole_match`.** Полный поток теперь кончается снимком `('setup', None, [])` — хвостовой `keep_roster=False` удаляет все строки игроков и обнуляет победителя. А `rebuild` начинается ровно с этого же: `delete(MatchPlayer)` плюс `status=setup, winner_id=None`. Значит, любой тест, сравнивающий снимок после `rebuild` с ожидаемым, на полном потоке вырождается в «пусто против пусто» и проходит, даже если `apply_events` не вызывался вообще.

Это касается двух тестов, и обоим нужен `until=MatchReset`:

```python
    recorded = await _play_whole_match(sessions, until=MatchReset)
```

- `test_a_rebuild_reproduces_the_incremental_projection`
- `test_a_rebuild_from_the_database_alone_restores_the_projection` — здесь вырождение особенно коварно: блок порчи данных в этом тесте выставляет ровно `setup / None / без игроков`, то есть воспроизводит ожидаемый снимок буквально.

`until=MatchReset` останавливает поток после первого сброса, который сохраняет ростер: снимок получается `setup / None / двое неисключённых игроков` — невырожденный, и ветка сброса при этом всё равно разыграна на обоих путях. Это строго больше покрытия, чем было до появления сбросов в потоке.

- [ ] **Step 9: Написать падающие тесты на читающую модель**

В `backend/tests/db/test_projection.py` дописать:

```python
async def test_a_full_reset_empties_the_roster_and_returns_the_match_to_setup(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§A.6: сброс в читающей модели — это первая половина `rebuild`.

    Kills on: сброс, не отражённый в проекции, — список партий показывал бы
    победителя у партии, которая заново стоит в сборке.
    """
    recorded = await _play_whole_match(sessions)
    status, winner_id, players = await _snapshot(sessions, recorded.state.id)
    assert status == MatchStatus.SETUP.value
    assert winner_id is None
    assert players == []


async def test_a_reset_that_keeps_the_roster_un_eliminates_everyone(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Поток кончается двумя сбросами: сперва с ростером, потом без него.
    Здесь проверяется первый — снимок берётся до второго.

    Kills on: ветка `keep_roster=True`, забывшая снять `eliminated`, —
    партия переигрывалась бы с уже выбывшим игроком.
    """
    recorded = build_rich_stream()
    events = list(recorded.events)
    first_reset = next(i for i, event in enumerate(events) if isinstance(event, MatchReset))
    created = events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(recorded.state.id, created, operation_id="op-create")
    uow = UnitOfWork(sessions)
    for offset, event in enumerate(events[1 : first_reset + 1]):
        async with uow.begin() as tx:
            await tx.append(
                recorded.state.id,
                expected_last_seq=offset + 1,
                events=(event,),
                operation_id=f"op-{offset}",
            )
    status, winner_id, players = await _snapshot(sessions, recorded.state.id)
    assert status == MatchStatus.SETUP.value
    assert winner_id is None
    assert len(players) == 2
    assert all(not eliminated for _, _, _, eliminated in players)
```

Импорт `MatchReset` дописать к существующему импорту из `budge.domain.events`.

- [ ] **Step 10: Убедиться, что тесты падают**

Run: `cd backend && pytest tests/db/test_projection.py -q -k reset`
Expected: FAIL — статус остаётся `finished`, строки игроков на месте.

- [ ] **Step 11: Научить проекцию сбросу**

В `backend/src/budge/db/projection.py` — импорт `MatchReset`, ветка в `apply_events` после `case MatchStarted()`:

```python
            case MatchReset():
                # §A.6: построчно первая половина `rebuild`. Совпадение не
                # случайное — оно и делает тест «инкрементальный путь и
                # перестройка согласны» покрытием сброса.
                if event.keep_roster:
                    await session.execute(
                        update(MatchPlayer)
                        .where(MatchPlayer.match_id == match_id)
                        .values(eliminated=False)
                    )
                else:
                    await session.execute(
                        delete(MatchPlayer).where(MatchPlayer.match_id == match_id)
                    )
                await session.execute(
                    update(Match)
                    .where(Match.id == match_id)
                    .values(status=MatchStatus.SETUP.value, winner_id=None)
                )
```

- [ ] **Step 12: Написать тест на то, что сброс разоружает дедлайн**

§A.5 утверждает, что рантайм не требует ни строчки, потому что планировщик взводится от состояния. Утверждение бесплатное — но только пока его держит тест.

В `backend/tests/runtime/test_scheduler.py` дописать:

```python
async def test_a_reset_disarms_the_deadline() -> None:
    """§A.5: `reschedule` читает состояние, а состояние без дуэли не имеет
    дедлайна — и задача снимается на общих основаниях, без единой строчки,
    знающей про сброс.

    Kills on: планировщик, снимающий задачу по списку известных событий, —
    сброс посреди дуэли оставил бы живой таймер, который через минуту
    разрешил бы дуэль, которой уже нет.
    """
    async def _never_fires(_deadline_id: int) -> None:
        raise AssertionError("the deadline must not fire in this test")

    recorded = build_rich_stream()
    duelling = _state_after(recorded, DuelStarted)
    clock = FakeClock(BASE_TIME)
    scheduler = DeadlineScheduler(clock, _never_fires)

    scheduler.reschedule(duelling)
    await clock.settle()
    assert scheduler.armed is True

    reset = fold(duelling, (MatchReset(keep_roster=True),))
    scheduler.reschedule(reset)
    await clock.settle()
    assert scheduler.armed is False
    assert reset.duel is None
```

Импорты: `MatchReset` к существующему списку из `budge.domain.events`. `_state_after` и `FakeClock` в файле уже есть — сверить точные имена и подставить; если у `FakeClock` метод донастройки цикла называется иначе, чем `settle`, использовать тот, которым пользуются соседние тесты этого модуля.

- [ ] **Step 13: Прогнать всё, что стоит на потоке**

Run: `cd backend && pytest tests/db tests/codec tests/support tests/runtime tests/backup -q`
Expected: PASS. Поток вырос на два события, и это видят все эти каталоги; если что-то падает на длине или на терминальном состоянии, чинить утверждение, а не поток — сбросы в хвосте требует §H.

- [ ] **Step 14: Коммит**

```bash
git add backend/src/budge/db backend/tests
git commit -m "feat: carry a reset through the codec, the read model and the scheduler"
```

---

### Task 3: `POST /api/matches/{id}/reset`

**Files:**
- Modify: `backend/src/budge/api/schemas/rest.py`
- Modify: `backend/src/budge/api/routes/matches.py`
- Modify: `backend/src/budge/contracts/schema.py`
- Modify: `frontend/src/shared/api/contracts.ts` (генерируется)
- Test: `backend/tests/api/test_match_routes.py` (дополнить)

**Interfaces:**
- Consumes: `budge.domain.actions.ResetMatch` из задачи 1.
- Produces: `POST /api/matches/{match_id}/reset` с телом `{"keep_roster": bool}`, отвечающий обычным `OutcomeBody`; `ResetMatchBody` в экспортируемом контракте.

- [ ] **Step 1: Написать падающие тесты**

В `backend/tests/api/test_match_routes.py` дописать, следуя стилю файла (там уже есть хелперы, поднимающие приложение и логинящие ведущего — переиспользовать их, а не писать свои):

```python
async def test_reset_returns_a_running_match_to_setup(host_client: httpx.AsyncClient) -> None:
    """§A.8: команда сборки, поэтому REST — рядом с `deal` и `start`.

    Kills on: роут, отправляющий команду мимо шлюза, — рантайм не узнал бы
    о сбросе, и следующий кадр показал бы доску, которой уже нет.
    """
    match_id = await _a_started_match(host_client)
    response = await host_client.post(
        f"/api/matches/{match_id}/reset", json={"keep_roster": True}
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "accepted"

    snapshot = (await host_client.get(f"/api/matches/{match_id}")).json()
    assert snapshot["frame"]["status"] == "setup"
    assert snapshot["frame"]["groups"] == []
    assert len(snapshot["frame"]["players"]) == 2


async def test_a_full_reset_drops_the_roster(host_client: httpx.AsyncClient) -> None:
    match_id = await _a_started_match(host_client)
    response = await host_client.post(
        f"/api/matches/{match_id}/reset", json={"keep_roster": False}
    )
    assert response.status_code == 200

    snapshot = (await host_client.get(f"/api/matches/{match_id}")).json()
    assert snapshot["frame"]["players"] == []


async def test_resetting_a_fresh_match_is_a_noop_and_not_an_error(
    host_client: httpx.AsyncClient,
) -> None:
    """§A.4 через `outcomes.py`: пустой переход — это 200 `noop`, а не 409.

    Kills on: отказ вместо пустого события — оператор, нажавший «Сбросить»
    дважды, увидел бы ошибку за то, что добился желаемого.
    """
    match_id = await _a_created_match(host_client)
    response = await host_client.post(
        f"/api/matches/{match_id}/reset", json={"keep_roster": False}
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "noop"


async def test_reset_needs_the_host(client: httpx.AsyncClient) -> None:
    """Роутер целиком под `require_host`; тест держит это на новом роуте."""
    response = await client.post(
        "/api/matches/00000000-0000-0000-0000-000000000001/reset",
        json={"keep_roster": True},
    )
    assert response.status_code == 401


async def test_reset_refuses_a_body_it_does_not_understand(
    host_client: httpx.AsyncClient,
) -> None:
    """`Body` объявлен `extra="forbid"`: клиент, думавший, что что-то
    сказал, не должен получить 200."""
    match_id = await _a_created_match(host_client)
    response = await host_client.post(
        f"/api/matches/{match_id}/reset", json={"keep_roster": True, "wipe_library": True}
    )
    assert response.status_code == 422
```

Хелперы `_a_created_match` и `_a_started_match`: если в файле уже есть эквиваленты (создание партии, добавление двух игроков с секретами, `deal`, `start`), использовать их. Если нет — написать один раз наверху файла:

```python
async def _a_created_match(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/matches", json={"board": {"width": 3, "height": 4}, "player_count": 2}
    )
    assert response.status_code == 201
    return str(response.json()["match_id"])


async def _a_started_match(client: httpx.AsyncClient) -> str:
    """Партия, доведённая до RUNNING через те же роуты, что и консоль.

    Библиотека наполняется здесь же: `deal` тянет обычные категории из
    активной библиотеки, и без них он отказал бы с `content_unavailable`.
    """
    match_id = await _a_created_match(client)
    secrets = []
    for title in ("Тайна 1", "Тайна 2"):
        created = await client.post(
            "/api/library/categories", json={"title": title, "is_secret": True}
        )
        secrets.append(created.json()["id"])
    for index in range(12):
        await client.post(
            "/api/library/categories", json={"title": f"Тема {index}", "is_secret": False}
        )
    for index, secret in enumerate(secrets):
        player_id = str(UUID(int=index + 1))
        await client.post(
            f"/api/matches/{match_id}/players",
            json={"player_id": player_id, "name": f"P{index}", "colour": "#e4572e"},
        )
        await client.post(
            f"/api/matches/{match_id}/secrets",
            json={"player_id": player_id, "category": secret},
        )
    assert (await client.post(f"/api/matches/{match_id}/deal")).json()["outcome"] == "accepted"
    assert (await client.post(f"/api/matches/{match_id}/start")).json()["outcome"] == "accepted"
    return match_id
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd backend && pytest tests/api/test_match_routes.py -q -k reset`
Expected: FAIL — 404 на несуществующем роуте.

- [ ] **Step 3: Добавить модель тела**

В `backend/src/budge/api/schemas/rest.py`, после `class AssignSecretBody`:

```python
class ResetMatchBody(Body):
    """§A.2: один флаг, отвечающий на два вопроса.

    Без значения по умолчанию намеренно: «сбросить» — необратимое действие
    перед залом, и клиент, забывший сказать, что именно он имел в виду,
    должен получить 422, а не самый разрушительный вариант молча.
    """

    keep_roster: bool
```

- [ ] **Step 4: Добавить роут**

В `backend/src/budge/api/routes/matches.py` — импорты (`ResetMatch` к списку из `budge.domain.actions`, `ResetMatchBody` к списку из `budge.api.schemas.rest`), и роут после `start_match`:

```python
@router.post("/{match_id}/reset")
async def reset_match(match_id: UUID, body: ResetMatchBody, request: Request) -> JSONResponse:
    """§A.8: команда сборки, поэтому REST.

    Её можно нажать из RUNNING, и это границы не меняет: сокету принадлежат
    команды *внутри* партии, от `DeclareAttack` и дальше, а эта возвращает
    партию в SETUP и стоит рядом с `deal` и `start`. Новый кадр приезжает
    подписчикам обычным путём — `MatchRuntime._publish` вещает его в хаб,
    как для любой другой команды.
    """
    return await _run(request, MatchId(match_id), ResetMatch(keep_roster=body.keep_roster))
```

- [ ] **Step 5: Зарегистрировать модель в контракте**

В `backend/src/budge/contracts/schema.py` — импорт `ResetMatchBody` из `budge.api.schemas.rest` и строка в списке моделей, рядом с прочими `validation`:

```python
    (AssignSecretBody, "validation"),
    (ResetMatchBody, "validation"),
```

- [ ] **Step 6: Перегенерировать контракт**

Run: `cd backend && budge export-types`
Expected: печатает путь к `frontend/src/shared/api/contracts.ts`; в файле появляется `export interface ResetMatchBody { keep_roster: boolean }`.

- [ ] **Step 7: Прогнать**

Run: `cd backend && ruff check && mypy && pytest tests/api -q && budge export-types --check`
Expected: PASS.

- [ ] **Step 8: Коммит**

```bash
git add backend/src/budge/api backend/src/budge/contracts backend/tests/api frontend/src/shared/api/contracts.ts
git commit -m "feat: expose the reset as a REST command"
```

---

### Task 4: Кнопки сброса в пульте ведущего

Две кнопки, доступные во всех тактах партии, — потому что механику проверяют из середины.

**Files:**
- Modify: `frontend/src/features/match-assembly/api/use-assembly.ts`
- Create: `frontend/src/widgets/match-reset/ui/match-reset.tsx`
- Create: `frontend/src/widgets/match-reset/index.ts`
- Modify: `frontend/src/pages/host-match/ui/match-page.tsx`
- Test: `frontend/src/widgets/match-reset/ui/match-reset.test.tsx` (создать)

**Interfaces:**
- Consumes: `POST /api/matches/{id}/reset` из задачи 3.
- Produces:
  - `useReset()` из `@/features/match-assembly` — мутация `({ matchId, keep_roster }: { matchId: string; keep_roster: boolean }) => Promise<OutcomeBody>`
  - `<MatchReset matchId={string} />` из `@/widgets/match-reset`

- [ ] **Step 1: Написать падающий тест**

Создать `frontend/src/widgets/match-reset/ui/match-reset.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { MatchReset } from "./match-reset";

const MATCH = "33333333-3333-3333-3333-333333333333";

function capture(): { sent: { keep_roster: boolean }[] } {
  const sent: { keep_roster: boolean }[] = [];
  server.use(
    http.post(`/api/matches/${MATCH}/reset`, async ({ request }) => {
      sent.push((await request.json()) as { keep_roster: boolean });
      return HttpResponse.json({ outcome: "accepted" });
    }),
  );
  return { sent };
}

describe("MatchReset", () => {
  it("replays the same match with the roster kept", async () => {
    const { sent } = capture();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithQuery(<MatchReset matchId={MATCH} />);
    await userEvent.click(screen.getByRole("button", { name: "Переиграть" }));
    await waitFor(() => expect(sent).toEqual([{ keep_roster: true }]));
  });

  it("wipes the roster on a full reset", async () => {
    const { sent } = capture();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithQuery(<MatchReset matchId={MATCH} />);
    await userEvent.click(screen.getByRole("button", { name: "Сбросить полностью" }));
    await waitFor(() => expect(sent).toEqual([{ keep_roster: false }]));
  });

  it("sends nothing when the operator backs out of the confirmation", async () => {
    // §A.8: партия идёт перед залом, и эти кнопки стоят рядом с теми,
    // которые ведущий жмёт по десять раз за дуэль.
    // Kills on: кнопка без подтверждения — один промах мимо «Верно»
    // стирал бы доску на глазах у зала.
    const { sent } = capture();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithQuery(<MatchReset matchId={MATCH} />);
    await userEvent.click(screen.getByRole("button", { name: "Сбросить полностью" }));
    expect(sent).toEqual([]);
  });
});
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd frontend && pnpm vitest run src/widgets/match-reset`
Expected: FAIL — модуль `./match-reset` не найден.

- [ ] **Step 3: Добавить мутацию**

В `frontend/src/features/match-assembly/api/use-assembly.ts`, после `useStart`:

```ts
/** §A.8: обе кнопки — одна команда с флагом. `evolve` у них расходится на
 * одну строку, и два маршрута ради одного булева были бы двумя вещами
 * там, где хватает одной. */
export function useReset() {
  return useMutation({
    mutationFn: ({ matchId, keep_roster }: { matchId: string; keep_roster: boolean }) =>
      outcome(`/api/matches/${matchId}/reset`, post({ keep_roster })),
  });
}
```

- [ ] **Step 4: Написать виджет**

Создать `frontend/src/widgets/match-reset/ui/match-reset.tsx`:

```tsx
import { useReset } from "@/features/match-assembly";

export interface MatchResetProps {
  matchId: string;
}

const PROMPTS = {
  replay: "Переиграть партию? Доска и история будут стёрты, игроки и их секреты останутся.",
  full: "Сбросить партию полностью? Будут стёрты доска, история, игроки и их секреты.",
} as const;

/** §A.8: доступно в любом такте, включая середину дуэли — механика
 * проверяется из середины, а не после.
 *
 * Подтверждение обязательно: партия идёт перед залом, и эти кнопки стоят
 * рядом с теми, которые ведущий жмёт по десять раз за дуэль. */
export function MatchReset({ matchId }: MatchResetProps) {
  const reset = useReset();

  function run(keep_roster: boolean, prompt: string) {
    if (!window.confirm(prompt)) return;
    void reset.mutateAsync({ matchId, keep_roster });
  }

  return (
    <div className="flex gap-2">
      <button
        type="button"
        onClick={() => run(true, PROMPTS.replay)}
        className="rounded-lg bg-white/10 px-3 py-1 text-sm text-stage-muted hover:bg-white/15"
      >
        Переиграть
      </button>
      <button
        type="button"
        onClick={() => run(false, PROMPTS.full)}
        className="rounded-lg bg-white/10 px-3 py-1 text-sm text-stage-muted hover:bg-white/15"
      >
        Сбросить полностью
      </button>
    </div>
  );
}
```

Создать `frontend/src/widgets/match-reset/index.ts`:

```ts
export { MatchReset } from "./ui/match-reset";
```

- [ ] **Step 5: Прогнать тест виджета**

Run: `cd frontend && pnpm vitest run src/widgets/match-reset`
Expected: PASS, все три.

- [ ] **Step 6: Повесить виджет на страницу партии**

В `frontend/src/pages/host-match/ui/match-page.tsx` — импорт `import { MatchReset } from "@/widgets/match-reset";` и полоса над тактовым переключателем, внутри внешнего `div`, сразу после блока `refusal`:

```tsx
      <div className="flex justify-end border-white/10 border-b px-6 py-2">
        <MatchReset matchId={matchId} />
      </div>
```

- [ ] **Step 7: Прогнать фронт целиком**

Run: `cd frontend && pnpm check && pnpm test`
Expected: PASS. `steiger` проверяет слоистость FSD — виджет, зависящий от `features`, легален; если он ругается на индекс, сверить структуру с соседним `widgets/match-setup`.

- [ ] **Step 8: Коммит**

```bash
git add frontend/src/features/match-assembly frontend/src/widgets/match-reset frontend/src/pages/host-match
git commit -m "feat: give the console a replay and a full reset"
```

---

### Task 5: Вердикт готовности начинает считать секреты

**Files:**
- Modify: `backend/src/budge/api/routes/library.py:236-266`
- Modify: `backend/src/budge/api/schemas/library.py:98-111`
- Modify: `frontend/src/shared/api/contracts.ts` (генерируется)
- Test: `backend/tests/api/test_library_routes.py` (дополнить)

**Interfaces:**
- Produces: `GET /api/library/readiness?cells=N&players=M`; `ReadinessBody` получает поле `players: int`.

- [ ] **Step 1: Написать падающие тесты**

В `backend/tests/api/test_library_routes.py` дописать, переиспользуя существующие хелперы файла для создания категорий:

```python
async def test_readiness_is_not_ready_without_enough_secret_categories(
    host_client: httpx.AsyncClient,
) -> None:
    """§B: библиотека из двенадцати обычных тем и нуля секретных сегодня
    рапортует «Тем достаточно: 12» — и партия из неё несобираема, потому
    что каждому игроку нужен свой секрет.

    Kills on: вердикт, считающий только обычные темы, — оператор дошёл бы
    до пустого списка секретов, ничего об этом не узнав.
    """
    for index in range(12):
        await host_client.post(
            "/api/library/categories", json={"title": f"Тема {index}", "is_secret": False}
        )
    body = (await host_client.get("/api/library/readiness?cells=12&players=3")).json()
    assert body["ordinary_available"] == 12
    assert body["secrets_available"] == 0
    assert body["ready"] is False


async def test_readiness_is_ready_when_both_pools_cover_the_board(
    host_client: httpx.AsyncClient,
) -> None:
    """`cells - players` — счёт из `Materialiser._deal`, который тянет из
    банка ровно столько обычных категорий: остальные клетки занимают
    секреты."""
    for index in range(9):
        await host_client.post(
            "/api/library/categories", json={"title": f"Тема {index}", "is_secret": False}
        )
    for index in range(3):
        await host_client.post(
            "/api/library/categories", json={"title": f"Тайна {index}", "is_secret": True}
        )
    body = (await host_client.get("/api/library/readiness?cells=12&players=3")).json()
    assert body["ready"] is True
    assert body["players"] == 3


async def test_readiness_without_a_player_count_keeps_the_strict_verdict(
    host_client: httpx.AsyncClient,
) -> None:
    """При `players = 0` выражение вырождается в прежнее поведение, так что
    старый вызов остаётся верным и спецслучая не требуется."""
    for index in range(12):
        await host_client.post(
            "/api/library/categories", json={"title": f"Тема {index}", "is_secret": False}
        )
    assert (await host_client.get("/api/library/readiness?cells=12")).json()["ready"] is True
    assert (await host_client.get("/api/library/readiness?cells=13")).json()["ready"] is False
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd backend && pytest tests/api/test_library_routes.py -q -k readiness`
Expected: FAIL — первый тест видит `ready: true`, второй падает на отсутствующем ключе `players`.

- [ ] **Step 3: Добавить поле в модель ответа**

В `backend/src/budge/api/schemas/library.py`, в `ReadinessBody`, после `cells`:

```python
    cells: int
    players: int
```

и в докстринг класса дописать абзац:

```
    §B: вердикт считает оба пула. Обычных категорий нужно `cells - players`
    — остальные клетки занимают секреты, — а секретных ровно `players`,
    потому что `AssignSecret` требует различных категорий.
```

- [ ] **Step 4: Научить роут считать**

В `backend/src/budge/api/routes/library.py` заменить сигнатуру и хвост `readiness`:

```python
@router.get("/readiness")
async def readiness(request: Request, cells: int = 0, players: int = 0) -> ReadinessBody:
    """§8's soft warning, answered in one call.

    «Отбор на партию: из активных, без повторов, число равно числу клеток
    поля» — a board of `cells` cells needs `cells - players` ordinary
    categories plus one secret each. `cells - players` is the count
    `Materialiser._deal` actually draws from the bank: the remaining cells
    carry the players' own secrets.

    `players = 0` degenerates to the older, stricter reading — enough
    ordinary categories for every cell, and no opinion about secrets — so a
    caller that does not know the roster size still gets a usable answer and
    no special case is needed.

    It refuses nothing either way (§8 asks for «мягкое предупреждение»), and
    an operator who wants the show to go on can start it.
    """
```

и в теле функции заменить вычисление `ready` и конструктор:

```python
    return ReadinessBody(
        cells=cells,
        players=players,
        threshold=settings.thin_image_threshold,
        ordinary_available=len(ordinary),
        secrets_available=len(secrets),
        thin=tuple(thin),
        ready=len(ordinary) >= cells - players and len(secrets) >= players,
    )
```

- [ ] **Step 5: Прогнать и перегенерировать контракт**

Run: `cd backend && pytest tests/api/test_library_routes.py -q && budge export-types && ruff check && mypy`
Expected: PASS; в `contracts.ts` у `ReadinessBody` появляется `players: number`.

- [ ] **Step 6: Коммит**

```bash
git add backend/src/budge/api backend/tests/api frontend/src/shared/api/contracts.ts
git commit -m "fix: count secret categories in the readiness verdict"
```

---

### Task 6: Экран сборки перестаёт быть немым

Тупик, с которого всё началось, плюс три соседних расхождения: причина неготовности, создание секретной темы и границы доски.

**Files:**
- Modify: `backend/src/budge/api/schemas/frames.py:177-195`
- Modify: `backend/src/budge/api/projection.py` (в `project_host`)
- Modify: `frontend/src/features/library/api/use-library.ts:41-46`
- Modify: `frontend/src/widgets/match-setup/ui/match-setup.tsx`
- Modify: `frontend/src/pages/host-library/ui/library-page.tsx`
- Modify: `frontend/src/pages/host-home/ui/home-page.tsx`
- Modify: `frontend/testing/host-frames.ts:67-93`
- Test: `frontend/src/widgets/match-setup/ui/match-setup.test.tsx`, `frontend/src/pages/host-library/ui/library-page.test.tsx`, `frontend/src/pages/host-home/ui/home-page.test.tsx` (дополнить)

**Interfaces:**
- Consumes: `ReadinessBody.players` из задачи 5.
- Produces: `HostFrame.player_count: number`; `useReadiness(cells: number, players: number)`.

- [ ] **Step 1: Написать падающий тест на кадр**

В `backend/tests/api/test_frames.py` дописать:

```python
async def test_the_host_frame_carries_the_declared_player_count() -> None:
    """§C: экран сборки спрашивает готовность по числу игроков, которое
    *объявлено*, а не по числу уже добавленных.

    Kills on: кадр без `player_count` — экран считал бы готовность по
    `players.length` и говорил «нужен один секрет», пока трое ещё не
    добавлены.
    """
    state, _ = build_setup_state(4)
    frame = await project_host(
        state, now=BASE_TIME, events=(), directory=RecordingContentDirectory()
    )
    assert frame.player_count == 4
```

Импорты (`build_setup_state`, `project_host`, `RecordingContentDirectory`, `BASE_TIME`) взять по образцу соседних тестов этого файла.

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && pytest tests/api/test_frames.py -q -k player_count`
Expected: FAIL — `AttributeError: 'HostFrame' object has no attribute 'player_count'`.

- [ ] **Step 3: Добавить поле в кадр**

В `backend/src/budge/api/schemas/frames.py`, в `HostFrame`, после `players`:

```python
    players: tuple[PlayerFrame, ...]
    # §C: объявленное число, не число уже добавленных. Экран сборки
    # спрашивает готовность библиотеки по нему, и `len(players)` во время
    # сборки заведомо меньше.
    player_count: int
```

В `backend/src/budge/api/projection.py`, в конструкторе `HostFrame` внутри `project_host`, рядом с `players=`:

```python
        player_count=state.player_count,
```

- [ ] **Step 4: Прогнать бэкенд и перегенерировать контракт**

Run: `cd backend && pytest tests/api -q && budge export-types && ruff check && mypy`
Expected: PASS; в `contracts.ts` у `HostFrame` появляется `player_count: number`.

Затем в `frontend/testing/host-frames.ts`, в `hostFrame`, добавить после `players`:

```ts
    player_count: 2,
```

- [ ] **Step 5: Написать падающие тесты фронта**

В `frontend/src/widgets/match-setup/ui/match-setup.test.tsx` дописать:

```tsx
  it("says what the library is missing instead of showing an empty picker", async () => {
    // Тупик, с которого всё началось: пустая библиотека оставляет в
    // `<select>` один disabled-пункт, и на экране не написано ничего.
    // Kills on: экран без вердикта готовности — оператор упирается в
    // немой список и не узнаёт ни причины, ни куда идти.
    server.use(
      http.get("/api/library/categories", () => HttpResponse.json([])),
      http.get("/api/library/readiness", () =>
        HttpResponse.json({
          cells: 9,
          players: 3,
          threshold: 5,
          ordinary_available: 0,
          secrets_available: 0,
          thin: [],
          ready: false,
        }),
      ),
    );
    renderWithQuery(
      <MatchSetup
        frame={hostFrame({ status: "setup", players: [], player_count: 3 })}
        matchId={MATCH}
        stageToken="t"
      />,
    );
    expect(await screen.findByText(/секретных тем/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Библиотека" })).toHaveAttribute(
      "href",
      "/host/library",
    );
  });

  it("stays quiet when the library covers the board", async () => {
    stock();
    server.use(
      http.get("/api/library/readiness", () =>
        HttpResponse.json({
          cells: 9,
          players: 3,
          threshold: 5,
          ordinary_available: 12,
          secrets_available: 4,
          thin: [],
          ready: true,
        }),
      ),
    );
    renderWithQuery(
      <MatchSetup frame={hostFrame({ player_count: 2 })} matchId={MATCH} stageToken="t" />,
    );
    await screen.findByRole("option", { name: "Тайна" });
    expect(screen.queryByText(/не хватает/i)).not.toBeInTheDocument();
  });

  it("asks readiness about the declared roster, not the one filled in so far", async () => {
    const asked: string[] = [];
    stock();
    server.use(
      http.get("/api/library/readiness", ({ request }) => {
        asked.push(new URL(request.url).search);
        return HttpResponse.json({
          cells: 12,
          players: 4,
          threshold: 5,
          ordinary_available: 12,
          secrets_available: 4,
          thin: [],
          ready: true,
        });
      }),
    );
    renderWithQuery(
      <MatchSetup
        frame={hostFrame({ board: { width: 4, height: 3 }, players: [], player_count: 4 })}
        matchId={MATCH}
        stageToken="t"
      />,
    );
    await waitFor(() => expect(asked).not.toHaveLength(0));
    expect(asked[0]).toContain("cells=12");
    expect(asked[0]).toContain("players=4");
  });
```

В `frontend/src/pages/host-library/ui/library-page.test.tsx` дописать:

```tsx
  it("names the real reason instead of always blaming thin categories", async () => {
    // Сегодня сообщение всегда одно, и при пустом `thin` оператор читает
    // буквально «Мало картинок: —».
    // Kills on: единственная формулировка — оператор ищет картинки там,
    // где не хватает тем.
    server.use(
      http.get("/api/library/categories", () => HttpResponse.json([])),
      http.get("/api/library/readiness", () =>
        HttpResponse.json({
          cells: 12,
          players: 0,
          threshold: 5,
          ordinary_available: 3,
          secrets_available: 0,
          thin: [],
          ready: false,
        }),
      ),
    );
    renderWithQuery(<LibraryPage />);
    const badge = await screen.findByTestId("readiness");
    expect(badge).toHaveTextContent(/обычных тем/i);
    expect(badge).not.toHaveTextContent("—");
  });

  it("creates a secret theme in one step", async () => {
    // §E: без флажка секретную тему заводят в четыре шага, из которых три
    // существуют только потому, что первый не спросил.
    const sent: { title: string; is_secret: boolean }[] = [];
    server.use(
      http.get("/api/library/categories", () => HttpResponse.json([])),
      http.get("/api/library/readiness", () =>
        HttpResponse.json({
          cells: 12,
          players: 0,
          threshold: 5,
          ordinary_available: 0,
          secrets_available: 0,
          thin: [],
          ready: false,
        }),
      ),
      http.post("/api/library/categories", async ({ request }) => {
        sent.push((await request.json()) as { title: string; is_secret: boolean });
        return HttpResponse.json({}, { status: 201 });
      }),
    );
    renderWithQuery(<LibraryPage />);
    await userEvent.type(screen.getByLabelText("Новая тема"), "Тайна Киры");
    await userEvent.click(screen.getByLabelText("Секретная"));
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));
    await waitFor(() => expect(sent).toEqual([{ title: "Тайна Киры", is_secret: true }]));
  });
```

В `frontend/src/pages/host-home/ui/home-page.test.tsx` дописать:

```tsx
  it("will not offer a board side the rules forbid", () => {
    // §2.1: W ≥ 3 и H ≥ 3. Сегодня оператор узнаёт это от сервера, отказом.
    server.use(http.get("/api/matches", () => HttpResponse.json([])));
    renderWithQuery(<HomePage />);
    expect(screen.getByLabelText("Ширина")).toHaveAttribute("min", "3");
    expect(screen.getByLabelText("Высота")).toHaveAttribute("min", "3");
  });

  it("names the rule a board breaks before anything is sent", async () => {
    // 5×4 = 20 клеток на троих: 20 не делится на 3, и это единственное из
    // трёх условий §2.1, которое здесь нарушено.
    server.use(http.get("/api/matches", () => HttpResponse.json([])));
    renderWithQuery(<HomePage />);
    await userEvent.clear(screen.getByLabelText("Ширина"));
    await userEvent.type(screen.getByLabelText("Ширина"), "5");
    await userEvent.clear(screen.getByLabelText("Высота"));
    await userEvent.type(screen.getByLabelText("Высота"), "4");
    expect(await screen.findByText(/не делится на 3/i)).toBeInTheDocument();
  });
```

Оба теста обязаны застабить `/api/matches`: `testing/setup.ts` поднимает msw с `onUnhandledRequest: "error"`, и `HomePage` дёргает этот роут при монтировании.

- [ ] **Step 6: Убедиться, что тесты падают**

Run: `cd frontend && pnpm vitest run src/widgets/match-setup src/pages/host-library src/pages/host-home`
Expected: FAIL по каждому новому тесту.

- [ ] **Step 7: Расширить хук готовности**

В `frontend/src/features/library/api/use-library.ts` заменить `useReadiness`:

```ts
/** §B: вердикт считает оба пула, и число игроков — часть вопроса. */
export function useReadiness(cells: number, players = 0): UseQueryResult<ReadinessBody> {
  return useQuery({
    queryKey: [LIBRARY, "readiness", cells, players],
    queryFn: () =>
      json<ReadinessBody>(`/api/library/readiness?cells=${cells}&players=${players}`),
  });
}
```

- [ ] **Step 8: Вынести формулировку причины в общее место**

Создать `frontend/src/features/library/model/shortfall.ts`:

```ts
import type { ReadinessBody } from "@/shared/api";

/** §D: причин три, они независимы, и называется каждая, которая верна.
 *
 * Пустой массив означает «всё на месте» — вызывающий сам решает, показывать
 * ли что-нибудь вместо этого. */
export function shortfallOf(readiness: ReadinessBody): string[] {
  const missing: string[] = [];
  const ordinaryNeeded = readiness.cells - readiness.players;
  if (readiness.ordinary_available < ordinaryNeeded) {
    missing.push(
      `обычных тем ${readiness.ordinary_available} из ${ordinaryNeeded}`,
    );
  }
  if (readiness.secrets_available < readiness.players) {
    missing.push(
      `секретных тем ${readiness.secrets_available} из ${readiness.players}`,
    );
  }
  if (readiness.thin.length > 0) {
    missing.push(
      `мало картинок: ${readiness.thin.map((row) => row.title).join(", ")}`,
    );
  }
  return missing;
}
```

и экспортировать из `frontend/src/features/library/index.ts`, рядом с прочими:

```ts
export { shortfallOf } from "./model/shortfall";
```

- [ ] **Step 9: Показать готовность на экране сборки**

В `frontend/src/widgets/match-setup/ui/match-setup.tsx` — импорты:

```tsx
import { Link } from "@tanstack/react-router";
import { shortfallOf, useCategories, useReadiness } from "@/features/library";
```

в теле компонента, после `const categories = useCategories();`:

```tsx
  const cells = frame.board.width * frame.board.height;
  const readiness = useReadiness(cells, frame.player_count);
  const missing = readiness.data ? shortfallOf(readiness.data) : [];
```

и блок сразу после ссылки на экран сцены:

```tsx
      {missing.length > 0 && (
        <p className="flex flex-wrap items-center gap-2 rounded-lg bg-amber-500/15 p-3 text-amber-300">
          <span>{`Библиотеке не хватает: ${missing.join("; ")}.`}</span>
          <Link to="/host/library" className="underline">
            Библиотека
          </Link>
        </p>
      )}
```

- [ ] **Step 10: Починить сообщение и флажок в библиотеке**

В `frontend/src/pages/host-library/ui/library-page.tsx` — импорт `shortfallOf` из `@/features/library`, состояние флажка рядом с `title`:

```tsx
  const [isSecret, setIsSecret] = useState(false);
```

заменить блок `readiness.data && (...)`:

```tsx
        {readiness.data && (
          <p
            data-testid="readiness"
            data-ready={readiness.data.ready}
            className="rounded-lg bg-white/5 p-3 text-sm text-stage-muted data-[ready=false]:text-amber-300"
          >
            {readiness.data.ready
              ? `Тем достаточно: ${readiness.data.ordinary_available}`
              : `Не хватает: ${shortfallOf(readiness.data).join("; ")}`}
          </p>
        )}
```

и добавить флажок с передачей его в мутацию:

```tsx
          <label className="flex items-center gap-2 text-sm text-stage-muted">
            <input
              type="checkbox"
              checked={isSecret}
              onChange={(event) => setIsSecret(event.target.checked)}
            />
            Секретная
          </label>
          <button
            type="button"
            onClick={() => {
              void create.mutateAsync({ title, is_secret: isSecret });
              setTitle("");
              setIsSecret(false);
            }}
            className="rounded-lg bg-white/15 px-4 py-2"
          >
            Создать
          </button>
```

`DEFAULT_CELLS` остаётся: страница библиотеки не знает про партию, и `players` там честно ноль — то самое вырождение в прежний строгий вердикт.

- [ ] **Step 11: Проверить доску до отправки**

В `frontend/src/pages/host-home/ui/home-page.tsx` — заменить `min={1}` на `min={3}` в разметке `<input type="number">`, и добавить перед `return` вычисление нарушенного правила:

```tsx
  const cells = width * height;
  const broken =
    width < 3 || height < 3
      ? "Сторона поля — не меньше 3 (§2.1)"
      : cells > 36
        ? `Клеток ${cells}, а больше 36 быть не может`
        : cells % players !== 0
          ? `${cells} не делится на ${players} игроков нацело`
          : null;
```

и подсказку рядом с кнопкой «Новая партия», внутри того же блока:

```tsx
        {broken && <p className="text-amber-300 text-sm">{broken}</p>}
```

Проверка остаётся серверной — ruling 4 в `MatchLifecycle.create` говорит, что API не перепроверяет домен; кнопка не блокируется, подсказка только называет правило.

- [ ] **Step 12: Прогнать фронт**

Run: `cd frontend && pnpm check && pnpm test`
Expected: PASS. Если падают старые тесты `useReadiness` с одним аргументом — второй параметр имеет значение по умолчанию, так что вызовы остаются валидными; падение здесь означает изменившийся URL в msw-хендлере, и его надо привести к `?cells=…&players=…`.

- [ ] **Step 13: Коммит**

```bash
git add backend/src/budge/api backend/tests/api frontend/src frontend/testing
git commit -m "fix: tell the operator what the library is missing"
```

---

### Task 7: Генератор картинок

Чистая функция без сети и без базы — единственная часть демо, которую можно проверить мгновенно.

**Files:**
- Create: `backend/src/budge/demo/__init__.py`
- Create: `backend/src/budge/demo/pictures.py`
- Test: `backend/tests/demo/__init__.py`, `backend/tests/demo/test_pictures.py` (создать)

**Interfaces:**
- Produces: `budge.demo.pictures.solid_png(colour: tuple[int, int, int], size: int = 512) -> bytes` и `budge.demo.pictures.PALETTE: tuple[tuple[str, tuple[int, int, int]], ...]` — пары «название цвета по-русски, RGB».

- [ ] **Step 1: Написать падающий тест**

Создать `backend/tests/demo/__init__.py` (пустой) и `backend/tests/demo/test_pictures.py`:

```python
"""§G.4: картинки генерируются из stdlib, и они должны быть настоящими PNG.

`sniff` — тот же самый детектор, которым `POST /api/media` решает, будет ли
он эти байты вообще хранить, так что он и есть оракул.
"""

import zlib

from budge.demo.pictures import PALETTE, solid_png
from budge.media.digest import digest_of, sniff


def test_a_generated_picture_is_a_png_the_media_route_will_accept() -> None:
    """Kills on: заголовок, собранный руками с ошибкой, — демо загрузило бы
    байты, которые `POST /api/media` отбивает с 415, и падало бы на первом
    же шаге, ничего не объяснив.
    """
    assert sniff(solid_png((255, 0, 0))) == "image/png"


def test_different_colours_are_different_bytes() -> None:
    """§G.4: на экране сцены должно быть видно, что кадр сменился.

    Kills on: генератор, игнорирующий цвет, — демо показывало бы одну и ту
    же картинку всю дуэль, и проверять было бы нечего.
    """
    digests = {digest_of(solid_png(rgb)) for _, rgb in PALETTE}
    assert len(digests) == len(PALETTE)


def test_the_pixels_are_the_colour_that_was_asked_for() -> None:
    """Заголовок может быть валиден, а содержимое — мусор. Распаковываем.

    PNG хранит скан-строки с байтом фильтра в начале каждой; при filter 0
    остальное — это RGB подряд.
    """
    size = 4
    data = solid_png((10, 20, 30), size=size)
    idat = b""
    offset = 8
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        if kind == b"IDAT":
            idat += data[offset + 8 : offset + 8 + length]
        offset += 12 + length
    raw = zlib.decompress(idat)
    assert len(raw) == size * (1 + size * 3)
    for row in range(size):
        line = raw[row * (1 + size * 3) : (row + 1) * (1 + size * 3)]
        assert line[0] == 0, "filter byte"
        assert line[1:] == bytes((10, 20, 30)) * size


def test_the_palette_names_its_colours_in_russian() -> None:
    """Название уходит в `answer_text`, который ведущий читает вслух."""
    assert len(PALETTE) >= 8
    for name, rgb in PALETTE:
        assert name.strip() != ""
        assert all(0 <= channel <= 255 for channel in rgb)
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && pytest tests/demo -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'budge.demo'`

- [ ] **Step 3: Написать генератор**

Создать `backend/src/budge/demo/__init__.py`:

```python
"""§G: демо-партия — команда, которая доводит пустую систему до партии, в
которой можно нажать «Начать».

Это не часть продукта: ни один роут и ни один модуль под `api/` сюда не
импортируется. Зависимость ровно обратная — демо ходит по HTTP.
"""
```

Создать `backend/src/budge/demo/pictures.py`:

```python
"""Solid-colour PNGs, written by hand out of the standard library.

§G.4: Pillow is not a dependency and will not become one for a demo. A PNG
of one flat colour is four chunks and a `zlib.compress`, which is less code
than the argument for adding an imaging library would be.

The colours differ on purpose. The demo exists so somebody can watch the
stage screen and see the picture change between judgements — a pack of
identical images would demonstrate the mechanic failing to be visible.
"""

import struct
import zlib

# Названия уходят в `answer_text`, который ведущий читает вслух, поэтому они
# по-русски и в именительном падеже — так же, как читался бы настоящий
# ответ.
PALETTE: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("Красный", (220, 60, 50)),
    ("Синий", (45, 110, 220)),
    ("Зелёный", (60, 175, 90)),
    ("Жёлтый", (225, 190, 60)),
    ("Фиолетовый", (150, 95, 210)),
    ("Оранжевый", (235, 135, 55)),
    ("Бирюзовый", (60, 190, 190)),
    ("Розовый", (230, 110, 170)),
    ("Коричневый", (140, 100, 70)),
    ("Серый", (140, 145, 150)),
)

_SIGNATURE = b"\x89PNG\r\n\x1a\x0a"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    """One PNG chunk: length, type, payload, CRC over type and payload."""
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def solid_png(colour: tuple[int, int, int], size: int = 512) -> bytes:
    """A `size` x `size` square of one colour, as PNG bytes.

    Truecolour, 8 bits per channel, no alpha, no interlace — the simplest
    thing `sniff` accepts and every browser draws.
    """
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    # Filter byte 0 ("none") in front of every scanline: with one flat
    # colour there is nothing for a filter to predict, and 0 keeps the
    # bytes readable by anything that decompresses them.
    row = b"\x00" + bytes(colour) * size
    return (
        _SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(row * size, 9))
        + _chunk(b"IEND", b"")
    )
```

- [ ] **Step 4: Прогнать**

Run: `cd backend && pytest tests/demo -q && ruff check && mypy`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add backend/src/budge/demo backend/tests/demo
git commit -m "feat: generate demo pictures without an imaging library"
```

---

### Task 8: Проход демо по API

**Files:**
- Create: `backend/src/budge/demo/seed.py`
- Test: `backend/tests/demo/test_seed.py` (создать)

**Interfaces:**
- Consumes: `solid_png`, `PALETTE` из задачи 7.
- Produces:
  - `budge.demo.seed.Caller` — Protocol с `async def call(self, method: str, path: str, *, json: object | None = None, body: bytes | None = None, content_type: str | None = None) -> tuple[int, object]`
  - `budge.demo.seed.DemoPlan(board: tuple[int, int], players: int, images: int, start: bool)`
  - `budge.demo.seed.DemoReport(match_id: str, stage_token: str, categories_created: int, images_created: int, started: bool)`
  - `async def run(caller: Caller, plan: DemoPlan) -> DemoReport`

- [ ] **Step 1: Написать падающий тест**

Создать `backend/tests/demo/test_seed.py`:

```python
"""§G: демо доходит до играбельной партии, и делает это по настоящим роутам.

Транспорт подставляется фальшивый — здесь проверяется порядок и состав
вызовов, а не сеть. Что этот же проход работает против живой системы,
доказывает интеграционный тест в `tests/api/test_demo_seed.py`.
"""

from typing import Any

import pytest

from budge.demo.seed import DemoPlan, run


class FakeApi:
    """The routes the demo touches, answering the way the real ones do."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.categories: list[dict[str, Any]] = []
        self.media: set[str] = set()
        self.images = 0
        self.secrets_assigned: list[str] = []
        self.dealt = False
        self.started = False

    async def call(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, object]:
        self.calls.append((method, path))
        if method == "GET" and path == "/api/library/categories":
            return 200, list(self.categories)
        if method == "POST" and path == "/api/media":
            assert body is not None
            digest = f"{len(self.media):064x}"
            self.media.add(digest)
            return 201, {"media_sha256": digest, "content_type": "image/png", "bytes": len(body)}
        if method == "POST" and path == "/api/library/categories":
            assert isinstance(json, dict)
            row = {
                "id": f"cat-{len(self.categories)}",
                "title": json["title"],
                "is_secret": json["is_secret"],
                "is_active": True,
                "version": 1,
                "active_image_count": 0,
            }
            self.categories.append(row)
            return 201, row
        if method == "POST" and path.endswith("/images"):
            self.images += 1
            return 201, {"id": f"img-{self.images}"}
        if method == "POST" and path == "/api/matches":
            return 201, {"outcome": "accepted", "match_id": "m-1", "stage_token": "tok"}
        if method == "POST" and path.endswith("/players"):
            return 200, {"outcome": "accepted"}
        if method == "POST" and path.endswith("/secrets"):
            assert isinstance(json, dict)
            self.secrets_assigned.append(str(json["category"]))
            return 200, {"outcome": "accepted"}
        if method == "POST" and path.endswith("/deal"):
            self.dealt = True
            return 200, {"outcome": "accepted"}
        if method == "POST" and path.endswith("/start"):
            self.started = True
            return 200, {"outcome": "accepted"}
        raise AssertionError(f"unexpected {method} {path}")


async def test_the_demo_reaches_a_dealt_match() -> None:
    api = FakeApi()
    report = await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    assert api.dealt is True
    assert api.started is False
    assert report.match_id == "m-1"
    assert report.stage_token == "tok"


async def test_start_is_pressed_only_when_asked() -> None:
    api = FakeApi()
    await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=True))
    assert api.started is True


async def test_every_player_gets_a_secret_of_their_own() -> None:
    """§2.3 и `AssignSecret`: две одинаковые категории — `duplicate_category`.

    Kills on: демо, назначающее один секрет всем, — `deal` отказал бы, и
    демо падало бы на предпоследнем шаге.
    """
    api = FakeApi()
    await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    assert len(api.secrets_assigned) == 3
    assert len(set(api.secrets_assigned)) == 3


async def test_the_board_gets_enough_ordinary_categories() -> None:
    """§G.5: доска 4×3 на троих требует 9 обычных; демо создаёт с запасом."""
    api = FakeApi()
    await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    ordinary = [row for row in api.categories if not row["is_secret"]]
    secrets = [row for row in api.categories if row["is_secret"]]
    assert len(ordinary) >= 12 - 3
    assert len(secrets) >= 3


async def test_no_category_is_left_without_pictures() -> None:
    """§8 называет исчерпание пачки в дуэли дефектом контента, и демо,
    производящее такой дефект, демонстрировало бы не ту механику.

    Kills on: картинки, добавленные только к обычным темам, — первая же
    атака на секрет упала бы с `content_unavailable` перед залом.
    """
    api = FakeApi()
    await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    assert api.images == len(api.categories) * 3


async def test_a_second_run_does_not_duplicate_the_library() -> None:
    """§G.6: идемпотентно по названию темы."""
    api = FakeApi()
    await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    after_first = len(api.categories)
    images_after_first = api.images
    second = await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    assert len(api.categories) == after_first
    assert api.images == images_after_first
    assert second.categories_created == 0
    assert second.images_created == 0


async def test_a_refused_command_stops_the_demo_loudly() -> None:
    """Демо, которое молча идёт дальше после отказа, оставляет оператора с
    партией в непонятном состоянии и без сообщения."""

    class RefusingApi(FakeApi):
        async def call(self, method: str, path: str, **kwargs: object) -> tuple[int, object]:
            if path.endswith("/deal"):
                return 409, {"outcome": "rejected", "reason": "secret_missing"}
            return await super().call(method, path, **kwargs)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="secret_missing"):
        await run(RefusingApi(), DemoPlan(board=(4, 3), players=3, images=3, start=False))
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && pytest tests/demo/test_seed.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'budge.demo.seed'`

- [ ] **Step 3: Написать проход**

Создать `backend/src/budge/demo/seed.py`:

```python
"""§G: the demo, as a sequence of calls to the real API.

The transport is a parameter, not an import. That is the whole reason the
`Caller` protocol exists: production supplies `urllib`, the tests supply
httpx over ASGI, and the walk itself — which is the part with the rules in
it — gets to be tested against a live PostgreSQL and MinIO in CI rather than
only run by hand.

Why HTTP at all, when this process could open the database: the library half
would indeed be cheap that way, but the match half goes through
`MatchManager`, `MatchHub`, `UnitOfWork` and `Materialiser`, and building
those here would duplicate the whole of `api/app.py`'s lifespan. A duplicate
service graph that drifts from the original at the first edit costs more
than any amount of convenience it buys — and going over HTTP means the demo
proves the operator's path works, not just that the tables can be filled.
"""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from budge.demo.pictures import PALETTE, solid_png

# Названия тем демо. Префикс общий и узнаваемый: §G.6 делает проход
# идемпотентным по названию, и оператор должен видеть, что именно демо
# положило в его библиотеку.
PREFIX = "Демо"

COLOURS = ("#e4572e", "#2e86e4", "#3fb950", "#d4a017", "#a371f7", "#e45ea0")


class Caller(Protocol):
    """One HTTP call, and what came back.

    Returns the status and the decoded body rather than raising: a 409 from
    a command route is an ordinary answer carrying a `reason` (§6.3), and a
    transport that raised on it would turn the domain's vocabulary into
    exceptions.
    """

    async def call(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, object]: ...


@dataclass(frozen=True, slots=True)
class DemoPlan:
    board: tuple[int, int]
    players: int
    images: int
    start: bool


@dataclass(frozen=True, slots=True)
class DemoReport:
    match_id: str
    stage_token: str
    categories_created: int
    images_created: int
    started: bool


class DemoFailed(RuntimeError):
    """A call the demo cannot continue past."""


async def _expect(
    caller: Caller,
    method: str,
    path: str,
    *,
    json: object | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
    accept: tuple[int, ...] = (200, 201),
) -> Any:
    status, payload = await caller.call(
        method, path, json=json, body=body, content_type=content_type
    )
    if status not in accept:
        raise DemoFailed(f"{method} {path} answered {status}: {payload}")
    # Команды отвечают конвертом исхода, и «принято» — не то же самое, что
    # «двести». Отказ здесь означает, что дальше идти бессмысленно.
    if isinstance(payload, dict) and payload.get("outcome") in {"rejected", "failed"}:
        raise DemoFailed(f"{method} {path} was refused: {payload.get('reason')}")
    return payload


async def _ensure_categories(
    caller: Caller, plan: DemoPlan
) -> tuple[list[str], list[str], int, int]:
    """§G.5 и §G.6: создать недостающие темы и наполнить их картинками.

    Возвращает id обычных и секретных тем плюс счётчики созданного, чтобы
    отчёт мог честно сказать «второй прогон не сделал ничего».
    """
    width, height = plan.board
    cells = width * height
    wanted_ordinary = [f"{PREFIX}: тема {index + 1}" for index in range(cells)]
    wanted_secret = [f"{PREFIX}: секрет {index + 1}" for index in range(plan.players + 1)]

    existing = {
        str(row["title"]): row
        for row in await _expect(caller, "GET", "/api/library/categories")
    }

    ordinary: list[str] = []
    secrets: list[str] = []
    categories_created = 0
    images_created = 0

    for title, is_secret in [(t, False) for t in wanted_ordinary] + [
        (t, True) for t in wanted_secret
    ]:
        found = existing.get(title)
        if found is not None:
            # §G.6: тема на месте — картинки к ней не досыпаются, иначе
            # каждый прогон растил бы пачку.
            (secrets if is_secret else ordinary).append(str(found["id"]))
            continue
        created = await _expect(
            caller,
            "POST",
            "/api/library/categories",
            json={"title": title, "is_secret": is_secret},
        )
        category_id = str(created["id"])
        categories_created += 1
        (secrets if is_secret else ordinary).append(category_id)
        for index in range(plan.images):
            name, rgb = PALETTE[(len(ordinary) + len(secrets) + index) % len(PALETTE)]
            stored = await _expect(
                caller,
                "POST",
                "/api/media",
                body=solid_png(rgb),
                content_type="image/png",
            )
            await _expect(
                caller,
                "POST",
                f"/api/library/categories/{category_id}/images",
                json={"media_sha256": str(stored["media_sha256"]), "answer_text": name},
            )
            images_created += 1

    return ordinary, secrets, categories_created, images_created


async def run(caller: Caller, plan: DemoPlan) -> DemoReport:
    """Пустая система на входе, партия с розданной доской на выходе."""
    _, secrets, categories_created, images_created = await _ensure_categories(caller, plan)

    width, height = plan.board
    created = await _expect(
        caller,
        "POST",
        "/api/matches",
        json={"board": {"width": width, "height": height}, "player_count": plan.players},
    )
    match_id = str(created["match_id"])

    for index in range(plan.players):
        player_id = str(uuid4())
        await _expect(
            caller,
            "POST",
            f"/api/matches/{match_id}/players",
            json={
                "player_id": player_id,
                "name": f"Игрок {index + 1}",
                "colour": COLOURS[index % len(COLOURS)],
            },
        )
        # Свой секрет каждому: `AssignSecret` отбивает повтор с
        # `duplicate_category`, и одна категория на всех уронила бы `deal`.
        await _expect(
            caller,
            "POST",
            f"/api/matches/{match_id}/secrets",
            json={"player_id": player_id, "category": secrets[index]},
        )

    await _expect(caller, "POST", f"/api/matches/{match_id}/deal")
    if plan.start:
        await _expect(caller, "POST", f"/api/matches/{match_id}/start")

    return DemoReport(
        match_id=match_id,
        stage_token=str(created["stage_token"]),
        categories_created=categories_created,
        images_created=images_created,
        started=plan.start,
    )
```

- [ ] **Step 4: Прогнать**

Run: `cd backend && pytest tests/demo -q && ruff check && mypy`
Expected: PASS, все семь тестов `test_seed.py`.

- [ ] **Step 5: Коммит**

```bash
git add backend/src/budge/demo/seed.py backend/tests/demo/test_seed.py
git commit -m "feat: walk the demo through the real API"
```

---

### Task 9: `budge seed-demo` и интеграционная проверка

Последняя задача связывает проход с транспортом, с CLI и с живой системой.

**Files:**
- Create: `backend/src/budge/demo/http.py`
- Modify: `backend/src/budge/cli.py`
- Test: `backend/tests/api/test_demo_seed.py` (создать)

**Interfaces:**
- Consumes: `run`, `DemoPlan`, `Caller` из задачи 8; `mint_session` из `budge.api.security`.
- Produces: `budge.demo.http.UrllibCaller(base_url: str, cookie: str)`, удовлетворяющий `Caller`; подкоманда `budge seed-demo`.

- [ ] **Step 1: Написать падающий интеграционный тест**

Создать `backend/tests/api/test_demo_seed.py`:

```python
"""§G: демо доходит до играбельной партии против живой системы.

Тот же проход, что в `tests/demo/test_seed.py`, но с настоящими PostgreSQL и
MinIO под ним — и потому это единственное место, где проверяется, что
`POST /api/media` принимает сгенерированные байты, а `deal` находит в базе
достаточно категорий.
"""

import httpx
import pytest

from budge.demo.seed import DemoPlan, run

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class HttpxCaller:
    """`Caller` over the ASGI app the API suite already stands up."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def call(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, object]:
        headers = {"content-type": content_type} if content_type else None
        response = await self._client.request(
            method, path, json=json, content=body, headers=headers
        )
        if not response.content:
            return response.status_code, None
        return response.status_code, response.json()


async def test_the_demo_leaves_a_running_match(host_client: httpx.AsyncClient) -> None:
    report = await run(
        HttpxCaller(host_client), DemoPlan(board=(4, 3), players=3, images=3, start=True)
    )
    snapshot = (await host_client.get(f"/api/matches/{report.match_id}")).json()
    assert snapshot["frame"]["status"] == "running"
    assert len(snapshot["frame"]["groups"]) == 12
    assert len(snapshot["frame"]["players"]) == 3


async def test_the_demo_leaves_the_library_ready(host_client: httpx.AsyncClient) -> None:
    """§B, проверенный тем самым вердиктом, который задача 5 научила
    считать секреты."""
    await run(HttpxCaller(host_client), DemoPlan(board=(4, 3), players=3, images=3, start=False))
    body = (await host_client.get("/api/library/readiness?cells=12&players=3")).json()
    assert body["ready"] is True


async def test_a_second_run_adds_no_categories(host_client: httpx.AsyncClient) -> None:
    caller = HttpxCaller(host_client)
    await run(caller, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    before = len((await host_client.get("/api/library/categories")).json())
    second = await run(caller, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    after = len((await host_client.get("/api/library/categories")).json())
    assert after == before
    assert second.categories_created == 0


async def test_the_demo_match_can_be_reset_and_dealt_again(
    host_client: httpx.AsyncClient,
) -> None:
    """§A вместе с §G: ради этой пары всё и делалось — партия, которую можно
    гонять по кругу, не собирая её заново."""
    report = await run(
        HttpxCaller(host_client), DemoPlan(board=(4, 3), players=3, images=3, start=True)
    )
    reset = await host_client.post(
        f"/api/matches/{report.match_id}/reset", json={"keep_roster": True}
    )
    assert reset.json()["outcome"] == "accepted"
    assert (await host_client.post(f"/api/matches/{report.match_id}/deal")).json()[
        "outcome"
    ] == "accepted"
    assert (await host_client.post(f"/api/matches/{report.match_id}/start")).json()[
        "outcome"
    ] == "accepted"
```

Имя фикстуры `host_client` взять то же, что используют `tests/api/test_match_routes.py` и `test_library_routes.py`; если она называется иначе — использовать существующее имя, а не заводить новую.

- [ ] **Step 2: Убедиться, что тест падает или проходит**

Run: `cd backend && pytest tests/api/test_demo_seed.py -q`
Expected: PASS — проход из задачи 8 уже написан, а транспорт здесь локальный. Если тесты падают, это настоящий дефект прохода: чинить `demo/seed.py`, а не тест. Ожидаемое место падения — формат `media_sha256`: `POST /api/media` возвращает digest, вычисленный сервером, и его надо передавать дословно.

- [ ] **Step 3: Написать транспорт для продакшена**

Создать `backend/src/budge/demo/http.py`:

```python
"""`Caller` over `urllib`, so the demo needs no HTTP dependency.

`httpx` is dev-only and `requests` is not a dependency at all; `urllib` is
six lines more and keeps the runtime image exactly as it is.

Blocking calls behind `asyncio.to_thread`, for the reason `S3MediaStore`
does the same: the walk is a few dozen sequential requests run by hand
between shows, so a thread per in-flight call costs nothing this deployment
can feel, and the loop is never blocked.
"""

import asyncio
import json as jsonlib
import urllib.error
import urllib.request
from typing import Any

from budge.api.principal import SESSION_COOKIE


class UrllibCaller:
    def __init__(self, base_url: str, session_cookie: str) -> None:
        self._base = base_url.rstrip("/")
        self._cookie = f"{SESSION_COOKIE}={session_cookie}"

    def _blocking_call(
        self,
        method: str,
        path: str,
        payload: bytes | None,
        content_type: str | None,
    ) -> tuple[int, object]:
        request = urllib.request.Request(
            f"{self._base}{path}", data=payload, method=method
        )
        request.add_header("Cookie", self._cookie)
        if content_type is not None:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return response.status, jsonlib.loads(raw) if raw else None
        except urllib.error.HTTPError as refused:
            # A 409 carrying a `reason` is an ordinary answer (§6.3), not an
            # exception — the walk decides what to do with it.
            raw = refused.read()
            body: Any = jsonlib.loads(raw) if raw else None
            return refused.code, body

    async def call(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, object]:
        payload = body
        kind = content_type
        if json is not None:
            payload = jsonlib.dumps(json).encode("utf-8")
            kind = "application/json"
        return await asyncio.to_thread(self._blocking_call, method, path, payload, kind)
```

- [ ] **Step 4: Добавить подкоманду**

В `backend/src/budge/cli.py` — парсер, рядом с прочими подкомандами:

```python
    seed = subcommands.add_parser(
        "seed-demo", help="fill the library and assemble a playable match (§G)"
    )
    seed.add_argument("--api", default="http://127.0.0.1:8000")
    seed.add_argument("--board", default="4x3", help="width x height, e.g. 4x3")
    seed.add_argument("--players", type=int, default=3)
    seed.add_argument("--images", type=int, default=3)
    seed.add_argument(
        "--start", action="store_true", help="press «Начать» too, not just deal"
    )
```

и ветка, перед финальным `return 1`:

```python
    if args.command == "seed-demo":
        # Imported here for the reason `serve`'s imports are: `migrate` must
        # not pull the API model tree in to run one Alembic command.
        import asyncio

        from budge.api.settings import ApiSettings
        from budge.api.security import mint_session
        from budge.demo.http import UrllibCaller
        from budge.demo.seed import DemoPlan, run
        from budge.runtime.clock import SystemClock

        width, _, height = args.board.partition("x")
        settings = ApiSettings()
        # §G.3: `BUDGE_HOST_PASSWORD` is a scrypt hash and cannot be logged
        # in with. The signing key can mint the same cookie the login route
        # mints, and holding it is already the operator's authority — it is
        # what signs stage links (§7.5).
        caller = UrllibCaller(
            args.api, mint_session(settings.secret_key, issued_at=SystemClock().now())
        )
        report = asyncio.run(
            run(
                caller,
                DemoPlan(
                    board=(int(width), int(height)),
                    players=args.players,
                    images=args.images,
                    start=args.start,
                ),
            )
        )
        print(f"партия: {args.api}/host/match/{report.match_id}")
        print(f"экран сцены: {args.api}/stage/{report.stage_token}")
        print(
            f"создано тем: {report.categories_created}, картинок: {report.images_created}"
        )
        return 0
```

Проверить точное имя класса часов в `budge/runtime/clock.py` и подставить его.

- [ ] **Step 5: Прогнать всё**

Run: `cd backend && ruff check && mypy && pytest -q`
Expected: PASS целиком.

- [ ] **Step 6: Проверить команду вживую**

```bash
docker compose up -d
docker compose exec api budge seed-demo --start
```

Expected: три строки вывода; по напечатанной ссылке на экран сцены видна доска, а в пульте — идущая партия. Если `api` не поднялся, сначала `docker compose logs api`.

- [ ] **Step 7: Записать команду в операционную документацию**

В `docs/operations.md` добавить раздел по образцу соседних:

```markdown
## Демо-партия

Наполнить пустую систему и собрать играбельную партию:

    docker compose exec api budge seed-demo --start

Команда идемпотентна по названию темы: второй прогон не удваивает
библиотеку, а собирает вторую партию. Флаги: `--board 4x3`, `--players 3`,
`--images 3`, `--start`.

Чтобы прогнать ту же партию ещё раз, не собирая её заново, в пульте есть
«Переиграть» (ростер и секреты остаются) и «Сбросить полностью».
```

- [ ] **Step 8: Коммит**

```bash
git add backend/src/budge backend/tests/api/test_demo_seed.py docs/operations.md
git commit -m "feat: assemble a playable demo match with one command"
```

---

## Проверка перед PR

- [ ] `cd backend && ruff check && mypy && pytest -q` — зелено
- [ ] `cd backend && budge export-types --check` — контракт не разошёлся
- [ ] `cd frontend && pnpm check && pnpm test` — зелено
- [ ] `docker compose exec api budge seed-demo --start` — партия собирается, «Переиграть» возвращает её в сборку
