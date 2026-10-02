"""The model queue's work (V1.3 steps 3, 4c; V1.4 step 8): classify new mail; run the actor on
items a rule sent to it or whose question you answered; and the local fallback's items: a C email
it took from the Claude queue is classified first. `shadow` is the fallback's shadow run."""

from __future__ import annotations

import sqlite3

from ecf_server import actor, classifier, fallback, ollama
from ecf_server.clock import Clock
from ecf_server.modelq import ItemResult
from ecf_server.ollama import Client


def work(conn: sqlite3.Connection, clock: Clock, client: Client, ready: ollama.Ready,
         item: sqlite3.Row) -> ItemResult:  # fmt: skip
    if item["status"] == "new":
        return classifier.classify_item(conn, clock, client, ready, item)
    if item["status"] == "awaiting_claude" and item["classification"] is None:
        return fallback.classify(conn, clock, client, ready, item)
    return actor.act_item(conn, clock, client, ready, item)


def shadow(conn: sqlite3.Connection, clock: Clock, client: Client, ready: ollama.Ready,
           item: sqlite3.Row) -> ItemResult:  # fmt: skip
    return fallback.shadow_item(conn, clock, client, ready, item)
