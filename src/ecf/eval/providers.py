"""Generator and grader providers for `ecf eval generate` and `grade` (eval generate plan, Model
switches; P24). `--generator` and `--grader` take `<provider>[:<model>]`.

The providers are the built adapters: `claude` (DEV, build step 2), `codex` (the generator) and
`mistral` (the grader, through the service; build step 3). Every model is checked against the
shipped list (`ecf.eval.models`): unknown models and labs the model filter bars are refused, and
each model only in the roles and modes it is listed for.
"""

from __future__ import annotations

from collections.abc import Callable

from ecf.errors import InvalidInputError
from ecf.eval import claude_tool, codex_tool, mistral_api, models
from ecf.eval.tools import Adapter
from ecf.paths import Paths

DEFAULTS = {
    ("generator", "dev"): "claude:claude-haiku-5-5",
    ("generator", "prod"): "codex:gpt-6.1-sol",
    ("grader", "dev"): "claude:claude-sonnet-5-5",
    ("grader", "prod"): "mistral:mistral-large-4-0",
}
# provider -> a factory taking the model id and the install's paths
BUILT: dict[str, Callable[[str, Paths], Adapter]] = {
    "claude": claude_tool.adapter, "codex": codex_tool.adapter, "mistral": mistral_api.adapter,
}  # fmt: skip


def parse(spec: str) -> tuple[str, str | None]:
    provider, _, model = spec.partition(":")
    if not provider:
        raise InvalidInputError(f"{spec!r}: give <provider>[:<model>]")
    return provider, model or None


def resolve(spec: str | None, role: str, mode: str, paths: Paths, *,
            claude_waiver: bool = False) -> Adapter:  # fmt: skip
    """The adapter for a role. In PROD, Claude never judges (it is the classifier) and writes a
    set only with `claude_waiver`: the operator's recorded choice that Claude is then scored on
    Claude-written mail, on the user's own Claude plan sign-in (OD-547, OD-559)."""
    provider, model = parse(spec or DEFAULTS[(role, mode)])
    default_provider, default_model = parse(DEFAULTS[(role, mode)])
    if model is None and provider == default_provider:
        model = default_model
    factory = BUILT.get(provider)
    if factory is None:
        raise InvalidInputError(f"no {role} provider {provider!r}: one of"
                                f" {', '.join(sorted(BUILT))}")  # fmt: skip
    if model is None:
        raise InvalidInputError(f"--{role} {provider}: name the model ({provider}:<model id>)")
    if mode == "prod" and provider == "claude" and (role != "generator" or not claude_waiver):
        raise InvalidInputError(
            f"a PROD {role} can't be Claude: the classifier is (use --dev for an all-Claude"
            " plumbing run)" if role != "generator" else
            "a PROD set written by Claude needs --claude-generator: you accept that Claude is"
            " then scored on Claude-written mail, and it runs on your own Claude plan sign-in,"
            " for your own use only")  # fmt: skip
    models.check(provider, model, role, mode)
    return factory(model, paths)
