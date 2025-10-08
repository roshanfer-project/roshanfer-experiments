"""Utility helpers for experiment execution framework."""

from __future__ import annotations

import re
from typing import Mapping, Any

_PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def format_query(template: str, values: Mapping[str, Any], *, strict: bool = False) -> str:
    """Format a PromQL (or generic) query template with {placeholders}.

    Only replaces simple placeholders of the form {identifier}. Curly braces belonging
    to PromQL label selectors (e.g., metric{label="value"}) are unaffected—only the
    inner {placeholder} patterns matching the regex are considered.

    Args:
        template: Query string containing zero or more {placeholders}.
        values: Mapping providing values for placeholders.
        strict: If True, raises KeyError when a placeholder has no provided value.
                If False (default), unknown placeholders are left unchanged.

    Returns:
        The formatted string.

    Examples:
        format_query('rate(foo_total{method="{method}"}[{window}])', {'method': 'search', 'window': '10s'})
        -> 'rate(foo_total{method="search"}[10s])'
    """

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in values:
            return str(values[key])
        if strict:
            raise KeyError(f"Missing value for placeholder '{key}' in template: {template}")
        # Leave untouched
            
        return match.group(0)

    return _PLACEHOLDER_PATTERN.sub(repl, template)


__all__ = ["format_query"]
