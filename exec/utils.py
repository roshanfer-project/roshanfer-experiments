"""Utility helpers for experiment execution framework."""

from __future__ import annotations

import re
import re
import sys
import subprocess
import logging
from pathlib import Path
from typing import Mapping, Any, List, Optional

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


def run_with_logging(cmd: List[str], env: dict, log_path: Optional[Path] = None):
    """
    Runs a command, streaming output to stdout and optionally to a log file.
    Similar to 'tee'. 
    """
    # If no log path, just use normal subprocess.run to keep it simple, 
    # unless we specifically want to force pipe usage. 
    # But 'tee' style is nice even for just stdout often.
    # Let's use Popen to ensure real-time output.
    
    logging.info(f"Running command: {' '.join(cmd)}")
    if log_path:
        logging.info(f"Logging output to: {log_path}")
    
    # Open log file if needed
    log_file = None
    if log_path:
        log_file = open(log_path, "w")
        
    try:
        # We merge stderr into stdout for simplicity
        process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1 # Line buffered
        )
        
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
                if log_file:
                    log_file.write(line)
                    log_file.flush()
                    
        rc = process.poll()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)
            
    finally:
        if log_file:
            log_file.close()


__all__ = ["format_query", "run_with_logging"]


