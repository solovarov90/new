"""Помощник для разговора: Gemini с инструментами над базами Notion."""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from html import escape

from google import genai
from google.genai import errors, types

from .ai import AIError
from .config import Config
from .entries import CHECKIN_FIELDS, plural_days
from .state import State
from .workspace import COLLECTIONS, Workspace, run_tool

log = logging.getLogger(__name__)

HISTORY_KEY = "agent:history"
HISTORY_TURNS = 30
MAX_STEPS = 10
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

SYSTEM_PROMPT = f"""Ты личный помощник человека, который проходит программу 12 шагов Анонимных Алкоголиков. \
Вы общаетесь в его закрытой Telegram-группе. Ты ведёшь вместе с ним его проект в Notion: инвентаризацию \
обид (4 шаг), журнал разборов, список людей, за которых он молится, молитвы, практики и ежедневный чек-ин.

Как ты помогаешь:
- Разговариваешь тепло и по-товарищески, на «ты», по-русски, коротко. Как трезвый друг по программе, \
а не как психолог. Не ставишь диагнозов, не морализируешь, не выносишь приговоров.
- Ты не заменяешь спонсора, группу и врачей. Когда уместно, мягко напоминаешь про спонсора и собрания.
- Сам выполняешь просьбы через инструменты: найти, показать, добавить, исправить, отметить. Не говори, \
что сделал, пока инструмент не вернул успех. После действия коротко скажи, что изменилось.
- Прежде чем менять или удалять запись, найди её через list_records или get_record. Никогда не выдумывай id.
- В записях инвентаризации и журнала сохраняй слова человека. Переписывай их только по его просьбе.
- Убирать запись в корзину (archive_record) можно только после явного согласия человека в этом разговоре.
- Разбор ситуации веди по схеме 4 шага: на кого обида и за что, что задето (самоуважение, амбиции, \
безопасность, сексуальные отношения, отношения с людьми, кошелёк), моя роль: эгоизм, нечестность, корысть, \
страх. Задавай по одному вопросу. Когда всё ясно, предложи записать в журнал или в инвентаризацию \
и запиши, если он согласится.
- Замечай повторяющиеся страхи и дефекты в инвентаризации, если он просит или это помогает разговору. \
Это материал для 6–7 шагов и для разговора со спонсором.
- Сообщения приходят из разных тем группы, в начале стоит пометка темы. Понимай просьбу по теме: короткое «давай» в «Промаливании» — это «начнём молиться», а не имя человека. Если не ясно, что он хочет, переспроси одним вопросом, а не делай наугад.
- Промаливание запускай инструментом start_people_prayer, благодарности — open_gratitude. После запуска ответь одной короткой фразой: молитва уже в теме.
- Молитвы из базы присылай целиком и точно, ничего не меняя.
- Вечером он пишет благодарности: пять Богу, четыре людям, три себе. Они ведутся в теме «🌙 Вечер» \
по кнопке «✨ Благодарности» или командой /thanks и сами попадают в чек-ин дня. \
Если он хочет поблагодарить или ему тяжело вечером, можешь предложить это, а если просит, помоги найти, за что.
- Если он пишет о тяге выпить или употребить, о срыве, о мыслях навредить себе или об опасности, \
сначала поддержи: пауза и несколько вдохов, молитва о душевном покое, позвонить спонсору или товарищу, \
собрание. При мыслях навредить себе или прямой опасности назови экстренные номера: 115 во Вьетнаме \
(скорая), 112 в России.

Оформление для Telegram: короткие абзацы, списки через «- », можно **жирный**. Без таблиц и заголовков.

Базы (параметр collection): {", ".join(f"{k} — {v}" for k, v in COLLECTIONS.items())}.
Пункты чек-ина (параметр items): {", ".join(f"{k} — {v}" for k, v in CHECKIN_FIELDS.items())}.
Если не знаешь точных названий полей базы, вызови describe_collections."""


def _schema(properties: dict, required: list[str]) -> dict:
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


COLLECTION = {"type": "string", "enum": list(COLLECTIONS)}
FIELDS = {
    "type": "object",
    "description": "Поля записи: {название поля: значение}. Списки с несколькими вариантами передавай массивом строк.",
}

TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="describe_collections",
                description="Поля всех баз: типы и допустимые варианты для списков.",
                parameters_json_schema=_schema({}, []),
            ),
            types.FunctionDeclaration(
                name="overview",
                description="Сводка на сегодня: дни трезвости, статусы инвентаризации и журнала, за кого идёт молитва, отметки чек-ина.",
                parameters_json_schema=_schema({}, []),
            ),
            types.FunctionDeclaration(
                name="list_records",
                description="Записи базы. contains отбирает записи, где встречается текст. full=true возвращает длинные поля целиком.",
                parameters_json_schema=_schema(
                    {"collection": COLLECTION, "contains": {"type": "string"}, "full": {"type": "boolean"}},
                    ["collection"],
                ),
            ),
            types.FunctionDeclaration(
                name="get_record",
                description="Одна запись целиком, вместе с текстом страницы (у молитв там текст молитвы, у журнала рассказ).",
                parameters_json_schema=_schema({"record_id": {"type": "string"}}, ["record_id"]),
            ),
            types.FunctionDeclaration(
                name="create_record",
                description="Создать запись в базе. text попадает в тело страницы.",
                parameters_json_schema=_schema(
                    {"collection": COLLECTION, "fields": FIELDS, "text": {"type": "string"}},
                    ["collection", "fields"],
                ),
            ),
            types.FunctionDeclaration(
                name="update_record",
                description="Изменить поля записи. Меняются только переданные поля. append_text дописывает текст в тело страницы.",
                parameters_json_schema=_schema(
                    {"record_id": {"type": "string"}, "fields": FIELDS, "append_text": {"type": "string"}},
                    ["record_id"],
                ),
            ),
            types.FunctionDeclaration(
                name="archive_record",
                description="Убрать запись в корзину Notion. Только после явного согласия человека.",
                parameters_json_schema=_schema({"record_id": {"type": "string"}}, ["record_id"]),
            ),
            types.FunctionDeclaration(
                name="mark_prayed",
                description="Отметить, что сегодня помолился за человека из базы people: счётчик дней +1, через 14 дней молитва завершена.",
                parameters_json_schema=_schema({"person_id": {"type": "string"}}, ["person_id"]),
            ),
            types.FunctionDeclaration(
                name="start_people_prayer",
                description=(
                    "Запустить промаливание: в теме «Промаливание» появится молитва с именем первого, за кого сегодня "
                    "ещё не молился, и кнопка «Помолился · дальше». person_id — начать с конкретного человека."
                ),
                parameters_json_schema=_schema({"person_id": {"type": "string"}}, []),
            ),
            types.FunctionDeclaration(
                name="open_gratitude",
                description="Открыть экран вечерних благодарностей 5·4·3 (Богу, людям, себе) в теме «Вечер».",
                parameters_json_schema=_schema({}, []),
            ),
            types.FunctionDeclaration(
                name="checkin",
                description="Отметить пункты сегодняшнего чек-ина.",
                parameters_json_schema=_schema(
                    {"items": {"type": "array", "items": {"type": "string", "enum": list(CHECKIN_FIELDS)}}},
                    ["items"],
                ),
            ),
        ]
    )
]


@dataclass
class Reply:
    text: str
    changed: bool


class Agent:
    def __init__(self, client: genai.Client, model: str, workspace: Workspace, state: State, config: Config):
        self._client = client
        self._model = model
        self._workspace = workspace
        self._state = state
        self._config = config
        # Действия в Telegram (запустить промаливание, открыть благодарности). Их задают обработчики.
        self.actions: dict[str, Callable[[dict], Awaitable[dict]]] = {}

    def history(self) -> list[dict]:
        raw = self._state.get(HISTORY_KEY)
        return json.loads(raw) if raw else []

    def reset(self) -> None:
        self._state.delete(HISTORY_KEY)

    def _remember(self, user_text: str, answer: str) -> None:
        turns = self.history() + [{"role": "user", "text": user_text}, {"role": "model", "text": answer}]
        self._state.set(HISTORY_KEY, json.dumps(turns[-HISTORY_TURNS * 2 :], ensure_ascii=False))

    def _context(self, now: datetime) -> str:
        line = f"[Сейчас {now.strftime('%d.%m.%Y %H:%M')}, {WEEKDAYS[now.weekday()]}, время Нячанга."
        if self._config.sobriety_date:
            line += f" Трезвость: {plural_days((now.date() - self._config.sobriety_date).days)}."
        if self._config.sponsor_contact:
            line += f" Спонсор: {self._config.sponsor_contact}."
        return line + "]"

    async def reply(self, user_text: str, now: datetime, card: str | None = None) -> Reply:
        message = user_text if card is None else f"{card}\n{user_text}"
        contents = [types.Content(role=turn["role"], parts=[types.Part(text=turn["text"])]) for turn in self.history()]
        contents.append(types.Content(role="user", parts=[types.Part(text=f"{self._context(now)}\n{message}")]))
        config = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, tools=TOOLS)
        changed = False
        answer = None
        for _ in range(MAX_STEPS):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model, contents=contents, config=config
                )
            except errors.APIError as exc:
                raise AIError(f"Gemini не ответил: {exc}") from exc
            calls = response.function_calls or []
            if not calls:
                answer = (response.text or "").strip()
                break
            contents.append(response.candidates[0].content)
            parts = []
            for call in calls:
                args = dict(call.args or {})
                if call.name in self.actions:
                    result, did_change = await self._action(call.name, args), False
                else:
                    result, did_change = await run_tool(self._workspace, call.name, args, now.date())
                changed = changed or (did_change and "error" not in result)
                log.info("Инструмент %s: %s", call.name, "ошибка" if "error" in result else "ok")
                parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=call.id, name=call.name, response={"result": result}
                        )
                    )
                )
            contents.append(types.Content(role="user", parts=parts))
        if answer is None:
            answer = "Я сделал несколько шагов, но не успел закончить ответ. Напиши, что проверить или продолжить."
        elif not answer:
            answer = "Не получилось ответить. Попробуй сказать иначе."
        self._remember(message, answer)
        return Reply(answer, changed)

    async def _action(self, name: str, args: dict) -> dict:
        try:
            return await self.actions[name](args)
        except Exception as exc:  # модель должна прочитать причину, а не уронить разговор
            log.exception("Действие %s", name)
            return {"error": str(exc) or type(exc).__name__}


def to_html(text: str) -> str:
    """Простая разметка модели (**жирный**, `код`, списки) в HTML для Telegram."""
    html = escape(text)
    html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html, flags=re.DOTALL)
    html = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", html)
    html = re.sub(r"^\s*[-*]\s+", "• ", html, flags=re.MULTILINE)
    html = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", html, flags=re.MULTILINE)
    return html


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Делит длинный ответ по абзацам, чтобы каждая часть влезла в сообщение Telegram."""
    chunks, current = [], ""
    for paragraph in text.split("\n"):
        while len(paragraph) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        candidate = f"{current}\n{paragraph}" if current else paragraph
        if len(candidate) > limit:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]
