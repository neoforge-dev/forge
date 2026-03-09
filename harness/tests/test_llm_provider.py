import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import forge_harness.llm_provider as llm


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeChoice:
    def __init__(self, message):
        self.message = message


class FakeResponse:
    def __init__(self, message):
        self.choices = [FakeChoice(message)]


class FakeToolFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, tool_id, name, arguments):
        self.id = tool_id
        self.function = FakeToolFunction(name, arguments)


@pytest.mark.asyncio
async def test_execute_bash_error(monkeypatch):
    async def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _raise)
    output = await llm._execute_bash("echo hi")
    assert "Error executing command" in output


@pytest.mark.asyncio
async def test_execute_bash_success(monkeypatch):
    class FakeProc:
        async def communicate(self):
            return (b"ok\n", b"")

    async def fake_create(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create)
    output = await llm._execute_bash("echo hi")
    assert output.strip() == "ok"


@pytest.mark.asyncio
async def test_read_file_missing(tmp_path):
    missing = tmp_path / "nope.txt"
    output = await llm._read_file(str(missing))
    assert "Error: File not found" in output


@pytest.mark.asyncio
async def test_write_and_list_directory(tmp_path):
    output = await llm._write_file("a/b.txt", "hello", cwd=str(tmp_path))
    assert "Successfully wrote" in output
    listing = await llm._list_directory("a", cwd=str(tmp_path))
    assert "b.txt" in listing


@pytest.mark.asyncio
async def test_read_file_success(tmp_path):
    path = tmp_path / "ok.txt"
    path.write_text("hello")
    output = await llm._read_file(str(path))
    assert output == "hello"


@pytest.mark.asyncio
async def test_write_file_error(monkeypatch, tmp_path):
    def fake_write_text(self, content):
        raise OSError("nope")

    monkeypatch.setattr(Path, "write_text", fake_write_text)
    output = await llm._write_file("x.txt", "hi", cwd=str(tmp_path))
    assert "Error writing file" in output


@pytest.mark.asyncio
async def test_list_directory_missing(tmp_path):
    output = await llm._list_directory("missing", cwd=str(tmp_path))
    assert "Directory not found" in output


@pytest.mark.asyncio
async def test_openai_generate_content_formats_messages(monkeypatch):
    calls = {}

    class FakeOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def _create(self, **kwargs):
            calls.update(kwargs)
            return FakeResponse(FakeMessage(content="hello"))

    monkeypatch.setitem(sys_modules(), "openai", SimpleNamespace(AsyncOpenAI=FakeOpenAI))

    provider = llm.OpenAIProvider()
    options = llm.ProviderOptions(model="gpt-4o", system_prompt="sys", max_tokens=10)
    result = await provider.generate_content("hi", options)

    assert result == "hello"
    assert calls["model"] == "gpt-4o"
    assert calls["messages"][0]["role"] == "system"
    assert calls["messages"][1]["role"] == "user"


@pytest.mark.asyncio
async def test_openai_run_coding_session_tools(monkeypatch, tmp_path):
    calls = {"count": 0}

    async def fake_list_directory(path=".", cwd=None):
        return "file1.py\nfile2.py"

    monkeypatch.setattr(llm, "_list_directory", fake_list_directory)

    responses = [
        FakeResponse(
            FakeMessage(
                content="",
                tool_calls=[FakeToolCall("1", "list_directory", json.dumps({"path": "."}))],
            )
        ),
        FakeResponse(FakeMessage(content="done", tool_calls=[])),
    ]

    class FakeOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def _create(self, **kwargs):
            calls["count"] += 1
            return responses.pop(0)

    monkeypatch.setitem(sys_modules(), "openai", SimpleNamespace(AsyncOpenAI=FakeOpenAI))

    provider = llm.OpenAIProvider()
    options = llm.ProviderOptions(model="gpt-4o", cwd=str(tmp_path))

    chunks = []
    async for msg in provider.run_coding_session("hi", options):
        chunks.append(msg.content)

    assert any("Executing list_directory" in c for c in chunks)
    assert "done" in chunks[-1]
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_openai_run_coding_session_tool_error(monkeypatch):
    calls = {"messages": []}

    responses = [
        FakeResponse(FakeMessage(content="", tool_calls=[FakeToolCall("1", "bash", "{bad-json")])),
        FakeResponse(FakeMessage(content="ok", tool_calls=[])),
    ]

    class FakeOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def _create(self, **kwargs):
            calls["messages"].append(kwargs["messages"])
            return responses.pop(0)

    monkeypatch.setitem(sys_modules(), "openai", SimpleNamespace(AsyncOpenAI=FakeOpenAI))

    provider = llm.OpenAIProvider()
    options = llm.ProviderOptions(model="gpt-4o")

    async for _ in provider.run_coding_session("hi", options):
        pass

    # Second call should include tool error response
    assert any(isinstance(m, dict) and m.get("role") == "tool" for m in calls["messages"][1])


