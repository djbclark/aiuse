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
        ("deepseek", "codewhale"),  # CodeWhale, formerly DeepSeek-TUI
    ],
)
def test_known_cli_binaries(provider: str, binary: str):
    assert provider_cli_binary(provider) == binary


@pytest.mark.parametrize(
    "provider",
    ["openrouter", "some-future-provider", ""],
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
        ("codewhale", "codewhale"),
        ("codew", "codewhale"),  # CodeWhale's short alias
        ("oc-go", "opencode"),  # the display name before it became `opencode`
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


def test_schema16_taxonomy_fields_and_legacy_aliases():
    from aiuse.analysis.selfdescribe import SCHEMA_VERSION, SEMANTICS, enrich_snapshot
    from aiuse.models import canonical_provider, provider_pool_id, provider_transports, provider_vendor, utcnow

    assert canonical_provider("omp") == "omp"
    assert canonical_provider("pi") == "pi"
    assert canonical_provider("opencode") == "opencode-go"
    assert canonical_provider("oc-zen") == "opencode-zen"
    assert canonical_provider("cline") == "clinepass"

    assert provider_vendor("claude") == "anthropic"
    assert provider_vendor("omp") == "anthropic"
    assert provider_vendor("omp", pool_family="openai_api") == "openai"
    assert provider_pool_id("claude") == "anthropic:subscription"
    assert provider_pool_id("omp") == "anthropic:claude_api"
    assert provider_pool_id("pi") == "openai:openai_api"
    assert provider_pool_id("antigravity", "gemini_argon") == "google:gemini_argon"
    assert provider_pool_id("opencode-go") == "opencode:go"
    assert provider_transports("cursor") == ["cursor-agent", "pi"]

    acc_dict = AccountUsage(source="codexbar", provider="omp").to_dict()
    assert acc_dict["vendor"] == "anthropic"
    assert acc_dict["pool_id"] == "anthropic:claude_api"
    assert acc_dict["transports"] == ["pi"]

    snap = enrich_snapshot(
        {
            "collected_at": utcnow().isoformat(),
            "accounts": [{"source": "codexbar", "provider": "codex", "windows": []}],
        },
    )
    assert SCHEMA_VERSION == "1.7"
    assert snap["accounts"][0]["vendor"] == "openai"
    assert snap["accounts"][0]["pool_id"] == "openai:codex"
    assert snap["accounts"][0]["transports"] == ["codex"]
    assert snap["semantics"]["taxonomy_version"] == "1.6"
    assert SEMANTICS["provider_to_vendor"]["claude"] == "anthropic"
    assert SEMANTICS["pool_id_map"]["opencode-zen"] == "opencode:zen"
