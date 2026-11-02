import re

ALIASES = {
    "claude-code": "claude",
    "claude-cli": "claude",
    "gemini-cli": "gemini",
    "codex-cli": "codex",
    "open-code": "opencode",
    "grok-cli": "grok",
    "deepseek-chat": "deepseek",
    "chat-gpt": "chatgpt",
}
EXPORT_TOOLS = [
    "grok",
    "deepseek",
    "chatgpt",
    "cursor",
    "windsurf",
    "copilot",
    "cline",
    "roo",
    "continue",
    "qwen",
    "kimi",
    "mistral",
    "perplexity",
    "ollama",
    "lmstudio",
]
CONTEXT_FILES = {
    "codex": "AGENTS.md",
    "opencode": "AGENTS.md",
    "claude": "CLAUDE.md",
    "gemini": "GEMINI.md",
    "aider": "MEMBRIDGE_HANDOFF.md",
}


def tool_name(value):
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value):
        raise ValueError("Tool name must use 1-64 lowercase letters, digits or hyphens")
    return ALIASES.get(value, value)


def context_filename(value):
    # All filenames are fixed basenames; arbitrary tool names never become filesystem paths.
    return CONTEXT_FILES.get(tool_name(value))