@pytest.mark.asyncio
async def test_openai_run_coding_session_unknown_tool(monkeypatch):
    calls = {"messages": []}

    responses = [
        FakeResponse(
            FakeMessage(
                content="",
                tool_calls=[FakeToolCall("1", "unknown", json.dumps({"path": "."}))],
            )
        ),
        FakeResponse(FakeMessage(content="ok", tool_calls=[])),
    ]

    class FakeOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def _create(self, **kwargs):
            calls["messages"].append(kwargs["messages"])
            return responses.pop(0)

    monkeypatch.setitem(sys_modules(), "openai", SimpleNamespace(AsyncOpenAI=FakeOpenAI))

    provider = llm.OpenAIProvider()
    options = llm.ProviderOptions(model="gpt-4o")

    async for _ in provider.run_coding_session("hi", options):
        pass

    tool_messages = [
        m for m in calls["messages"][1] if isinstance(m, dict) and m.get("role") == "tool"
    ]
    assert tool_messages
    assert "Unknown tool" in tool_messages[0]["content"]


@pytest.mark.asyncio
async def test_openai_rate_limit_turn_cap(monkeypatch):
    calls = {"count": 0}

    async def fake_bash(command, cwd=None):
        return "ok"

    monkeypatch.setattr(llm, "_execute_bash", fake_bash)

    response = FakeResponse(
        FakeMessage(
            content="", tool_calls=[FakeToolCall("1", "bash", json.dumps({"command": "echo hi"}))]
        )
    )

    class FakeOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def _create(self, **kwargs):
            calls["count"] += 1
            return response

    monkeypatch.setitem(sys_modules(), "openai", SimpleNamespace(AsyncOpenAI=FakeOpenAI))

    provider = llm.OpenAIProvider()
    options = llm.ProviderOptions(model="gpt-4o")

    # Consume generator fully
    async for _ in provider.run_coding_session("hi", options):
        pass

    assert calls["count"] == 20  # max turns


@pytest.mark.asyncio
async def test_gemini_provider_api_key_config(monkeypatch):
    configured = {"api_key": None}

    class FakeGenAI:
        def configure(self, api_key):
            configured["api_key"] = api_key

        class GenerativeModel:
            def __init__(self, *args, **kwargs):
                pass

            async def generate_content_async(self, prompt):
                return SimpleNamespace(text="gemini")

    monkeypatch.setitem(sys_modules(), "google.generativeai", FakeGenAI())
    monkeypatch.setenv("GEMINI_API_KEY", "key-123")

    provider = llm.GeminiProvider()
    options = llm.ProviderOptions(model="gemini-2.0")
    result = await provider.generate_content("hi", options)

    assert result == "gemini"
    assert configured["api_key"] == "key-123"


def test_gemini_provider_no_api_key(monkeypatch):
    class FakeGenAI:
        def configure(self, api_key):
            raise AssertionError("configure should not be called")

        class GenerativeModel:
            def __init__(self, *args, **kwargs):
                pass

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setitem(sys_modules(), "google.generativeai", FakeGenAI())
    provider = llm.GeminiProvider()
    assert provider.genai is not None


@pytest.mark.asyncio
async def test_gemini_run_coding_session_text_only(monkeypatch):
    class FakePart:
        def __init__(self, text=None, function_call=None):
            self.text = text
            self.function_call = function_call

    class FakeResponse:
        def __init__(self, parts):
            self.parts = parts

    class FakeChat:
        async def send_message_async(self, message):
            return FakeResponse([FakePart(text="hello")])

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def start_chat(self, enable_automatic_function_calling=False):
            return FakeChat()

    class FakeGenAI:
        GenerativeModel = FakeModel

    monkeypatch.setitem(sys_modules(), "google.generativeai", FakeGenAI())

    provider = llm.GeminiProvider()
    options = llm.ProviderOptions(model="gemini-2.0")

    chunks = []
    async for msg in provider.run_coding_session("hi", options):
        chunks.append(msg.content)

    assert chunks == ["hello"]


