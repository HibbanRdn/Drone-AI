from __future__ import annotations

import pytest

from gap_plot_ai.state import AppState, StateMachine


def test_state_machine_full_start_stop_and_idempotence() -> None:
    states = []
    machine = StateMachine(lambda snapshot: states.append(snapshot["state"]))
    assert machine.transition(AppState.STARTING)
    assert not machine.transition(AppState.STARTING)
    assert machine.transition(AppState.WARMING_UP)
    assert machine.transition(AppState.RUNNING)
    assert machine.transition(AppState.STOPPING)
    assert machine.transition(AppState.IDLE)
    assert states == ["STARTING", "WARMING_UP", "RUNNING", "STOPPING", "IDLE"]


def test_error_is_visible_and_recoverable_without_process_restart() -> None:
    machine = StateMachine()
    machine.fail("ENGINE_MISSING", "engine not found")
    assert machine.state == AppState.ERROR
    assert machine.last_error is not None
    assert machine.last_error.code == "ENGINE_MISSING"
    assert machine.transition(AppState.STARTING)


def test_invalid_transition_is_rejected() -> None:
    machine = StateMachine()
    with pytest.raises(RuntimeError, match="IDLE -> RUNNING"):
        machine.transition(AppState.RUNNING)
