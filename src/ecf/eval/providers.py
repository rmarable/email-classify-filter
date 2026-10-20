"""Generator and grader providers for `ecf eval generate` and `grade` (eval generate plan, Model
switches; P24). `--generator` and `--grader` take `<provider>[:<model>]`.

Only built adapters are listed: `claude` (DEV, build step 2). The `codex` adapter and the Mistral
API client come in step 3 with the shipped model list, the model filter and the three-family
check; until then a named provider is refused with the step it arrives in.
"""

from __future__ import annotations

from collections.abc import Callable

from ecf.errors import InvalidInputError
from ecf.eval import claude_tool
from ecf.eval.tools import Adapter
from ecf.paths import Paths

DEFAULTS = {
    ("generator", "dev"): "claude:claude-haiku-5-5",
    ("generator", "prod"): "codex:gpt-6.1-sol",
    ("grader", "dev"): "claude:claude-sonnet-5-5",
    ("grader", "prod"): "mistral:mistral-large-4-0",
}
PLANNED = {"codex": "build step 3", "mistral": "build step 3"}
# provider -> a factory taking the model id and the install's paths; filled as each is built
BUILT: dict[str, Callable[[str, Paths], Adapter]] = {"claude": claude_tool.adapter}


def parse(spec: str) -> tuple[str, str | None]:
    provider, _, model = spec.partition(":")
    if not provider:
        raise InvalidInputError(f"{spec!r}: give <provider>[:<model>]")
    return provider, model or None


def resolve(spec: str | None, role: str, mode: str, paths: Paths) -> Adapter:
    provider, model = parse(spec or DEFAULTS[(role, mode)])
    default_provider, default_model = parse(DEFAULTS[(role, mode)])
    if model is None and provider == default_provider:
        model = default_model
    factory = BUILT.get(provider)
    if factory is None:
        when = PLANNED.get(provider)
        raise InvalidInputError(f"the {provider} {role} isn't built yet ({when})" if when else
                                f"no {role} provider {provider!r}: one of"
                                f" {', '.join(sorted(PLANNED))}")  # fmt: skip
    if model is None:
        raise InvalidInputError(f"--{role} {provider}: name the model ({provider}:<model id>)")
    if mode == "prod" and provider == "claude":
        raise InvalidInputError(f"a PROD {role} can't be Claude: the classifier is (use --dev"
                                " for an all-Claude plumbing run)")  # fmt: skip
    return factory(model, paths)
