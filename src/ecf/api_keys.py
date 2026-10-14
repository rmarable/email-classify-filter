"""The API keys `ecf models api-key set --provider ...` stores (SPEC §7.6, §11.6; v3.0.0 step 2a,
OD-503).

The provider list is fixed: a new provider is a new destination for data, so it needs an operator
decision first (OD-503). The same checks run in the CLI before anything is sent and again in the
service. They follow TypeSafe's SDK key rules (docs.typesafe.ai/sdk/python/usage, read 2026-10-14):
the ends stripped; no space inside, no control or non-ASCII character. An Anthropic key starts
`sk-ant-`, and a key with that prefix is refused for TypeSafe (a pasted key of the wrong kind
never leaves this computer). TypeSafe documents no prefix of its own (/introduction/quickstart,
read 2026-10-14), so none is required. No message ever quotes the key.
"""

from __future__ import annotations

from ecf.errors import InvalidInputError

PROVIDERS = ("anthropic", "typesafe")
ANTHROPIC_PREFIX = "sk-ant-"
MAX_KEY_CHARS = 400
NAME_PROVIDER = "name the provider: --provider anthropic or --provider typesafe"
LABEL = {"anthropic": "Anthropic API key, sk-ant-...", "typesafe": "TypeSafe API key"}


def check_provider(provider: str | None) -> str:
    """`provider` when it is one ecf knows; otherwise refused, naming both."""
    if provider not in PROVIDERS:
        raise InvalidInputError(NAME_PROVIDER)
    return provider


def check_key(provider: str, key: str) -> str:
    """The key with its ends stripped, after the checks above. Raises InvalidInputError."""
    check_provider(provider)
    k = key.strip()
    if not k:
        raise InvalidInputError("nothing entered")
    if len(k) > MAX_KEY_CHARS:
        raise InvalidInputError(f"that isn't an API key: longer than {MAX_KEY_CHARS} characters")
    if any(c.isspace() for c in k):
        raise InvalidInputError(f"a {LABEL[provider]} has no spaces: paste it without spaces")
    if any(not c.isascii() or not c.isprintable() for c in k):
        raise InvalidInputError("that isn't an API key: it has a control or non-ASCII character")
    if provider == "anthropic" and not k.startswith(ANTHROPIC_PREFIX):
        raise InvalidInputError(f"an Anthropic API key starts {ANTHROPIC_PREFIX}")
    if provider == "typesafe" and k.startswith(ANTHROPIC_PREFIX):
        raise InvalidInputError("that is an Anthropic key (sk-ant-); TypeSafe's key is another")
    return k