@pytest.mark.asyncio
async def test_gemini_run_coding_session_with_tool(monkeypatch, tmp_path):
    class FakePart:
        def __init__(self, text=None, function_call=None):
            self.text = text
            self.function_call = function_call

    class FakeFunctionCall:
        def __init__(self, name, args):
            self.name = name
            self.args = args

    class FakeResponse:
        def __init__(self, parts):
            self.parts = parts

    class FakeChat:
        def __init__(self):
            self.calls = 0

        async def send_message_async(self, message):
            self.calls += 1
            if self.calls == 1:
                return FakeResponse(
                    [FakePart(function_call=FakeFunctionCall("bash", {"command": "echo hi"}))]
                )
            return FakeResponse([FakePart(text="done")])

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def start_chat(self, enable_automatic_function_calling=False):
            return FakeChat()

    class FakeGenAI:
        GenerativeModel = FakeModel

    class FakeFunctionResponse:
        def __init__(self, name, response):
            self.name = name
            self.response = response

    class FakeProtoPart:
        def __init__(self, function_response=None):
            self.function_response = function_response

    monkeypatch.setitem(sys_modules(), "google.generativeai", FakeGenAI())
    monkeypatch.setitem(
        sys_modules(),
        "google.generativeai.protos",
        SimpleNamespace(FunctionResponse=FakeFunctionResponse, Part=FakeProtoPart),
    )

    async def fake_bash(command, cwd=None):
        return "ok"

    monkeypatch.setattr(llm, "_execute_bash", fake_bash)

    provider = llm.GeminiProvider()
    options = llm.ProviderOptions(model="gemini-2.0", cwd=str(tmp_path))

    chunks = []
    async for msg in provider.run_coding_session("hi", options):
        chunks.append(msg.content)

    assert any("Executing bash" in c for c in chunks)
    assert "done" in chunks[-1]


@pytest.mark.asyncio
async def test_claude_code_provider(monkeypatch):
    class FakeOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    async def fake_query(prompt, options):
        yield SimpleNamespace(content="part1")
        yield SimpleNamespace(content="part2")

    class FakeHookMatcher:
        pass

    fake_module = SimpleNamespace(
        ClaudeAgentOptions=FakeOptions,
        query=fake_query,
    )
    monkeypatch.setitem(sys_modules(), "claude_agent_sdk", fake_module)
    monkeypatch.setitem(
        sys_modules(), "claude_agent_sdk.types", SimpleNamespace(HookMatcher=FakeHookMatcher)
    )

    provider = llm.ClaudeCodeProvider()
    options = llm.ProviderOptions(model="claude-3")
    result = await provider.generate_content("hi", options)

    assert result == "part1part2"


def test_get_provider_factory(monkeypatch):
    class FakeOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    async def fake_query(prompt, options):
        yield SimpleNamespace(content="ok")

    fake_module = SimpleNamespace(
        ClaudeAgentOptions=FakeOptions,
        query=fake_query,
    )
    monkeypatch.setitem(sys_modules(), "claude_agent_sdk", fake_module)
    monkeypatch.setitem(
        sys_modules(), "claude_agent_sdk.types", SimpleNamespace(HookMatcher=object)
    )

    assert isinstance(llm.get_provider("mock"), llm.MockProvider)
    # Unknown provider falls back to claude
    assert isinstance(llm.get_provider("unknown"), llm.ClaudeCodeProvider)


@pytest.mark.asyncio
async def test_mock_provider_session():
    provider = llm.MockProvider()
    options = llm.ProviderOptions(model="mock", cwd="/tmp")
    chunks = []
    async for msg in provider.run_coding_session("hi", options):
        chunks.append(msg.content)

    assert len(chunks) == 3
    assert "Mock implementation complete." in chunks[-1]


# Additional coverage for empty responses, tools, and import errors


@pytest.mark.asyncio
async def test_openai_generate_content_empty_response(monkeypatch):
    class FakeOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def _create(self, **kwargs):
            return FakeResponse(FakeMessage(content=None))

    monkeypatch.setitem(sys_modules(), "openai", SimpleNamespace(AsyncOpenAI=FakeOpenAI))

    provider = llm.OpenAIProvider()
    options = llm.ProviderOptions(model="gpt-4o")
    result = await provider.generate_content("hi", options)
    assert result == ""


def test_openai_tools_definition(monkeypatch):
    class FakeOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: None))

    monkeypatch.setitem(sys_modules(), "openai", SimpleNamespace(AsyncOpenAI=FakeOpenAI))
    provider = llm.OpenAIProvider()
    tools = provider._get_tools()
    assert any(t["function"]["name"] == "bash" for t in tools)


def test_gemini_tools_definition(monkeypatch):
    class FakeGenAI:
        class GenerativeModel:
            def __init__(self, *args, **kwargs):
                pass

    monkeypatch.setitem(sys_modules(), "google.generativeai", FakeGenAI())
    provider = llm.GeminiProvider()
    tools = provider._get_tools()
    assert tools and "function_declarations" in tools[0]


def test_import_errors(monkeypatch):
    import builtins

    orig_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"openai", "google.generativeai", "claude_agent_sdk"}:
            raise ImportError("missing")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError):
        llm.OpenAIProvider()
    with pytest.raises(ImportError):
        llm.GeminiProvider()
    with pytest.raises(ImportError):
        llm.ClaudeCodeProvider()


# Helper to avoid importing sys directly in test code above


def sys_modules():
    import sys

    return sys.modules
