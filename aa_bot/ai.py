"""Gemini: расшифровка голосовых и разбор ситуации по схеме 4 шага."""

import json
import logging
import re
from dataclasses import dataclass, field

from google import genai
from google.genai import errors, types

log = logging.getLogger(__name__)

FEELINGS = ["Обида", "Злость", "Страх", "Стыд", "Вина", "Тревога", "Одиночество", "Жалость к себе", "Ревность"]
AFFECTED = ["Самоуважение", "Амбиции", "Безопасность", "Сексуальные отношения", "Отношения с людьми", "Кошелёк"]
DEFECTS = ["Эгоизм", "Нечестность", "Корысть", "Страх"]

TRANSCRIBE_PROMPT = (
    "Расшифруй это голосовое сообщение дословно на русском языке. "
    "Верни только текст сообщения, без пояснений, заголовков и меток времени."
)

SYSTEM_PROMPT = f"""Ты помогаешь человеку, который проходит программу 12 шагов Анонимных Алкоголиков, \
разбирать ситуации по схеме 4 шага (таблица обид). Он присылает рассказ о ситуации своими словами, \
часто расшифрованный из голосового сообщения.

Разложи рассказ по колонкам таблицы обид:
- situation: коротко, за что обида или что произошло (до 80 символов).
- who: на кого обида или с кем ситуация, как назвал человек. Если это кто-то из списка известных людей, \
напиши имя точно как в списке.
- feelings, affected, defects: только значения из разрешённых списков.
- selfishness, dishonesty, self_seeking, fear: моя роль по каждому дефекту (эгоизм, нечестность, корысть, \
страх) от первого лица, словами человека: «Я считал, что…», «Я делал вид…», «Я хотел…», «Страх…».
- my_role: одной-двумя фразами, где была моя часть в ситуации.
- next_step: одно небольшое действие по программе, если человек сам его назвал или оно прямо следует \
из рассказа (помолиться за человека, позвонить спонсору, извиниться). Иначе пустая строка.
- questions: до трёх мягких открытых вопросов о том, чего не хватает для колонок. Например: \
«Чего ты хотел получить в этой ситуации?», «Где здесь была твоя часть?», «Чего ты боялся?». \
Если всё заполнено, пустой список.
- support_needed: true, если человек пишет о тяге выпить или употребить, о срыве, о мыслях навредить \
себе или о прямой опасности. Иначе false.

Правила:
- Используй только то, что человек сказал. Ничего не придумывай. Если колонка не раскрыта, оставь \
пустую строку и задай про неё вопрос.
- Не ставь диагнозов, не морализируй и не выноси вердиктов. Ты не заменяешь спонсора.
- Пиши по-русски.

Разрешённые чувства: {", ".join(FEELINGS)}.
Разрешённые сферы: {", ".join(AFFECTED)}.
Разрешённые дефекты: {", ".join(DEFECTS)}."""


def _enum_list(values: list[str]) -> dict:
    return {"type": "array", "items": {"type": "string", "enum": values}}


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "situation": {"type": "string"},
        "who": {"type": "string"},
        "feelings": _enum_list(FEELINGS),
        "affected": _enum_list(AFFECTED),
        "defects": _enum_list(DEFECTS),
        "selfishness": {"type": "string"},
        "dishonesty": {"type": "string"},
        "self_seeking": {"type": "string"},
        "fear": {"type": "string"},
        "my_role": {"type": "string"},
        "next_step": {"type": "string"},
        "questions": {"type": "array", "items": {"type": "string"}},
        "support_needed": {"type": "boolean"},
    },
    "required": [
        "situation",
        "who",
        "feelings",
        "affected",
        "defects",
        "selfishness",
        "dishonesty",
        "self_seeking",
        "fear",
        "my_role",
        "next_step",
        "questions",
        "support_needed",
    ],
}


PERSONALIZE_PROMPT = """Перед тобой текст молитвы. Подставь вместо «(имя)» {who}, поставив в падеж, \
который нужен по смыслу фразы. Если имён несколько, перечисли их через запятую, тоже в нужном падеже. \
Больше ничего в тексте не меняй: ни слов, ни порядка, ни знаков препинания, ни переносов строк. \
Верни только итоговый текст молитвы.

Молитва:
{template}"""

GRATITUDE_PROMPT = """Человек проходит программу АА и вечером пишет благодарности: \
пять Богу, четыре людям, три себе. Сейчас он заполняет раздел «{current}». \
Разбей его сообщение на отдельные благодарности и разложи по разделам:
- god: благодарность Богу, Высшей силе, жизни, за обстоятельства и события дня;
- people: благодарность конкретному человеку или людям (назван человек, роль или группа);
- self: благодарность себе, своим поступкам и усилиям («себе за то, что…», «я молодец, что…»).
Если раздел неясен, клади в «{current_key}». Каждую благодарность пиши коротко, словами человека, \
без «Спасибо» и «Благодарю» в начале, с маленькой буквы не начинай. Ничего не придумывай и не добавляй.

Сообщение:
{text}"""

