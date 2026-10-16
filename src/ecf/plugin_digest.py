"""What an `ecf claude` client renders for Claude Code, as a digest (v2.2.0; SPEC §10.3): the
plugin templates (the skills and the agent prompts). `ecf claude` sends it with its version when
it opens a session; the service computes it from its own install and refuses a session that
differs, and a Claude eval's result records it."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable


def _files(node: Traversable, prefix: str) -> Iterator[tuple[str, Traversable]]:
    for child in node.iterdir():
        rel = f"{prefix}{child.name}"
        if child.is_dir():
            yield from _files(child, rel + "/")
        else:
            yield rel, child


def digest_of(root: Traversable) -> str:
    """SHA-256 over each file's path under `root` and its bytes, in path order."""
    h = hashlib.sha256()
    for rel, f in sorted(_files(root, ""), key=lambda p: p[0]):
        data = f.read_bytes()
        h.update(f"{rel}\0{len(data)}\0".encode())
        h.update(data)
    return h.hexdigest()


@cache
def plugin_digest() -> str:
    """This install's plugin templates."""
    return digest_of(resources.files("ecf.data").joinpath("plugin"))


@cache
def actor_prompt_digest() -> str:
    """SHA-256 of the actor's instructions as shipped (`agents/actor.md`, the template before its
    agent name is filled in), so every actor agent shares it: part of the gate key (v3.0.1,
    OD-523; SPEC §9.3)."""
    return hashlib.sha256(
        resources.files("ecf.data").joinpath("plugin", "agents", "actor.md").read_bytes()
    ).hexdigest()
