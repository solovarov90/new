"""Действия помощника над базами Notion: найти, прочитать, создать, изменить, убрать в корзину.

Каждый метод возвращает простой словарь. Ошибки тоже возвращаются словарём {"error": ...},
чтобы модель могла прочитать причину и исправить запрос сама.
"""

import logging
from datetime import date

from . import entries
from . import notion as n
from .config import Config
from .notion import Notion, NotionError, read

log = logging.getLogger(__name__)

COLLECTIONS = {
    "inventory": "4 шаг: инвентаризация обид",
    "journal": "Журнал: разборы ситуаций",
    "people": "Люди: кого промаливать",
    "prayers": "Молитвы",
    "practices": "Практики",
    "checkin": "Ежедневный чек-ин",
}
WRITABLE = {"title", "rich_text", "select", "status", "multi_select", "number", "checkbox", "date"}
SHORT = 300


def _norm(page_id: str) -> str:
    return page_id.replace("-", "")


class Workspace:
    def __init__(self, notion: Notion, config: Config):
        self.notion = notion
        self.config = config
        self._schemas: dict[str, dict] = {}

    def _database(self, collection: str) -> str:
        if collection not in COLLECTIONS:
            raise ValueError(f"Нет такой базы {collection!r}. Есть: {', '.join(COLLECTIONS)}")
        return getattr(self.config.databases, collection)

    def _collection_of(self, page: dict) -> str | None:
        parent = _norm(page.get("parent", {}).get("database_id", ""))
        for collection in COLLECTIONS:
            if _norm(getattr(self.config.databases, collection)) == parent:
                return collection
        return None

    async def schema(self, collection: str) -> dict[str, dict]:
        """Свойства базы: тип и допустимые варианты для списков."""
        if collection not in self._schemas:
            data = await self.notion.get_database(self._database(collection))
            result = {}
            for name, prop in data["properties"].items():
                kind = prop["type"]
                info = {"type": kind}
                options = (prop.get(kind) or {}).get("options")
                if options:
                    info["options"] = [o["name"] for o in options]
                result[name] = info
            self._schemas[collection] = result
        return self._schemas[collection]

    def summarize(self, page: dict, full: bool = False) -> dict:
        fields = {}
        for name, prop in page.get("properties", {}).items():
            if prop["type"] not in WRITABLE:
                continue
            value = read(page, name)
            if value in (None, "", []):
                continue
            if isinstance(value, str) and not full and len(value) > SHORT:
                value = value[:SHORT] + "…"
            fields[name] = value
        return {"id": page["id"], "collection": self._collection_of(page), "fields": fields}

    def _properties(self, schema: dict[str, dict], fields: dict) -> dict:
        props = {}
        for name, value in fields.items():
            if name not in schema:
                raise ValueError(f"Нет поля {name!r}. Поля: {', '.join(schema)}")
            kind = schema[name]["type"]
            if kind not in WRITABLE:
                raise ValueError(f"Поле {name!r} заполняется само, его нельзя менять")
            props[name] = _to_property(kind, value)
        return props

    # --- инструменты ------------------------------------------------------------

    async def describe(self) -> dict:
        result = {}
        for collection, title in COLLECTIONS.items():
            schema = await self.schema(collection)
            result[collection] = {"title": title, "fields": schema}
        return result

    async def list_records(self, collection: str, contains: str = "", full: bool = False) -> dict:
        pages = await self.notion.query(self._database(collection))
        records = [self.summarize(p, full) for p in pages]
        if contains:
            needle = contains.lower()
            records = [r for r in records if needle in str(r["fields"]).lower()]
        return {"collection": collection, "count": len(records), "records": records}

    async def get_record(self, record_id: str) -> dict:
        page = await self.notion.get_page(record_id)
        record = self.summarize(page, full=True)
        record["text"] = await self.notion.page_text(record_id)
        return record

    async def create_record(self, collection: str, fields: dict, text: str = "") -> dict:
        props = self._properties(await self.schema(collection), fields)
        page = await self.notion.create_page(self._database(collection), props, text=text or None)
        return {"created": self.summarize(page)}

    async def update_record(self, record_id: str, fields: dict | None = None, append_text: str = "") -> dict:
        page = await self.notion.get_page(record_id)
        collection = self._collection_of(page)
        if collection is None:
            raise ValueError("Эта страница не из баз проекта")
        if fields:
            page = await self.notion.update_page(record_id, self._properties(await self.schema(collection), fields))
        if append_text:
            await self.notion.append_text(record_id, append_text)
        return {"updated": self.summarize(page)}

    async def archive_record(self, record_id: str) -> dict:
        page = await self.notion.get_page(record_id)
        if self._collection_of(page) is None:
            raise ValueError("Эта страница не из баз проекта")
        await self.notion.archive_page(record_id)
        return {"archived": record_id, "note": "В корзине Notion, восстановить можно 30 дней"}

    async def mark_prayed(self, person_id: str, today: date) -> dict:
        page = await self.notion.get_page(person_id)
        if entries.prayed_today(page, today.isoformat()):
            return {
                "person": read(page, "Имя"),
                "already_marked_today": True,
                "days": int(read(page, "Дней подряд") or 0),
            }
        props, days = entries.person_after_prayer(page, today.isoformat())
        page = await self.notion.update_page(person_id, props)
        return {"person": read(page, "Имя"), "days": days, "goal": 14}

    async def checkin(self, items: list[str], today: date) -> dict:
        unknown = [i for i in items if i not in entries.CHECKIN_FIELDS]
        if unknown:
            raise ValueError(f"Неизвестные пункты {unknown}. Есть: {', '.join(entries.CHECKIN_FIELDS)}")
        database = self.config.databases.checkin
        found = await self.notion.query(database, filter={"property": "Дата", "date": {"equals": today.isoformat()}})
        props = {}
        for item in items:
            props.update(entries.checkin_properties(item))
        if found:
            await self.notion.update_page(found[0]["id"], props)
        else:
            props["День"] = n.title(today.strftime("%d.%m.%Y"))
            props["Дата"] = n.date(today.isoformat())
            await self.notion.create_page(database, props)
        return {"marked": [entries.CHECKIN_FIELDS[i] for i in items], "date": today.isoformat()}

    async def overview(self, today: date) -> dict:
        """Короткая сводка по программе на сегодня."""
        dbs = self.config.databases
        inventory = await self.notion.query(dbs.inventory)
        journal = await self.notion.query(dbs.journal)
        people = await self.notion.query(dbs.people)
        checkin = await self.notion.query(
            dbs.checkin, filter={"property": "Дата", "date": {"equals": today.isoformat()}}
        )

        def by_status(pages, prop):
            counts: dict[str, int] = {}
            for p in pages:
                key = read(p, prop) or "без статуса"
                counts[key] = counts.get(key, 0) + 1
            return counts

        done_today = []
        if checkin:
            done_today = [label for label in entries.CHECKIN_FIELDS.values() if read(checkin[0], label)]
        result = {
            "today": today.isoformat(),
            "inventory": by_status(inventory, "Статус"),
            "journal": by_status(journal, "Статус"),
            "praying_now": [read(p, "Имя") for p in people if read(p, "Молитва") == "Молюсь"],
            "people": by_status(people, "Молитва"),
            "checkin_today": done_today,
        }
        if self.config.sobriety_date:
            result["sober_days"] = (today - self.config.sobriety_date).days
        return result