GRATITUDE_SCHEMA = {
    "type": "object",
    "properties": {
        "god": {"type": "array", "items": {"type": "string"}},
        "people": {"type": "array", "items": {"type": "string"}},
        "self": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["god", "people", "self"],
}

GRATITUDE_HINT_PROMPT = """Человек проходит программу АА и вечером пишет благодарности: \
пять Богу, четыре людям, три себе. Сейчас ему не приходит в голову, что написать в раздел «{section}». \
Вот что было у него сегодня (записи из журнала и уже написанные благодарности):
{context}

Предложи три короткие подсказки-зацепки (до 12 слов каждая), за что он мог бы поблагодарить, \
опираясь на его день. Не повторяй уже написанное. Тепло, без пафоса, по-русски, на «ты» не обращайся: \
пиши как продолжение фразы «Благодарю…». Верни JSON-массив из трёх строк."""

SECTION_NAMES = {"god": "Богу", "people": "людям", "self": "себе"}

NAME_PLACEHOLDER = re.compile(r"\(\s*имя\s*\)", re.IGNORECASE)


class AIError(RuntimeError):
    pass


def has_name_placeholder(text: str) -> bool:
    return bool(NAME_PLACEHOLDER.search(text or ""))


def keeps_text(template: str, result: str) -> bool:
    """Весь текст молитвы вокруг «(имя)» сохранился дословно и в том же порядке."""
    position = 0
    for part in NAME_PLACEHOLDER.split(template):
        part = part.strip()
        if not part:
            continue
        found = result.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    return True


def fill_name(template: str, names: str) -> str:
    """Простая подстановка без склонения: запасной вариант, если Gemini недоступен."""
    return NAME_PLACEHOLDER.sub(names, template)


@dataclass
class Analysis:
    situation: str = ""
    who: str = ""
    feelings: list[str] = field(default_factory=list)
    affected: list[str] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)
    selfishness: str = ""
    dishonesty: str = ""
    self_seeking: str = ""
    fear: str = ""
    my_role: str = ""
    next_step: str = ""
    questions: list[str] = field(default_factory=list)
    support_needed: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "Analysis":
        def text(key: str) -> str:
            value = data.get(key)
            return value.strip() if isinstance(value, str) else ""

        def pick(key: str, allowed: list[str]) -> list[str]:
            values = data.get(key) or []
            return [v for v in dict.fromkeys(values) if v in allowed]

        questions = [q.strip() for q in data.get("questions") or [] if isinstance(q, str) and q.strip()]
        return cls(
            situation=text("situation"),
            who=text("who"),
            feelings=pick("feelings", FEELINGS),
            affected=pick("affected", AFFECTED),
            defects=pick("defects", DEFECTS),
            selfishness=text("selfishness"),
            dishonesty=text("dishonesty"),
            self_seeking=text("self_seeking"),
            fear=text("fear"),
            my_role=text("my_role"),
            next_step=text("next_step"),
            questions=questions[:3],
            support_needed=bool(data.get("support_needed")),
        )


class Assistant:
    def __init__(self, api_key: str, model: str, client: genai.Client | None = None):
        self._client = client or genai.Client(api_key=api_key)
        self._model = model

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=[types.Part.from_bytes(data=audio, mime_type=mime_type), TRANSCRIBE_PROMPT],
            )
        except errors.APIError as exc:
            raise AIError(f"Gemini не расшифровал голосовое: {exc}") from exc
        result = (response.text or "").strip()
        if not result:
            raise AIError("Gemini вернул пустую расшифровку")
        return result

    async def personalize(self, template: str, names: str) -> str:
        """Подставляет имя в молитву в правильном падеже. Если текст изменился сильнее, чем нужно, это ошибка."""
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model, contents=PERSONALIZE_PROMPT.format(who=names, template=template)
            )
        except errors.APIError as exc:
            raise AIError(f"Gemini не подставил имя: {exc}") from exc
        result = (response.text or "").strip()
        if not result or has_name_placeholder(result) or not keeps_text(template, result):
            raise AIError("Gemini изменил текст молитвы")
        return result

    async def sort_gratitude(self, text: str, current: str) -> dict[str, list[str]]:
        """Раскладывает сообщение с благодарностями по разделам god / people / self."""
        prompt = GRATITUDE_PROMPT.format(current=SECTION_NAMES[current], current_key=current, text=text)
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_json_schema=GRATITUDE_SCHEMA
                ),
            )
            data = json.loads(response.text or "")
        except errors.APIError as exc:
            raise AIError(f"Gemini не разобрал благодарности: {exc}") from exc
        except (json.JSONDecodeError, TypeError) as exc:
            raise AIError(f"Gemini вернул ответ не в том формате: {exc}") from exc
        result = {}
        for section in SECTION_NAMES:
            values = data.get(section) if isinstance(data, dict) else None
            result[section] = [v.strip() for v in values or [] if isinstance(v, str) and v.strip()]
        if not any(result.values()):
            raise AIError("Gemini не нашёл благодарностей в сообщении")
        return result

    async def gratitude_hints(self, section: str, context: str) -> list[str]:
        prompt = GRATITUDE_HINT_PROMPT.format(section=SECTION_NAMES[section], context=context or "записей нет")
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema={"type": "array", "items": {"type": "string"}},
                ),
            )
            data = json.loads(response.text or "")
        except errors.APIError as exc:
            raise AIError(f"Gemini не подсказал: {exc}") from exc
        except (json.JSONDecodeError, TypeError) as exc:
            raise AIError(f"Gemini вернул ответ не в том формате: {exc}") from exc
        hints = [h.strip() for h in data if isinstance(h, str) and h.strip()] if isinstance(data, list) else []
        if not hints:
            raise AIError("Gemini не подсказал")
        return hints[:3]

    async def analyze(self, story: str, known_people: list[str]) -> Analysis:
        known = ", ".join(known_people) if known_people else "нет"
        prompt = f"Известные люди: {known}.\n\nРассказ:\n{story}"
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_json_schema=ANALYSIS_SCHEMA,
                ),
            )
        except errors.APIError as exc:
            raise AIError(f"Gemini не разобрал ситуацию: {exc}") from exc
        try:
            return Analysis.from_dict(json.loads(response.text or ""))
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            raise AIError(f"Gemini вернул ответ не в том формате: {exc}") from exc
