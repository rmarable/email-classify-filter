"""The model queue's work for preset A (V1.3 steps 3, 4c): classify new mail; run the actor on
items a rule sent to it or whose question you answered."""

from __future__ import annotations

import sqlite3

from ecf_server import actor, classifier, ollama
from ecf_server.clock import Clock
from ecf_server.modelq import ItemResult
from ecf_server.ollama import Client


def work(conn: sqlite3.Connection, clock: Clock, client: Client, ready: ollama.Ready,
         item: sqlite3.Row) -> ItemResult:  # fmt: skip
    if item["status"] == "new":
        return classifier.classify_item(conn, clock, client, ready, item)
    return actor.act_item(conn, clock, client, ready, item)
