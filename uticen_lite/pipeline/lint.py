"""Allowlist AST deny-scan for Custom Python nodes (issue #25, Stage 2 §8).

A Custom Python node is ``rows → rows`` (transform) or ``rows → violations``
(test). The spec's core principle (§3.3) is that **custom nodes never see a
source**: all cross-source work goes through the visible Import/Join nodes. This
module is the *guardrail* layer of the three-layer enforcement stack:

1. **Allowlist AST lint at save** (this module) — parse the node's ``code`` with
   :mod:`ast`, allow a tiny pure set (imports of ``re`` / ``datetime`` /
   ``decimal`` + the provided helper module) and REJECT everything that could
   read a file or reach outside ``rows``: ``open``, ``read_csv`` / ``read_excel``,
   ``__import__``, ``eval`` / ``exec`` / ``compile``, ``globals``, and dunder
   attribute access. On a violation we surface a teaching message that names the
   "Convert to Python test" one-way door (:data:`OFFRAMP_MESSAGE`).
2. **Lexical starvation at compile** — custom nodes become module-level
   ``def _node(rows)`` (see :mod:`uticen_lite.pipeline.compile`), so
   ``sources`` is structurally out of scope.
3. **Hard export gate** — :func:`lint_pipeline` is re-run in the bundle producer
   path (``build_bundle``); the bundle is REFUSED if any custom node trips.

Threat model: this is a guardrail against *accidental* bypass, not a sandbox
against a malicious local user. So it is light, pure-Python, and layered — no
subprocess / seccomp / RestrictedPython / WASM (those fight the offline,
brittle-by-design ethos). This module is Pyodide-safe: pure stdlib ``ast``.
"""

from __future__ import annotations

import ast

from uticen_lite.pipeline.model import Pipeline

# The shared pure-helper module a custom node may import (Stage 3 ships the
# module itself; the lint allows it by name now so nodes can rely on it).
HELPER_MODULE = "cflow_helpers"

# Imports a custom node may make: a tiny pure set + the provided helper module.
_ALLOWED_IMPORTS = frozenset({"re", "datetime", "decimal", HELPER_MODULE})

# Builtins / attributes that could read a file or reach outside ``rows``. These
# are rejected wherever they appear (call target or bare name reference).
#
# ``__builtins__`` / ``__builtin__`` are denied as bare names because, indexed by
# a string literal, they reach any builtin (``__builtins__['open']``) past
# visit_Name (a dict key is a Constant, not a Name). ``getattr`` / ``setattr`` /
# ``delattr`` are denied because they reach any builtin/attr/dunder by string
# (``getattr(obj, 'read_csv')``, ``getattr(rows, '__class__')``), defeating both
# _DENIED_ATTRS and the dunder guard.
_DENIED_NAMES = frozenset(
    {
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
        "globals",
        "vars",
        "locals",
        "input",
        "breakpoint",
        "__builtins__",
        "__builtin__",
        "getattr",
        "setattr",
        "delattr",
    }
)
# Attribute names that read external data (e.g. ``pd.read_csv(...)``). Matched on
# the *attribute*, so ``df.read_csv`` is caught regardless of the receiver name.
_DENIED_ATTRS = frozenset(
    {
        "read_csv",
        "read_excel",
        "read_parquet",
        "read_json",
        "read_sql",
        "read_table",
        "read_html",
        "read_pickle",
        "to_csv",
        "to_excel",
        "to_parquet",
        "system",
        "popen",
    }
)

OFFRAMP_MESSAGE = (
    "Custom nodes can't read files — pull data in with an Import node, or "
    "convert this control to a full Python test, where source access is allowed."
)


