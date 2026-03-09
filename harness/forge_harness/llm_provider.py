"""
LLM Provider Abstraction
========================

Provides a unified interface for different LLM providers (Claude, OpenAI, Gemini, etc.)
to be used within the FORGE harness.
"""

import asyncio
import json
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ProviderOptions:
    """Standard options for LLM providers."""

    model: str
    max_tokens: int | None = None
    temperature: float = 0.7
    system_prompt: str | None = None
    cwd: str | None = None
    allowed_tools: list[str] | None = None
    hooks: dict[str, Any] | None = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def generate_content(self, prompt: str, options: ProviderOptions) -> str:
        """Generate a single text response."""
        pass

    @abstractmethod
    def run_coding_session(
        self, prompt: str, options: ProviderOptions
    ) -> AsyncGenerator[Any, None]:
        """
        Run an interactive coding session (streaming).
        Yields messages/chunks from the provider.
        """
        pass


class ClaudeCodeProvider(LLMProvider):
    """Provider for Anthropic's Claude Code SDK."""

    def __init__(self):
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
            from claude_agent_sdk.types import HookMatcher

            self._sdk_options_cls = ClaudeAgentOptions
            self._query_func = query
            self._hook_matcher_cls = HookMatcher
        except ImportError:
            raise ImportError("claude-agent-sdk is not installed.")

    async def generate_content(self, prompt: str, options: ProviderOptions) -> str:
        response = ""
        async for message in self.run_coding_session(prompt, options):
            if hasattr(message, "content"):
                response += str(message.content)
        return response

    def run_coding_session(
        self, prompt: str, options: ProviderOptions
    ) -> AsyncGenerator[Any, None]:
        sdk_options = self._sdk_options_cls(
            model=options.model,
            cwd=options.cwd,
            allowed_tools=options.allowed_tools
            or ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
            system_prompt=options.system_prompt,
            hooks=options.hooks,
            permission_mode="acceptEdits",  # Auto-accept file edits for autonomous operation
        )
        return self._query_func(prompt=prompt, options=sdk_options)


# --- Tool Implementations for Generic Providers ---


async def _execute_bash(command: str, cwd: str | None = None) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode() + stderr.decode()
        return output if output.strip() else "(no output)"
    except Exception as e:
        return f"Error executing command: {e}"


async def _read_file(path: str, cwd: str | None = None) -> str:
    try:
        full_path = Path(cwd) / path if cwd else Path(path)
        if not full_path.exists():
            return f"Error: File not found: {path}"
        return full_path.read_text()
    except Exception as e:
        return f"Error reading file: {e}"


async def _write_file(path: str, content: str, cwd: str | None = None) -> str:
    try:
        full_path = Path(cwd) / path if cwd else Path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


async def _list_directory(path: str = ".", cwd: str | None = None) -> str:
    try:
        full_path = Path(cwd) / path if cwd else Path(path)
        if not full_path.exists():
            return f"Error: Directory not found: {path}"
        files = [f.name for f in full_path.iterdir()]
        return "\n".join(files)
    except Exception as e:
        return f"Error listing directory: {e}"


# --- OpenAI Provider ---


class OpenAIProvider(LLMProvider):
    """Provider for OpenAI (GPT-4o, etc.)."""

    def __init__(self):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai is not installed. Run `uv add openai`.")
        self.client = AsyncOpenAI()

    def _get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "Execute a bash command",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "The command to execute"}
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read content of a file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to the file"}
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write content to a file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to the file"},
                            "content": {"type": "string", "description": "Content to write"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "description": "List files in a directory",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to the directory"}
                        },
                        "required": ["path"],
                    },
                },
            },
        ]

    async def generate_content(self, prompt: str, options: ProviderOptions) -> str:
        messages = [{"role": "user", "content": prompt}]
        if options.system_prompt:
            messages.insert(0, {"role": "system", "content": options.system_prompt})

        response = await self.client.chat.completions.create(
            model=options.model,
            messages=messages,
            max_tokens=options.max_tokens,
            temperature=options.temperature,
        )
        return response.choices[0].message.content or ""

    async def run_coding_session(
        self, prompt: str, options: ProviderOptions
    ) -> AsyncGenerator[Any, None]:
        messages = [{"role": "user", "content": prompt}]
        if options.system_prompt:
            messages.insert(0, {"role": "system", "content": options.system_prompt})

        tools = self._get_tools()
        cwd = options.cwd or os.getcwd()

        # Simple agent loop (max turns hardcoded for safety)
        for _ in range(20):
            # Non-streaming for logic simplicity with tools
            response = await self.client.chat.completions.create(
                model=options.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                stream=False,
            )

            msg = response.choices[0].message
            messages.append(msg)

            if msg.content:
                yield type("Message", (), {"content": msg.content})()

            if not msg.tool_calls:
                break  # No more tools, we are done

            # Execute tools
            for tool_call in msg.tool_calls:
                result = ""
                try:
                    args = json.loads(tool_call.function.arguments)
                    name = tool_call.function.name

                    yield type("Message", (), {"content": f"\n[Executing {name}... ]\n"})()

                    if name == "bash":
                        result = await _execute_bash(args["command"], cwd)
                    elif name == "read_file":
                        result = await _read_file(args["path"], cwd)
                    elif name == "write_file":
                        result = await _write_file(args["path"], args["content"], cwd)
                    elif name == "list_directory":
                        result = await _list_directory(args.get("path", "."), cwd)
                    else:
                        result = f"Error: Unknown tool {name}"
                except Exception as e:
                    result = f"Error executing tool: {e}"

                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})


