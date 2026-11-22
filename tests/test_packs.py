import asyncio
import time

import pytest
from langchain_core.tools import ToolException

from pcagent.packs import Pack, PackError, python_langchain_tool, qualify


def test_pack_name_validation():
    for bad in ("Bad-Name", "a" * 21, "a__b", ""):
        with pytest.raises(PackError):
            Pack(bad)
    assert Pack("billing").name == "billing"


def test_qualify_limits():
    assert qualify("billing", "get_balance") == "billing__get_balance"
    with pytest.raises(PackError):
        qualify("billing", "x" * 56)
    with pytest.raises(PackError):
        qualify("billing", "bad.name")


def test_tool_requires_docstring():
    p = Pack("demo")
    with pytest.raises(PackError):
        @p.tool()
        async def nodoc(x: str) -> str:
            return x


def test_read_only_and_destructive_conflict():
    p = Pack("demo")
    with pytest.raises(PackError):
        @p.tool(read_only=True, destructive=True)
        async def both(x: str) -> str:
            """Both."""
            return x


def test_destructive_defaults_to_not_read_only():
    p = Pack("demo")

    @p.tool()
    async def write(x: str) -> str:
        """Write."""
        return x

    @p.tool(read_only=True)
    async def read(x: str) -> str:
        """Read."""
        return x

    assert p.python_tools["write"].destructive is True
    assert p.python_tools["read"].destructive is False
    assert p.python_tools["read"].read_only is True


def _billing():
    p = Pack("billing", labels={"get_balance": "Looked up the balance"})

    @p.tool(read_only=True)
    async def get_balance(account_number: str) -> str:
        """Current balance for an account."""
        return f"ok:{account_number}"

    @p.tool(read_only=True)
    def get_sync(account_number: str) -> str:
        """Sync lookup."""
        return f"sync:{account_number}"

    @p.tool()
    def explode(account_number: str) -> str:
        """Always fails."""
        raise KeyError("x")

    return p


async def test_python_tool_runs_async_and_sync():
    p = _billing()
    tool, info = python_langchain_tool(p, p.python_tools["get_balance"])
    assert await tool.ainvoke({"account_number": "A1"}) == "ok:A1"
    assert info.name == tool.name == "billing__get_balance"
    assert (info.pack, info.short_name, info.source) == ("billing", "get_balance", "python")
    assert info.label == "Looked up the balance"
    assert info.description == "Current balance for an account."
    sync_tool, _ = python_langchain_tool(p, p.python_tools["get_sync"])
    assert await sync_tool.ainvoke({"account_number": "A1"}) == "sync:A1"


async def test_python_tool_exception_becomes_toolexception():
    p = _billing()
    tool, _ = python_langchain_tool(p, p.python_tools["explode"])
    with pytest.raises(ToolException, match="KeyError"):
        await tool.ainvoke({"account_number": "A1"})


async def test_sync_tool_does_not_block_loop():
    p = Pack("demo")

    @p.tool(read_only=True)
    def slow(x: str) -> str:
        """Slow."""
        time.sleep(0.3)
        return x

    tool, _ = python_langchain_tool(p, p.python_tools["slow"])
    done = {}

    async def quick():
        await asyncio.sleep(0.05)
        done["quick"] = time.monotonic()

    async def slow_call():
        await tool.ainvoke({"x": "y"})
        done["slow"] = time.monotonic()

    await asyncio.gather(slow_call(), quick())
    assert done["quick"] < done["slow"]


def test_mcp_requires_exactly_one_transport():
    p = Pack("demo")
    with pytest.raises(PackError):
        p.mcp("x")
    with pytest.raises(PackError):
        p.mcp("x", url="https://u", command="c")
    p.mcp("ok", url="https://u", exclude=["a"])
    assert p.mcp_sources[0].spec.url == "https://u"
    assert p.mcp_sources[0].exclude == ["a"]


def test_paths_resolve_relative_to_base_dir(tmp_path):
    p = Pack("p", rules="r.yaml", evals="evals", base_dir=tmp_path)
    assert p.rules_path == tmp_path / "r.yaml"
    assert p.evals_path == tmp_path / "evals"
    assert Pack("q").rules_path is None
