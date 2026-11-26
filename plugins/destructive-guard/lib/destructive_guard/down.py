"""Why there is no classifier answer, why the hook skipped the classifiers, or why the session is halted
(the Lean model's `Down`).

Lean model (formal/SimpleGate.lean) -> code:

| Lean                                          | Here                                                  |
| --------------------------------------------- | ----------------------------------------------------- |
| `Down.timeout/http/network/malformed`         | `Down.TIMEOUT/HTTP/NETWORK/MALFORMED` (any backend)   |
| `Down.envReference`                           | `Down.ENV_REFERENCE`                                  |
| `Down.badInput/pythonMissing/internalError`   | `Down.BAD_INPUT/PYTHON_MISSING/INTERNAL_ERROR`        |
| `Down.noClassifier` (halt)                    | `Down.NO_CLASSIFIER`                                  |
| `Down.jevUnavailable`, `pluginFault`, `asks`  | the properties of the same names                      |
"""

from enum import StrEnum


class Down(StrEnum):
    """Why there is no classifier answer, why the hook skipped the classifiers, or why the session is halted.
    Each one is shown as a warning."""

    TIMEOUT = "timeout"
    HTTP = "http"
    NETWORK = "network"
    MALFORMED = "malformed"
    ENV_REFERENCE = "envReference"
    BAD_INPUT = "badInput"
    PYTHON_MISSING = "pythonMissing"
    INTERNAL_ERROR = "internalError"
    NO_CLASSIFIER = "noClassifier"

    @property
    def jev_unavailable(self) -> bool:
        """A usable backend (Clef or Jev) did not give a usable answer (the model's `Down.jevUnavailable`)."""
        return self in (Down.TIMEOUT, Down.HTTP, Down.NETWORK, Down.MALFORMED)

    @property
    def plugin_fault(self) -> bool:
        """The plugin itself failed (D12): asks with a warning."""
        return self in (Down.BAD_INPUT, Down.PYTHON_MISSING, Down.INTERNAL_ERROR)

    @property
    def asks(self) -> bool:
        """Comes with an ask: everything except the `.env` skip and the halt."""
        return self.jev_unavailable or self.plugin_fault
