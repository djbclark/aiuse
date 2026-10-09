"""cli_binary: canonical provider → local CLI binary, surfaced in account JSON."""

import pytest

from aiuse.models import AccountUsage, provider_cli_binary


@pytest.mark.parametrize(
    ("provider", "binary"),
    [
        # Verified against `which` on this machine, 2026-09-27.
        ("antigravity", "agy"),
        ("zai", "zcode"),
        ("opencode-go", "opencode"),
        ("opencode-zen", "opencode"),
        ("codex", "codex"),
        ("claude", "claude"),
        ("cursor", "cursor-agent"),
        ("copilot", "copilot"),
        ("grok", "grok"),
        ("devin", "devin"),
        ("muse", "muse"),
        ("clinepass", "cline"),
        ("qwencloud", "qwen"),
        ("alibaba", "qwen"),
    ],
)
def test_known_cli_binaries(provider: str, binary: str):
    assert provider_cli_binary(provider) == binary


@pytest.mark.parametrize(
    "provider",
    ["openrouter", "deepseek", "some-future-provider", ""],
)
def test_providers_without_local_cli_are_none(provider: str):
    assert provider_cli_binary(provider) is None


@pytest.mark.parametrize(
    ("spelling", "binary"),
    [
        ("gemini", "agy"),  # hermes / pre-normalization snapshot spelling
        ("opencodego", "opencode"),  # CodexBar/OpenUsage spelling
        ("github-copilot", "copilot"),
        ("grok-build", "grok"),  # tokscale spelling
        ("chatgpt", "codex"),
        ("qwen", "qwen"),  # alias collapses onto qwencloud, whose TUI is qwen
    ],
)
def test_alias_spellings_resolve_to_the_same_binary(spelling: str, binary: str):
    assert provider_cli_binary(spelling) == binary


def test_to_dict_includes_cli_binary():
    account = AccountUsage(source="codexbar", provider="antigravity")
    assert account.to_dict()["cli_binary"] == "agy"


def test_to_dict_cli_binary_is_null_for_api_only_providers():
    account = AccountUsage(source="openrouter", provider="openrouter")
    assert account.to_dict()["cli_binary"] is None


def test_to_dict_resolves_collector_spelling():
    # A row carrying a vendor alias spelling still resolves the binary.
    account = AccountUsage(source="codexbar", provider="opencodego")
    assert account.to_dict()["cli_binary"] == "opencode"
