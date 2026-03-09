"""
Security Hooks for FORGE Autonomous Agents
===========================================

Pre-tool-use hooks that validate bash commands for security.
Uses an allowlist approach - only explicitly permitted commands can run.

Extended from the base harness to support FORGE's full tech stack:
- Astral uv for Python package management (MANDATORY)
- FastAPI/uvicorn for backend servers
- Railway CLI for backend deployment
- Cloudflare Wrangler for frontend deployment
- Alembic for database migrations
- PostHog analytics CLI (if needed)
"""

import os
import re
import secrets
import shlex
import string
from dataclasses import dataclass, field
from typing import Any, Final

import bcrypt

# Allowed commands for FORGE development tasks
ALLOWED_COMMANDS = {
    # File inspection
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "find",
    "tree",
    # File operations
    "cp",
    "mv",
    "mkdir",
    "touch",
    "chmod",  # Validated separately
    "rm",  # Validated separately - only for safe patterns
    # Directory
    "pwd",
    "cd",
    # Python - Astral uv ONLY (never pip)
    "uv",
    "python",
    "python3",
    "uvicorn",
    "gunicorn",
    "alembic",
    # Testing
    "pytest",
    "coverage",
    # Node.js development
    "npm",
    "npx",
    "node",
    "pnpm",
    "vite",
    # Version control
    "git",
    "gh",  # GitHub CLI
    # Process management
    "ps",
    "lsof",
    "sleep",
    "pkill",  # Validated separately
    "kill",  # Validated separately
    # Build tools
    "make",
    "just",  # Just command runner
    # Deployment
    "railway",  # Railway CLI
    "wrangler",  # Cloudflare CLI
    "docker",  # Validated separately
    "docker-compose",
    # Database
    "psql",  # PostgreSQL CLI - validated separately
    "sqlite3",
    # Utilities
    "curl",  # Validated separately - HTTP only
    "jq",
    "sed",
    "awk",
    "sort",
    "uniq",
    "xargs",
    "echo",
    "printf",
    "date",
    "env",
    "which",
    "whereis",
    # Script execution
    "bash",  # Validated separately
    "sh",  # Validated separately
    "init.sh",  # Validated separately
}

# Commands that need additional validation even when in the allowlist
COMMANDS_NEEDING_EXTRA_VALIDATION = {
    "pkill",
    "kill",
    "chmod",
    "rm",
    "docker",
    "psql",
    "curl",
    "bash",
    "sh",
    "init.sh",
    "railway",
    "wrangler",
}

# Sensitive paths that should never be modified
# Note: /home and /Users are intentionally NOT included since FORGE agents
# work in user home directories. We protect /root (superuser home) instead.
PROTECTED_PATHS = {
    "/",
    "/etc",
    "/usr",
    "/var",
    "/bin",
    "/sbin",
    "/boot",
    "/root",
    "/System",
    "/Library",
    "/Applications",
}


@dataclass
class SecurityContext:
    """Context for security validation."""

    domain: str | None = None
    project: str | None = None
    allowed_dirs: list[str] = field(default_factory=list)
    extra_allowed_commands: set[str] = field(default_factory=set)


@dataclass
class ParsedCommand:
    """
    Structured representation of a parsed shell command.

    Provides explicit flags for dangerous patterns that should be
    detected BEFORE attempting shlex parsing.
    """

    executable: str
    args: list[str]
    has_shell_expansion: bool  # $VAR, ${VAR}
    has_subshell: bool  # $(...) or `...`
    has_pipe: bool  # |
    has_redirect: bool  # >, >>, <
    has_background: bool  # &
    raw: str
    parse_error: str | None = None

    @property
    def is_dangerous(self) -> bool:
        """Check if command has dangerous patterns that should be blocked."""
        return self.has_subshell

    def __str__(self) -> str:
        return self.raw


