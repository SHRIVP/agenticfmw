import sys
import textwrap

import mcp.types as mt
import pytest

from pcagent.config import PackRef
from pcagent.packs import Pack, PackError
from pcagent.packs import loader
from pcagent.packs.loader import open_packs, resolve_pack


@pytest.fixture
def mod(tmp_path, monkeypatch):
    def make(name, body):
        (tmp_path / f"{name}.py").write_text(textwrap.dedent(body))
        monkeypatch.syspath_prepend(str(tmp_path))
        sys.modules.pop(name, None)
        return name
    return make


def test_resolve_module_attr_instance(mod):
    m = mod("p_inst", "from pcagent.packs import Pack\npack = Pack('demo')\n")
    assert resolve_pack(PackRef(use=f"{m}:pack")).name == "demo"


def test_resolve_factory_gets_settings(mod):
    m = mod("p_fact", """
        from pcagent.packs import Pack
        def make_pack(settings):
            return Pack(settings['name'])
    """)
    assert resolve_pack(PackRef(use=f"{m}:make_pack", settings={"name": "zed"})).name == "zed"


def test_instance_with_settings_rejected(mod):
    m = mod("p_inst2", "from pcagent.packs import Pack\npack = Pack('demo')\n")
    with pytest.raises(PackError):
        resolve_pack(PackRef(use=f"{m}:pack", settings={"a": 1}))


def test_resolve_entry_point(monkeypatch):
    class EP:
        name = "fakepack"
        def load(self):
            return lambda settings: Pack("fake")
    monkeypatch.setattr(loader, "entry_points", lambda group: [EP()] if group == "pcagent.packs" else [])
    assert resolve_pack(PackRef(use="fakepack")).name == "fake"


def test_unknown_pack_rejected(monkeypatch):
    monkeypatch.setattr(loader, "entry_points", lambda group: [])
    with pytest.raises(PackError, match="nosuch"):
        resolve_pack(PackRef(use="nosuch"))


def demo_pack():
    p = Pack("demo")

    @p.tool(read_only=True)
    async def echo(text: str) -> str:
        """Echo text."""
        return text

    return p


def docs_pack(**kw):
    d = Pack("docs", labels={"get_claim_notes": "Read the claim notes"})
    d.mcp("claimdocs", command=sys.executable, args=["-m", "pcagent.packs.everywire.claimdocs"], **kw)
    return d


async def test_open_packs_python_and_mcp():
    async with open_packs([demo_pack(), docs_pack()]) as loaded:
        assert set(loaded.info) == {"demo__echo", "docs__get_claim_notes"}
        assert loaded.info["demo__echo"].source == "python"
        notes = loaded.info["docs__get_claim_notes"]
        assert (notes.source, notes.pack, notes.short_name) == ("mcp", "docs", "get_claim_notes")
        assert notes.label == "Read the claim notes" and notes.read_only is True
        tool = next(t for t in loaded.tools if t.name == "docs__get_claim_notes")
        assert "SYSTEM" in await tool.ainvoke({"policyNumber": "PA-200001-01"})


async def test_include_exclude():
    async with open_packs([docs_pack(exclude=["get_claim_notes"])]) as loaded:
        assert "docs__get_claim_notes" not in loaded.info


async def test_duplicate_pack_names_rejected():
    with pytest.raises(PackError, match="demo"):
        async with open_packs([demo_pack(), demo_pack()]):
            pass


async def test_bad_mcp_tool_name_rejected(monkeypatch):
    async def fake_list(session):
        return [mt.Tool(name="x" * 70, input_schema={"type": "object", "properties": {}})]
    monkeypatch.setattr(loader, "_list_tools", fake_list)
    with pytest.raises(PackError) as e:
        async with open_packs([docs_pack()]):
            pass
    assert "docs" in str(e.value) and "x" * 70 in str(e.value)
