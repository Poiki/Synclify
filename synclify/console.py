from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

console = Console()

logger = logging.getLogger("synclify")
logger.setLevel(logging.INFO)
handler = RichHandler(
    console=console,
    show_time=False,
    show_level=True,
    show_path=False,
    markup=True,
)
if not logger.handlers:
    logger.addHandler(handler)

__all__ = ["console", "logger"]
