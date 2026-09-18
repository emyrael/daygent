"""Small AST helpers for static string literals. No eval."""

from __future__ import annotations

import ast


def literal_string(node: ast.AST | None) -> str | None:
    """Return a string only when it is a static Constant. Omit f-strings and names."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def call_function_name(func: ast.expr) -> str | None:
    """Return the simple name of a Call target (`APIRouter`, `add_node`, …)."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def keyword_value(call: ast.Call, name: str) -> ast.expr | None:
    """Return a keyword argument expression, if present."""
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def expression_text(node: ast.AST | None, limit: int = 120) -> str | None:
    """Return a compact unparsed expression. Never evaluates the node."""
    if node is None:
        return None
    try:
        text = ast.unparse(node)
    except (RecursionError, ValueError, TypeError):
        return type(node).__name__
    compact = " ".join(text.split())
    if len(compact) > limit:
        return compact[: limit - 3] + "..."
    return compact


def named_or_positional(
    call: ast.Call,
    names: tuple[str, ...],
    position: int | None = 0,
) -> ast.expr | None:
    """Return a keyword argument, else the positional argument at `position`."""
    for name in names:
        node = keyword_value(call, name)
        if node is not None:
            return node
    if position is None or position < 0 or position >= len(call.args):
        return None
    arg = call.args[position]
    if isinstance(arg, ast.Starred):
        return None
    return arg


def literal_str_dict(node: ast.AST | None) -> tuple[dict[str, str], bool]:
    """Extract string-to-string dict entries. `complete` is False if any pair is dynamic."""
    if not isinstance(node, ast.Dict):
        return {}, False
    mapping: dict[str, str] = {}
    complete = True
    for key, value in zip(node.keys, node.values, strict=False):
        if key is None:
            complete = False
            continue
        key_text = literal_string(key)
        value_text = literal_string(value)
        if key_text is None or value_text is None:
            complete = False
            continue
        mapping[key_text] = value_text
    return mapping, complete