# Patterns for detecting dangerous shell constructs BEFORE parsing
SUBSHELL_PATTERN = re.compile(r"\$\([^)]+\)")  # $(...)
BACKTICK_PATTERN = re.compile(r"`[^`]+`")  # `...`
SHELL_EXPANSION_PATTERN = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")  # $VAR or ${VAR}
REDIRECT_PATTERN = re.compile(r"[<>]{1,2}")  # <, >, >>
# Match single & not preceded or followed by & (background, not &&)
BACKGROUND_PATTERN = re.compile(r"(?<!&)&(?!&)")
# Match single | not followed by | (pipe, not ||)
PIPE_PATTERN = re.compile(r"\|(?!\|)")


def detect_dangerous_patterns(command: str) -> dict[str, bool]:
    """
    Detect dangerous shell patterns in a command string.

    This function runs BEFORE shlex parsing to catch obfuscation attempts
    that might be designed to bypass the parser.

    Args:
        command: Raw shell command string

    Returns:
        Dict of pattern flags
    """
    return {
        "has_subshell": bool(SUBSHELL_PATTERN.search(command) or BACKTICK_PATTERN.search(command)),
        "has_shell_expansion": bool(SHELL_EXPANSION_PATTERN.search(command)),
        "has_pipe": bool(PIPE_PATTERN.search(command)),
        "has_redirect": bool(REDIRECT_PATTERN.search(command)),
        "has_background": bool(BACKGROUND_PATTERN.search(command)),
    }


def parse_command(command: str) -> ParsedCommand:
    """
    Parse a shell command into a structured representation.

    Detects dangerous patterns BEFORE attempting shlex parsing.
    This prevents obfuscation attacks that exploit parser quirks.

    Args:
        command: Raw shell command string

    Returns:
        ParsedCommand with extracted executable, args, and pattern flags
    """
    command = command.strip()

    # Detect dangerous patterns before parsing
    patterns = detect_dangerous_patterns(command)

    # Try to parse with shlex
    try:
        tokens = shlex.split(command)
    except ValueError as e:
        # Malformed command - could be an obfuscation attempt
        return ParsedCommand(
            executable="",
            args=[],
            has_shell_expansion=patterns["has_shell_expansion"],
            has_subshell=patterns["has_subshell"],
            has_pipe=patterns["has_pipe"],
            has_redirect=patterns["has_redirect"],
            has_background=patterns["has_background"],
            raw=command,
            parse_error=str(e),
        )

    if not tokens:
        return ParsedCommand(
            executable="",
            args=[],
            has_shell_expansion=patterns["has_shell_expansion"],
            has_subshell=patterns["has_subshell"],
            has_pipe=patterns["has_pipe"],
            has_redirect=patterns["has_redirect"],
            has_background=patterns["has_background"],
            raw=command,
        )

    # Extract executable (handle paths)
    executable = os.path.basename(tokens[0])
    args = tokens[1:] if len(tokens) > 1 else []

    return ParsedCommand(
        executable=executable,
        args=args,
        has_shell_expansion=patterns["has_shell_expansion"],
        has_subshell=patterns["has_subshell"],
        has_pipe=patterns["has_pipe"],
        has_redirect=patterns["has_redirect"],
        has_background=patterns["has_background"],
        raw=command,
    )


def split_command_segments(command_string: str) -> list[str]:
    """
    Split a compound command into individual command segments.

    Handles command chaining (&&, ||, ;) but not pipes (those are single commands).

    Args:
        command_string: The full shell command

    Returns:
        List of individual command segments
    """
    # Split on && and || while preserving the ability to handle each segment
    segments = re.split(r"\s*(?:&&|\|\|)\s*", command_string)

    # Further split on semicolons
    result = []
    for segment in segments:
        sub_segments = re.split(r'(?<!["\'])\s*;\s*(?!["\'])', segment)
        for sub in sub_segments:
            sub = sub.strip()
            if sub:
                result.append(sub)

    return result


