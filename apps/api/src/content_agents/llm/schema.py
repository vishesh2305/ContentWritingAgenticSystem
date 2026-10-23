"""Checks on JSON schemas sent to the model as structured-output formats."""

from typing import Any


def find_unbounded_arrays(schema: dict[str, Any]) -> list[str]:
    """Dotted paths of array nodes with no `maxItems`.

    Small local models can keep appending items until they hit the token limit,
    so every list the model fills must be bounded.
    """
    found: list[str] = []
    _walk(schema, "", found)
    return found


def _walk(node: Any, path: str, found: list[str]) -> None:
    if isinstance(node, dict):
        if node.get("type") == "array" and "maxItems" not in node:
            found.append(path)
        for key, value in node.items():
            _walk(value, f"{path}.{key}" if path else key, found)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _walk(item, f"{path}.{index}", found)
