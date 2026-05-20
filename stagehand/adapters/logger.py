import logging

from stagehand.ports.logger import Logger


class StdlibLogger(Logger):
    """Wraps Python's stdlib logging module.

    By default silences httpx and httpcore at INFO level — the two libraries
    that produce the noisy "HTTP Request: POST ..." lines when using the
    Anthropic / OpenAI SDKs.  Set ``suppress_http_logs=False`` to keep them.
    """

    def __init__(self, name: str = "stagehand", suppress_http_logs: bool = True) -> None:
        self._logger = logging.getLogger(name)
        if suppress_http_logs:
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpcore").setLevel(logging.WARNING)

    def debug(self, message: str) -> None:
        self._logger.debug(message)

    def info(self, message: str) -> None:
        self._logger.info(message)

    def warning(self, message: str) -> None:
        self._logger.warning(message)

    def error(self, message: str) -> None:
        self._logger.error(message)


class NullLogger(Logger):
    """Silently discards all log messages."""

    def debug(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...
