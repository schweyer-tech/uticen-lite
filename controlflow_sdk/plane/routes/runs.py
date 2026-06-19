from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    pass