def _to_property(kind: str, value) -> dict:
    if kind == "title":
        return n.title(str(value))
    if kind == "rich_text":
        return n.text(str(value))
    if kind == "select":
        return n.select(str(value) if value else None)
    if kind == "status":
        return n.status(str(value))
    if kind == "multi_select":
        items = value if isinstance(value, list) else [v.strip() for v in str(value).split(",")]
        return n.multi([str(v) for v in items if str(v).strip()])
    if kind == "number":
        return n.number(float(value) if value not in (None, "") else None)
    if kind == "checkbox":
        if isinstance(value, str):
            value = value.strip().lower() in ("true", "да", "1", "yes")
        return n.checkbox(bool(value))
    if kind == "date":
        return n.date(str(value) if value else None)
    raise ValueError(f"Тип {kind} не поддерживается")


async def run_tool(workspace: Workspace, name: str, args: dict, today: date) -> tuple[dict, bool]:
    """Выполняет инструмент. Возвращает результат и признак того, что данные изменились."""
    tools = {
        "describe_collections": (lambda: workspace.describe(), False),
        "overview": (lambda: workspace.overview(today), False),
        "list_records": (
            lambda: workspace.list_records(args["collection"], args.get("contains", ""), bool(args.get("full"))),
            False,
        ),
        "get_record": (lambda: workspace.get_record(args["record_id"]), False),
        "create_record": (
            lambda: workspace.create_record(args["collection"], args.get("fields") or {}, args.get("text", "")),
            True,
        ),
        "update_record": (
            lambda: workspace.update_record(args["record_id"], args.get("fields"), args.get("append_text", "")),
            True,
        ),
        "archive_record": (lambda: workspace.archive_record(args["record_id"]), True),
        "mark_prayed": (lambda: workspace.mark_prayed(args["person_id"], today), True),
        "checkin": (lambda: workspace.checkin(list(args.get("items") or []), today), True),
    }
    if name not in tools:
        return {"error": f"Нет инструмента {name}"}, False
    action, changes = tools[name]
    try:
        return await action(), changes
    except KeyError as exc:
        return {"error": f"Не хватает параметра {exc}"}, False
    except (ValueError, TypeError) as exc:
        return {"error": str(exc)}, False
    except NotionError as exc:
        log.warning("Инструмент %s: %s", name, exc)
        return {"error": f"Notion ответил ошибкой: {exc}"}, False