# --- Gemini Provider ---


class GeminiProvider(LLMProvider):
    """Provider for Google Gemini."""

    def __init__(self):
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError(
                "google-generativeai is not installed. Run `uv add google-generativeai`."
            )

        self.genai = genai
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            pass  # Rely on implicit auth or warn later
        else:
            genai.configure(api_key=api_key)

    def _get_tools(self):
        # Gemini function calling format
        return [
            {
                "function_declarations": [
                    {
                        "name": "bash",
                        "description": "Execute a bash command",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "command": {
                                    "type": "STRING",
                                    "description": "The command to execute",
                                }
                            },
                            "required": ["command"],
                        },
                    },
                    {
                        "name": "read_file",
                        "description": "Read content of a file",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "path": {"type": "STRING", "description": "Path to the file"}
                            },
                            "required": ["path"],
                        },
                    },
                    {
                        "name": "write_file",
                        "description": "Write content to a file",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "path": {"type": "STRING", "description": "Path to the file"},
                                "content": {"type": "STRING", "description": "Content to write"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                    {
                        "name": "list_directory",
                        "description": "List files in a directory",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "path": {"type": "STRING", "description": "Path to the directory"}
                            },
                            "required": ["path"],
                        },
                    },
                ]
            }
        ]

    async def generate_content(self, prompt: str, options: ProviderOptions) -> str:
        model = self.genai.GenerativeModel(options.model)
        response = await model.generate_content_async(prompt)
        return response.text

    async def run_coding_session(
        self, prompt: str, options: ProviderOptions
    ) -> AsyncGenerator[Any, None]:
        model = self.genai.GenerativeModel(
            options.model, tools=self._get_tools(), system_instruction=options.system_prompt
        )
        chat = model.start_chat(enable_automatic_function_calling=False)  # Manual for control

        cwd = options.cwd or os.getcwd()

        # Initial message
        response = await chat.send_message_async(prompt)

        # Loop for tool calls (simplified loop)
        for _ in range(20):
            if not response.parts:
                break

            # Check for text
            text_content = ""
            for part in response.parts:
                if part.text:
                    text_content += part.text

            if text_content:
                yield type("Message", (), {"content": text_content})()

            # Check for function calls
            function_calls = []
            for part in response.parts:
                if part.function_call:
                    function_calls.append(part.function_call)

            if not function_calls:
                break

            # Execute tools
            tool_responses = []
            for call in function_calls:
                name = call.name
                args = call.args
                result = ""

                yield type("Message", (), {"content": f"\n[Executing {name}... ]\n"})()

                try:
                    if name == "bash":
                        result = await _execute_bash(args["command"], cwd)
                    elif name == "read_file":
                        result = await _read_file(args["path"], cwd)
                    elif name == "write_file":
                        result = await _write_file(args["path"], args["content"], cwd)
                    elif name == "list_directory":
                        result = await _list_directory(args.get("path", "."), cwd)
                    else:
                        result = f"Unknown tool: {name}"
                except Exception as e:
                    result = f"Error: {e}"

                # Gemini expects function responses
                from google.generativeai.protos import FunctionResponse, Part

                tool_responses.append(
                    Part(function_response=FunctionResponse(name=name, response={"result": result}))
                )

            # Send tool outputs back
            if tool_responses:
                response = await chat.send_message_async(tool_responses)
            else:
                break


class MockProvider(LLMProvider):
    """Mock provider for testing the harness without external APIs."""

    async def generate_content(self, prompt: str, options: ProviderOptions) -> str:
        return "This is a mock response."

    async def run_coding_session(
        self, prompt: str, options: ProviderOptions
    ) -> AsyncGenerator[Any, None]:
        yield type("Message", (), {"content": "Mock session started."})()
        yield type("Message", (), {"content": f"Implementing task in {options.cwd}..."})()
        yield type("Message", (), {"content": "Mock implementation complete."})()


def get_provider(provider_name: str) -> LLMProvider:
    """Factory to get LLM provider instance."""
    providers = {
        "claude": ClaudeCodeProvider,
        "openai": OpenAIProvider,
        "gemini": GeminiProvider,
        "mock": MockProvider,
    }

    provider_class = providers.get(provider_name.lower())
    if not provider_class:
        # Fallback to claude if unknown, or raise error
        return ClaudeCodeProvider()

    return provider_class()
