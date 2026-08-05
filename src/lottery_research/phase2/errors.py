from __future__ import annotations

PASS = 0
REJECTED = 2
ENVIRONMENT_FAILURE = 3
INVALID_CONTRACT = 4
EVIDENCE_MISMATCH = 5
HOLD = 20


class Phase2Error(Exception):
    def __init__(self, message: str, *, exit_code: int, terminal: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.terminal = terminal


class InvalidContract(Phase2Error):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=INVALID_CONTRACT, terminal="INVALID_CONTRACT")


class EvidenceMismatch(Phase2Error):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EVIDENCE_MISMATCH, terminal="EVIDENCE_MISMATCH")


class Rejected(Phase2Error):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=REJECTED, terminal="REJECTED")


class Hold(Phase2Error):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=HOLD, terminal="HOLD")

