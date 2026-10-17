"""Небольшой асинхронный клиент Notion API и помощники для свойств страниц."""

import asyncio
import logging

import httpx

API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
TEXT_LIMIT = 2000  # Notion принимает не больше 2000 символов в одном куске текста
BLOCKS_PER_REQUEST = 100

log = logging.getLogger(__name__)

TEXT_BLOCKS = (
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "quote",
    "callout",
    "to_do",
    "toggle",
)


class NotionError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Notion:
    def __init__(self, token: str, http: httpx.AsyncClient | None = None):
        self._http = http or httpx.AsyncClient(base_url=API_URL, timeout=30)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
        }

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        for attempt in range(5):
            try:
                resp = await self._http.request(method, path, json=body, headers=self._headers)
            except httpx.TransportError as exc:
                log.warning("Notion %s %s: %s", method, path, exc)
                await asyncio.sleep(2**attempt)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                delay = float(resp.headers.get("Retry-After", 2**attempt))
                log.warning("Notion %s %s: %s, повтор через %.0f с", method, path, resp.status_code, delay)
                await asyncio.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise NotionError(f"{method} {path}: {resp.status_code} {resp.text[:500]}", resp.status_code)
            return resp.json()
        raise NotionError(f"{method} {path}: Notion не отвечает")

    async def query(self, database_id: str, filter: dict | None = None, sorts: list | None = None) -> list[dict]:
        pages, cursor = [], None
        while True:
            body: dict = {"page_size": 100}
            if filter:
                body["filter"] = filter
            if sorts:
                body["sorts"] = sorts
            if cursor:
                body["start_cursor"] = cursor
            data = await self._request("POST", f"/databases/{database_id}/query", body)
            pages.extend(data["results"])
            if not data.get("has_more"):
                return pages
            cursor = data["next_cursor"]

    async def get_page(self, page_id: str) -> dict:
        return await self._request("GET", f"/pages/{page_id}")

    async def get_database(self, database_id: str) -> dict:
        return await self._request("GET", f"/databases/{database_id}")

    async def archive_page(self, page_id: str) -> dict:
        """Переносит страницу в корзину Notion. Оттуда её можно восстановить в течение 30 дней."""
        return await self._request("PATCH", f"/pages/{page_id}", {"archived": True})

    async def page_text(self, page_id: str) -> str:
        lines, cursor = [], None
        while True:
            path = f"/blocks/{page_id}/children?page_size=100"
            if cursor:
                path += f"&start_cursor={cursor}"
            data = await self._request("GET", path)
            for block in data["results"]:
                kind = block.get("type")
                if kind in TEXT_BLOCKS:
                    lines.append(plain(block[kind].get("rich_text", [])))
            if not data.get("has_more"):
                return "\n".join(lines).strip()
            cursor = data["next_cursor"]

    async def create_page(self, database_id: str, properties: dict, text: str | None = None) -> dict:
        blocks = paragraphs(text or "")
        body = {
            "parent": {"database_id": database_id},
            "properties": properties,
            "children": blocks[:BLOCKS_PER_REQUEST],
        }
        page = await self._request("POST", "/pages", body)
        if len(blocks) > BLOCKS_PER_REQUEST:
            await self._append_blocks(page["id"], blocks[BLOCKS_PER_REQUEST:])
        return page

    async def update_page(self, page_id: str, properties: dict) -> dict:
        return await self._request("PATCH", f"/pages/{page_id}", {"properties": properties})

    async def append_text(self, page_id: str, text: str) -> None:
        await self._append_blocks(page_id, paragraphs(text))

    async def _append_blocks(self, page_id: str, blocks: list[dict]) -> None:
        for start in range(0, len(blocks), BLOCKS_PER_REQUEST):
            chunk = blocks[start : start + BLOCKS_PER_REQUEST]
            await self._request("PATCH", f"/blocks/{page_id}/children", {"children": chunk})


# --- запись свойств ---------------------------------------------------------


def _chunks(text: str) -> list[str]:
    return [text[i : i + TEXT_LIMIT] for i in range(0, len(text), TEXT_LIMIT)] or [""]


def rich_text(text: str) -> list[dict]:
    if not text:
        return []
    return [{"type": "text", "text": {"content": chunk}} for chunk in _chunks(text)]


def title(text: str) -> dict:
    return {"title": rich_text(text)}


def text(value: str) -> dict:
    return {"rich_text": rich_text(value)}


def select(name: str | None) -> dict:
    return {"select": {"name": name} if name else None}


def status(name: str) -> dict:
    return {"status": {"name": name}}


def multi(names: list[str]) -> dict:
    return {"multi_select": [{"name": n} for n in names]}


def number(value: float | None) -> dict:
    return {"number": value}


def checkbox(value: bool) -> dict:
    return {"checkbox": bool(value)}


def date(iso: str | None) -> dict:
    return {"date": {"start": iso} if iso else None}


def paragraphs(text: str) -> list[dict]:
    blocks = []
    for part in text.split("\n"):
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(part)}})
    return blocks if text.strip() else []


# --- чтение свойств ---------------------------------------------------------


def plain(items: list[dict]) -> str:
    return "".join(item.get("plain_text") or item.get("text", {}).get("content", "") for item in items)


def read(page: dict, name: str):
    """Значение свойства страницы в простом виде: строка, список строк, число или bool."""
    prop = page.get("properties", {}).get(name)
    if not prop:
        return None
    kind = prop["type"]
    value = prop.get(kind)
    if kind in ("title", "rich_text"):
        return plain(value or [])
    if kind in ("select", "status"):
        return value["name"] if value else ""
    if kind == "multi_select":
        return [option["name"] for option in value or []]
    if kind == "date":
        return value["start"] if value else ""
    if kind in ("number", "checkbox", "created_time", "last_edited_time", "url", "email", "phone_number"):
        return value
    if kind == "formula":
        return value.get(value.get("type")) if value else None
    return None