def extract_commands(command_string: str) -> list[str]:
    """
    Extract command names from a shell command string.

    Handles pipes, command chaining (&&, ||, ;), and subshells.
    Returns the base command names (without paths).

    Args:
        command_string: The full shell command

    Returns:
        List of command names found in the string
    """
    commands = []

    # Split on semicolons that aren't inside quotes
    segments = re.split(r'(?<!["\'])\s*;\s*(?!["\'])', command_string)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        try:
            tokens = shlex.split(segment)
        except ValueError:
            # Malformed command - fail safe by returning empty
            return []

        if not tokens:
            continue

        # Track when we expect a command vs arguments
        expect_command = True

        for token in tokens:
            # Shell operators indicate a new command follows
            if token in ("|", "||", "&&", "&"):
                expect_command = True
                continue

            # Skip shell keywords
            if token in (
                "if",
                "then",
                "else",
                "elif",
                "fi",
                "for",
                "while",
                "until",
                "do",
                "done",
                "case",
                "esac",
                "in",
                "!",
                "{",
                "}",
            ):
                continue

            # Skip flags/options
            if token.startswith("-"):
                continue

            # Skip variable assignments
            if "=" in token and not token.startswith("="):
                continue

            if expect_command:
                # Extract the base command name
                cmd = os.path.basename(token)
                commands.append(cmd)
                expect_command = False

    return commands


def validate_pkill_command(command_string: str) -> tuple[bool, str]:
    """
    Validate pkill commands - only allow killing dev-related processes.

    Returns:
        Tuple of (is_allowed, reason_if_blocked)
    """
    allowed_process_names = {
        "node",
        "npm",
        "npx",
        "vite",
        "next",
        "uvicorn",
        "gunicorn",
        "python",
        "python3",
        "pytest",
        "alembic",
    }

    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse pkill command"

    if not tokens:
        return False, "Empty pkill command"

    # Separate flags from arguments
    args = [token for token in tokens[1:] if not token.startswith("-")]

    if not args:
        return False, "pkill requires a process name"

    target = args[-1]

    # For -f flag, extract first word as process name
    if " " in target:
        target = target.split()[0]

    if target in allowed_process_names:
        return True, ""
    return False, f"pkill only allowed for dev processes: {allowed_process_names}"


def validate_kill_command(command_string: str) -> tuple[bool, str]:
    """
    Validate kill commands - only allow with specific signals.

    Returns:
        Tuple of (is_allowed, reason_if_blocked)
    """
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse kill command"

    # Allow standard signals for dev processes
    allowed_signals = {"-9", "-15", "-TERM", "-INT", "-HUP", "-KILL"}

    for token in tokens[1:]:
        if token.startswith("-") and token not in allowed_signals:
            # Could be a PID if it's a number
            try:
                int(token)
            except ValueError:
                return False, f"kill signal '{token}' not allowed"

    return True, ""


def validate_chmod_command(command_string: str) -> tuple[bool, str]:
    """
    Validate chmod commands - only allow making files executable with +x.

    Returns:
        Tuple of (is_allowed, reason_if_blocked)
    """
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse chmod command"

    if not tokens or tokens[0] != "chmod":
        return False, "Not a chmod command"

    mode = None
    files = []

    for token in tokens[1:]:
        if token.startswith("-"):
            return False, "chmod flags are not allowed"
        elif mode is None:
            mode = token
        else:
            files.append(token)

    if mode is None:
        return False, "chmod requires a mode"

    if not files:
        return False, "chmod requires at least one file"

    # Only allow +x variants
    if not re.match(r"^[ugoa]*\+x$", mode):
        return False, f"chmod only allowed with +x mode, got: {mode}"

    return True, ""


