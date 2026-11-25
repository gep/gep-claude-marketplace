"""Exceptions raised by destructive-guard."""


class ConfigError(Exception):
    pass


class JevConfigError(Exception):
    """A backend (Clef or Jev) setting is missing or invalid. `kind` feeds the session warning: noKey, badUrl,
    badPrompt, badValue. With `backend` set the text starts with its name (`clef: GATE_CLEF_URL must use https`)."""

    def __init__(self, message: str, kind: str = "badValue", backend: str | None = None) -> None:
        super().__init__(f"{backend}: {message}" if backend else message)
        self.kind = kind
        self.message = message
        self.backend = backend

    def for_backend(self, backend: str) -> JevConfigError:
        """The same issue, labelled with the backend it belongs to."""
        return JevConfigError(self.message, self.kind, backend)


class UsageError(Exception):
    pass
