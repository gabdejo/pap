# tests/test_job_assignment.py
# ---------------------------------------------------------------
# Scheduler job assignment: a job runs on the machine(s) its
# `machine` field in config/schedules.yaml names, and nowhere else.
# ---------------------------------------------------------------

import pytest

from src.shared.config_loader import jobs_for_machine, load_schedules


def test_assignment():
    cfg = {
        "a": {"machine": "i7"},
        "b": {"machine": "BLOOMBERG-WS"},
        "c": {"machine": ["i7", "bloomberg-ws"]},
    }
    assert jobs_for_machine(cfg, "I7") == ["a", "c"]
    assert jobs_for_machine(cfg, "bloomberg-ws") == ["b", "c"]
    # A dev laptop, or a machine with no machine_id, owns nothing
    # whatever its capability flags say.
    assert jobs_for_machine(cfg, "laptop") == []
    assert jobs_for_machine(cfg, "unknown") == []


def test_unassigned_job_is_an_error():
    with pytest.raises(KeyError):
        jobs_for_machine({"a": {"hour": 9}}, "i7")


def test_real_schedules_all_assigned():
    jobs_for_machine(load_schedules()["jobs"], "anything")  # raises if one lacks `machine`
