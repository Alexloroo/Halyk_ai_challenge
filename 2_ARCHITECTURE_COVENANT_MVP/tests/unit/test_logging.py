import logging
from io import StringIO

from halyk_covenants.logging import configure_logging


def test_configure_logging_emits_level_logger_and_message() -> None:
    stream = StringIO()
    configure_logging("DEBUG", stream=stream)

    logging.getLogger("halyk_covenants.test").debug("evaluation started")

    assert "DEBUG halyk_covenants.test evaluation started" in stream.getvalue()