def validate_rm_command(
    command_string: str, context: SecurityContext | None = None
) -> tuple[bool, str]:
    """
    Validate rm commands - prevent dangerous deletions.

    Returns:
        Tuple of (is_allowed, reason_if_blocked)
    """
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse rm command"

    if not tokens:
        return False, "Empty rm command"

    dangerous_flags = {"-rf", "-fr", "--recursive"}
    has_recursive = any(t in dangerous_flags or t == "-r" for t in tokens)

    for token in tokens[1:]:
        if token.startswith("-"):
            continue

        # Expand the path
        path = os.path.expanduser(token)

        # Check against protected paths
        for protected in PROTECTED_PATHS:
            if path == protected or path.startswith(protected + "/"):
                return False, f"Cannot delete protected path: {path}"

        # Block recursive delete in home directory root
        if has_recursive and (path == os.path.expanduser("~") or path == "/"):
            return False, "Cannot recursively delete home or root directory"

    return True, ""


def validate_docker_command(command_string: str) -> tuple[bool, str]:
    """
    Validate docker commands - only allow safe operations.

    Returns:
        Tuple of (is_allowed, reason_if_blocked)
    """
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse docker command"

    if len(tokens) < 2:
        return False, "docker requires a subcommand"

    allowed_subcommands = {
        "build",
        "run",
        "ps",
        "images",
        "logs",
        "stop",
        "start",
        "restart",
        "rm",
        "rmi",
        "exec",
        "compose",
        "network",
        "volume",
        "inspect",
        "pull",
        "push",
        "tag",
    }

    subcommand = tokens[1]
    if subcommand not in allowed_subcommands:
        return False, f"docker subcommand '{subcommand}' not allowed"

    # Block privileged mode
    if "--privileged" in tokens:
        return False, "docker --privileged not allowed"

    # Block host network mode
    if "--network=host" in tokens or ("--network" in tokens and "host" in tokens):
        return False, "docker --network=host not allowed"

    return True, ""


def validate_psql_command(
    command_string: str, context: SecurityContext | None = None
) -> tuple[bool, str]:
    """
    Validate psql commands - allow only for development/migration.

    Returns:
        Tuple of (is_allowed, reason_if_blocked)
    """
    # For now, allow all psql commands as they're typically for dev/migration
    # In production, this would be more restrictive
    return True, ""


def validate_curl_command(command_string: str) -> tuple[bool, str]:
    """
    Validate curl commands - only allow HTTP/HTTPS requests.

    Returns:
        Tuple of (is_allowed, reason_if_blocked)
    """
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse curl command"

    for token in tokens[1:]:
        if token.startswith("-"):
            continue

        # Check if it's a URL
        if token.startswith(("http://", "https://")):
            continue

        # Check for file:// or other dangerous protocols
        if "://" in token and not token.startswith(("http://", "https://")):
            return False, f"curl only allowed for HTTP/HTTPS: {token}"

    return True, ""


def validate_script_execution(command_string: str) -> tuple[bool, str]:
    """
    Validate bash/sh script execution - only allow safe patterns.

    Returns:
        Tuple of (is_allowed, reason_if_blocked)
    """
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse script command"

    if not tokens:
        return False, "Empty command"

    script = tokens[0]

    # Allow ./init.sh or paths ending in /init.sh
    if script == "./init.sh" or script.endswith("/init.sh"):
        return True, ""

    # Allow bash/sh with -c for inline commands (will be validated separately)
    if tokens[0] in ("bash", "sh") and "-c" in tokens:
        return True, ""

    # Allow running scripts in the current project
    if script.startswith("./") and script.endswith(".sh"):
        return True, ""

    return False, f"Script execution pattern not allowed: {script}"


def validate_railway_command(command_string: str) -> tuple[bool, str]:
    """
    Validate Railway CLI commands - only allow safe deployment operations.

    Returns:
        Tuple of (is_allowed, reason_if_blocked)
    """
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse railway command"

    if len(tokens) < 2:
        return True, ""  # Allow `railway` alone for help

    allowed_subcommands = {
        "login",
        "logout",
        "whoami",
        "status",
        "up",
        "deploy",
        "logs",
        "run",
        "environment",
        "env",
        "variables",
        "link",
        "unlink",
        "open",
        "service",
        "domain",
        "volume",
    }

    subcommand = tokens[1]
    if subcommand not in allowed_subcommands:
        return False, f"railway subcommand '{subcommand}' not allowed"

    return True, ""