class LintError(ValueError):
    """A Custom Python node tripped the allowlist deny-scan.

    Carries the list of human-readable reasons (one per offending construct,
    each already suffixed with :data:`OFFRAMP_MESSAGE`).
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _reason(detail: str) -> str:
    """A lint reason: the offending construct + the teaching offramp."""
    return f"{detail} — {OFFRAMP_MESSAGE}"


class _DenyScanner(ast.NodeVisitor):
    """Walk a node body's AST and collect deny-scan reasons."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    # -- imports -----------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top not in _ALLOWED_IMPORTS:
                self.errors.append(
                    _reason(f"import of {alias.name!r} is not allowed in a custom node")
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        top = (node.module or "").split(".")[0]
        if top not in _ALLOWED_IMPORTS:
            self.errors.append(
                _reason(f"import from {node.module!r} is not allowed in a custom node")
            )
        self.generic_visit(node)

    # -- name references (open, eval, __import__, globals, ...) ------------
    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _DENIED_NAMES:
            self.errors.append(_reason(f"use of {node.id!r} is not allowed in a custom node"))
        self.generic_visit(node)

    # -- attribute access (dunders + read_csv/read_excel/...) --------------
    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if attr.startswith("__") and attr.endswith("__"):
            self.errors.append(
                _reason(f"dunder attribute access {attr!r} is not allowed in a custom node")
            )
        elif attr in _DENIED_ATTRS:
            self.errors.append(
                _reason(f"call to {attr!r} (reads/writes files) is not allowed in a custom node")
            )
        self.generic_visit(node)

    # -- subscript access (string-literal builtin/attr lookup) -------------
    def visit_Subscript(self, node: ast.Subscript) -> None:
        """Reject string-literal subscript that names a denied builtin/attr.

        ``__builtins__['open']`` is already caught by visit_Name (the receiver is
        a denied Name), but a string key naming any denied builtin or
        file-reading attr (``something['read_csv']``, ``ns['open']``) is rejected
        here too — a dict-key string literal is a Constant, never a Name or
        Attribute, so neither other visitor would see it.
        """
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            name = key.value
            if name in _DENIED_NAMES:
                self.errors.append(
                    _reason(f"use of {name!r} via subscript is not allowed in a custom node")
                )
            elif name in _DENIED_ATTRS:
                self.errors.append(
                    _reason(
                        f"call to {name!r} (reads/writes files) via subscript "
                        "is not allowed in a custom node"
                    )
                )
            elif name.startswith("__") and name.endswith("__"):
                self.errors.append(
                    _reason(f"dunder access {name!r} via subscript is not allowed in a custom node")
                )
        self.generic_visit(node)


def lint_custom_code(code: str) -> list[str]:
    """Return deny-scan reasons for a Custom Python node's ``code`` (``[]`` = clean).

    The ``code`` is the raw node body (the same text compiled into a module-level
    ``def _node(rows)`` — see :func:`uticen_lite.pipeline.compile`). A
    non-empty return value means the node is rejected; each reason names the
    offending construct and ends with :data:`OFFRAMP_MESSAGE`.
    """
    try:
        # ``return`` is legal because the body is compiled inside a function; wrap
        # it so test-flavor bodies (which end in ``return out``) parse cleanly.
        tree = ast.parse("def _node(rows):\n" + _indent(code))
    except SyntaxError as exc:
        return [_reason(f"the custom node has a syntax error: {exc.msg}")]
    scanner = _DenyScanner()
    scanner.visit(tree)
    return scanner.errors


def _indent(code: str) -> str:
    """Indent ``code`` one level so it is a valid function body (handles blanks)."""
    lines = code.splitlines() or ["pass"]
    return "\n".join("    " + ln for ln in lines)


def lint_pipeline(pipeline: Pipeline) -> list[str]:
    """Deny-scan every Custom Python node in *pipeline* (``[]`` = clean).

    Each reason is prefixed with the offending node's id so a caller (the save
    handler or the export gate) can pin an inline error on that node.
    """
    errors: list[str] = []
    for node in pipeline.nodes:
        if node.type != "custom_python":
            continue
        code = str(node.config.get("code", ""))
        for reason in lint_custom_code(code):
            errors.append(f"node {node.id!r}: {reason}")
    return errors
