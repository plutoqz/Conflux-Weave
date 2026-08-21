from conflux_weave.core import RunStatus


def test_run_terminal_statuses_are_explicit() -> None:
    terminal = {status.value for status in RunStatus if status.is_terminal}
    assert terminal == {"succeeded", "partial", "failed", "cancelled", "expired"}


def test_active_run_statuses_are_not_terminal() -> None:
    active = {"accepted", "queued", "running", "waiting_for_user", "cancelling"}
    assert all(not RunStatus(status).is_terminal for status in active)