def validate_wrangler_command(command_string: str) -> tuple[bool, str]:
    """
    Validate Cloudflare Wrangler commands - only allow safe operations.

    Returns:
        Tuple of (is_allowed, reason_if_blocked)
    """
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse wrangler command"

    if len(tokens) < 2:
        return True, ""  # Allow `wrangler` alone for help

    allowed_subcommands = {
        "login",
        "logout",
        "whoami",
        "dev",
        "deploy",
        "publish",
        "pages",
        "kv",
        "r2",
        "d1",
        "secret",
        "tail",
        "init",
    }

    subcommand = tokens[1]
    if subcommand not in allowed_subcommands:
        return False, f"wrangler subcommand '{subcommand}' not allowed"

    return True, ""


def get_command_for_validation(cmd: str, segments: list[str]) -> str:
    """
    Find the specific command segment that contains the given command.

    Args:
        cmd: The command name to find
        segments: List of command segments

    Returns:
        The segment containing the command, or empty string if not found
    """
    for segment in segments:
        segment_commands = extract_commands(segment)
        if cmd in segment_commands:
            return segment
    return ""


async def bash_security_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None = None,
    context: SecurityContext | None = None,
) -> dict[str, Any]:
    """
    Pre-tool-use hook that validates bash commands using an allowlist.

    Only commands in ALLOWED_COMMANDS are permitted.
    Dangerous patterns (subshells, backticks) are blocked unconditionally.

    Args:
        input_data: Dict containing tool_name and tool_input
        tool_use_id: Optional tool use ID
        context: Optional SecurityContext for domain-specific rules

    Returns:
        Empty dict to allow, or {"decision": "block", "reason": "..."} to block
    """
    if input_data.get("tool_name") != "Bash":
        return {}

    command = input_data.get("tool_input", {}).get("command", "")
    if not command:
        return {}

    # Parse command and detect dangerous patterns FIRST
    # This happens before any other validation to catch obfuscation attempts
    parsed = parse_command(command)

    # Block subshells unconditionally - these can be used for obfuscation
    if parsed.has_subshell:
        return {
            "decision": "block",
            "reason": "Subshell execution ($(...) or `...`) is not allowed for security reasons",
        }

    # Block commands that failed to parse - could be obfuscation
    if parsed.parse_error:
        return {
            "decision": "block",
            "reason": f"Could not parse command (possible obfuscation): {parsed.parse_error}",
        }

    # Block pip usage - FORGE uses uv only
    if "pip install" in command or "pip3 install" in command:
        return {
            "decision": "block",
            "reason": "Use 'uv add' instead of pip. FORGE requires Astral uv for all Python packages.",
        }

    # Extract all commands from the command string
    commands = extract_commands(command)

    if not commands:
        return {
            "decision": "block",
            "reason": f"Could not parse command for security validation: {command}",
        }

    # Merge with any extra allowed commands from context
    allowed = ALLOWED_COMMANDS.copy()
    if context and context.extra_allowed_commands:
        allowed.update(context.extra_allowed_commands)

    # Split into segments for per-command validation
    segments = split_command_segments(command)

    # Check each command against the allowlist
    for cmd in commands:
        if cmd not in allowed:
            return {
                "decision": "block",
                "reason": f"Command '{cmd}' is not in the allowed commands list",
            }

        # Additional validation for sensitive commands
        if cmd in COMMANDS_NEEDING_EXTRA_VALIDATION:
            cmd_segment = get_command_for_validation(cmd, segments)
            if not cmd_segment:
                cmd_segment = command

            validators = {
                "pkill": validate_pkill_command,
                "kill": validate_kill_command,
                "chmod": validate_chmod_command,
                "rm": lambda s: validate_rm_command(s, context),
                "docker": validate_docker_command,
                "psql": lambda s: validate_psql_command(s, context),
                "curl": validate_curl_command,
                "bash": validate_script_execution,
                "sh": validate_script_execution,
                "init.sh": validate_script_execution,
                "railway": validate_railway_command,
                "wrangler": validate_wrangler_command,
            }

            if cmd in validators:
                allowed_result, reason = validators[cmd](cmd_segment)
                if not allowed_result:
                    return {"decision": "block", "reason": reason}

    return {}


