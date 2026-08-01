import logging
from typing import TextIO


def configure_logging(level: str | int = "WARNING", *, stream: TextIO | None = None) -> None:
    """Configure concise process-wide stdlib logging without writing to CLI stdout."""
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s %(message)s",
        stream=stream,
        force=True,
    )
