from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from google.genai import types

from aa_bot import notion as n
from aa_bot.agent import Agent, split_message, to_html
from aa_bot.config import Config, Databases
from aa_bot.state import State
from aa_bot.workspace import Workspace, run_tool

from .fakes import FakeNotion, page

DBS = Databases(inventory="inv", journal="jr", people="ppl", prayers="pr", practices="pc", checkin="ch")
TODAY = date(2026, 10, 17)
SCHEMAS = {
    "inv": {
        "За что (причина)": {"type": "title", "title": {}},
        "Кто": {"type": "select", "select": {"options": [{"name": "Отец"}, {"name": "Дядя"}]}},
        "Корысть": {"type": "rich_text", "rich_text": {}},
        "Задеты": {"type": "multi_select", "multi_select": {"options": [{"name": "Кошелёк"}]}},
        "Создано": {"type": "created_time", "created_time": {}},
    },
    "ppl": {
        "Имя": {"type": "title", "title": {}},
        "Дней подряд": {"type": "number", "number": {}},
        "Молитва": {"type": "status", "status": {"options": [{"name": "Молюсь"}]}},
        "Начало": {"type": "date", "date": {}},
    },
}


def make_config() -> Config:
    return Config(
        bot_token="t",
        chat_id=-1,
        owner_id=1,
        notion_token="n",
        databases=DBS,
        gemini_api_key="g",
        gemini_model="m",
        timezone="Asia/Ho_Chi_Minh",
        morning_time="06:30",
        evening_time="22:30",
        sobriety_date=date(2026, 6, 15),
        sponsor_contact=None,
        sync_interval=60,
        state_path=":memory:",
    )


@pytest.fixture
def notion():
    uncle = page(
        "u1",
        parent="inv",
        **{
            "За что (причина)": ("title", "Не помог"),
            "Кто": ("select", "Дядя"),
            "Корысть": ("rich_text", "Получить поддержку"),
        },
    )
    father = page("f1", parent="ppl", Имя=("title", "Отец"))
    return FakeNotion({"inv": [uncle], "ppl": [father]}, bodies={"u1": "рассказ"}, schemas=SCHEMAS)


async def test_update_record_converts_fields_by_schema(notion):
    workspace = Workspace(notion, make_config())
    result, changed = await run_tool(
        workspace,
        "update_record",
        {"record_id": "u1", "fields": {"Корысть": "Хотел, чтобы меня пожалели", "Задеты": ["Кошелёк"]}},
        TODAY,
    )
    assert changed and "updated" in result
    page_id, props = notion.updated[-1]
    assert page_id == "u1"
    assert props["Корысть"] == n.text("Хотел, чтобы меня пожалели")
    assert props["Задеты"] == n.multi(["Кошелёк"])


async def test_unknown_field_returns_error_instead_of_writing(notion):
    workspace = Workspace(notion, make_config())
    result, changed = await run_tool(workspace, "update_record", {"record_id": "u1", "fields": {"Эмоция": "x"}}, TODAY)
    assert not changed and "Нет поля" in result["error"]
    assert notion.updated == []


async def test_computed_field_cannot_be_written(notion):
    workspace = Workspace(notion, make_config())
    result, _ = await run_tool(
        workspace, "create_record", {"collection": "inventory", "fields": {"Создано": "x"}}, TODAY
    )
    assert "заполняется само" in result["error"]


async def test_list_records_filters_by_text(notion):
    workspace = Workspace(notion, make_config())
    result, changed = await run_tool(workspace, "list_records", {"collection": "inventory", "contains": "дяд"}, TODAY)
    assert not changed and result["count"] == 1
    assert result["records"][0]["fields"]["Кто"] == "Дядя"


async def test_mark_prayed_counts_day(notion):
    workspace = Workspace(notion, make_config())
    result, changed = await run_tool(workspace, "mark_prayed", {"person_id": "f1"}, TODAY)
    assert changed and result["days"] == 1
    assert notion.updated[-1][1]["Начало"] == n.date("2026-10-17")


async def test_archive_only_records_from_project(notion):
    workspace = Workspace(notion, make_config())
    outside = page("x1", parent="somewhere-else")
    notion.databases["other"] = [outside]
    result, changed = await run_tool(workspace, "archive_record", {"record_id": "x1"}, TODAY)
    assert not changed and "error" in result and notion.archived == []


def _call(name, args):
    return SimpleNamespace(
        function_calls=[SimpleNamespace(id="c1", name=name, args=args)],
        candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part(text="…")]))],
        text=None,
    )


def _text(value):
    return SimpleNamespace(function_calls=None, candidates=[], text=value)


class FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def generate_content(self, model, contents, config):
        self.requests.append(list(contents))
        return self.responses.pop(0)


async def test_agent_runs_tools_then_answers_and_remembers(notion, tmp_path):
    models = FakeModels(
        [
            _call("list_records", {"collection": "inventory", "contains": "дядя"}),
            _call("update_record", {"record_id": "u1", "fields": {"Корысть": "Хотел жалости"}}),
            _text("Готово, поправил **корысть** у обиды на дядю."),
        ]
    )
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    state = State(str(tmp_path / "s.db"))
    agent = Agent(client, "m", Workspace(notion, make_config()), state, make_config())

    now = datetime(2026, 10, 17, 20, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    reply = await agent.reply("перепиши корысть у дяди: хотел жалости", now)

    assert reply.changed
    assert "корысть" in reply.text
    assert notion.updated[-1][1]["Корысть"] == n.text("Хотел жалости")
    first_prompt = models.requests[0][-1].parts[0].text
    assert "Трезвость: 124 дня" in first_prompt
    # Результат инструмента вернулся модели с тем же id вызова.
    tool_answer = models.requests[1][-1].parts[0].function_response
    assert tool_answer.id == "c1" and tool_answer.response["result"]["count"] == 1
    assert [t["role"] for t in agent.history()] == ["user", "model"]


async def test_agent_reads_without_marking_changes(notion, tmp_path):
    models = FakeModels([_call("overview", {}), _text("Сегодня 124 дня трезвости.")])
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    agent = Agent(client, "m", Workspace(notion, make_config()), State(str(tmp_path / "s.db")), make_config())
    reply = await agent.reply("как я?", datetime(2026, 10, 17, 8, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))
    assert not reply.changed


def test_to_html_converts_simple_markdown_and_escapes():
    assert to_html("**Важно** <x>\n- пункт") == "<b>Важно</b> &lt;x&gt;\n• пункт"


def test_split_message_keeps_parts_under_limit():
    parts = split_message("абзац\n" * 3000, limit=4000)
    assert len(parts) > 1 and all(len(p) <= 4000 for p in parts)