def create_forge_security_context(
    domain: str | None = None,
    project: str | None = None,
    forge_root: str | None = None,
) -> SecurityContext:
    """
    Create a SecurityContext for FORGE development.

    Args:
        domain: The FORGE domain (e.g., 'codeswiftr-com')
        project: The project name within the domain
        forge_root: Path to the FORGE repository root

    Returns:
        SecurityContext configured for the domain/project
    """
    allowed_dirs = []

    if forge_root:
        allowed_dirs.append(forge_root)
        if domain:
            allowed_dirs.append(os.path.join(forge_root, domain))
            if project:
                allowed_dirs.append(os.path.join(forge_root, domain, project))

    # Extra commands for specific domains
    extra_commands: set[str] = set()

    # React Native domains need expo
    if domain == "babybit-es":
        extra_commands.add("expo")
        extra_commands.add("eas")

    return SecurityContext(
        domain=domain,
        project=project,
        allowed_dirs=allowed_dirs,
        extra_allowed_commands=extra_commands,
    )


COMMON_PASSWORDS: Final[set[str]] = {
    "password",
    "12345678",
    "123456789",
    "qwerty",
    "abc123",
    "password1",
    "111111",
    "iloveyou",
    "1234567",
    "sunshine",
    "princess",
    "admin",
    "welcome",
    "letmein",
    "football",
    "monkey",
    "dragon",
    "baseball",
    "superman",
    "michael",
    "ninja",
    "mustang",
    "password123",
    "1234567890",
    "ashley",
    "bailey",
    "passw0rd",
    "shadow",
    "123123",
    "654321",
    "starwars",
    "cheese",
    "qwerty123",
    "hello",
    "freedom",
    "whatever",
    "qazwsx",
    "jennifer",
    "hunter",
    "soccer",
    "harley",
    "andrew",
    "tigger",
    "secret",
    "purple",
    "amanda",
    "lovely",
    "jessica",
    "thomas",
    "daniel",
    "joshua",
    "matthew",
    "austin",
    "brandon",
    "robert",
    "steven",
    "thunder",
    "merlin",
    "diamond",
    "charlie",
    "chester",
    "michelle",
    "george",
    "johnny",
    "cancer",
    "sunflower",
    "hammer",
    "cookie",
    "william",
    "corvette",
    "martin",
    "heather",
    "knight",
    "maggie",
    "peanut",
    "morgan",
    "walter",
    "playboy",
    "blazer",
    "crystal",
    "123456a",
    "fuckyou",
    "amsterdam",
    "badboy",
    "spiderman",
    "scorpion",
    "qazwsxedc",
    "88888888",
    "anthony",
    "justin",
    "test",
    "q1w2e3r4",
    "jackie",
}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def validate_password_complexity(password: str) -> list[str]:
    issues: list[str] = []

    if len(password) < 8:
        issues.append("Password must be at least 8 characters long")

    if not any(c.isupper() for c in password):
        issues.append("Password must contain at least one uppercase letter")

    if not any(c.islower() for c in password):
        issues.append("Password must contain at least one lowercase letter")

    if not any(c.isdigit() for c in password):
        issues.append("Password must contain at least one digit")

    special_chars = set(string.punctuation)
    if not any(c in special_chars for c in password):
        issues.append("Password must contain at least one special character")

    if password.lower() in COMMON_PASSWORDS:
        issues.append("Password is too common. Choose a more unique password")

    return issues


def generate_secure_token(length: int = 32) -> str:
    if length < 16:
        raise ValueError("Token length must be at least 16 characters")
    return secrets.token_urlsafe(length)
