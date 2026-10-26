import json
import threading
from datetime import UTC, datetime, timedelta, timezone

import boto3
import pytest

from core import scheduler as scheduler_module
from core.router import Router
from core.scheduler import EventBridgeScheduler, InProcessScheduler

ROLE_ARN = "arn:aws:iam::123456789012:role/scheduler"
TARGET_ARN = "arn:aws:lambda:ap-southeast-1:123456789012:function:preprod-app-func-sheareswebbot-bot"


def test_eventbridge_schedule_targets_job_event_in_utc():
    sgt = timezone(timedelta(hours=8))

    EventBridgeScheduler(ROLE_ARN, TARGET_ARN).schedule(
        "Laundry_A_W1_1", datetime(2026, 10, 6, 20, 30, tzinfo=sgt), "laundry_done", {"chat_id": 10},
    )

    got = boto3.client("scheduler").get_schedule(Name="Laundry_A_W1_1")
    assert got["ScheduleExpression"] == "at(2026-10-06T12:30:00)"
    assert got["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert got["Target"]["Arn"] == TARGET_ARN
    assert got["Target"]["RoleArn"] == ROLE_ARN
    assert json.loads(got["Target"]["Input"]) == {"job": "laundry_done", "payload": {"chat_id": 10}}


def test_in_process_scheduler_runs_job_with_its_telegram(monkeypatch, services):
    router = Router()
    ran = threading.Event()
    seen = []

    @router.job("laundry_done")
    def done(payload, telegram):
        seen.append((payload, telegram))
        ran.set()

    monkeypatch.setattr(scheduler_module, "router", router)

    InProcessScheduler(services.telegram).schedule("Laundry_A_W1_1", datetime.now(UTC), "laundry_done", {"chat_id": 10})

    assert ran.wait(timeout=2)
    assert seen == [({"chat_id": 10}, services.telegram)]


def test_eventbridge_recurring_schedule_has_explicit_start_end_and_cleanup():
    first = datetime(2026, 10, 26, 4, tzinfo=UTC)
    end = first + timedelta(days=26, minutes=1)

    EventBridgeScheduler(ROLE_ARN, TARGET_ARN).schedule_recurring(
        "Split_period", 48 * 3600, "split_bill_reminder", {}, start_at=first, end_at=end,
    )

    got = boto3.client("scheduler").get_schedule(Name="Split_period")
    assert got["ScheduleExpression"] == "rate(48 hours)"
    assert got["StartDate"] == first and got["EndDate"] == end
    assert got["ActionAfterCompletion"] == "DELETE"
    assert got["FlexibleTimeWindow"] == {"Mode": "OFF"}


def test_recurring_end_must_follow_the_first_run():
    first = datetime(2026, 10, 26, 4, tzinfo=UTC)
    with pytest.raises(ValueError, match="follow the first run"):
        EventBridgeScheduler(ROLE_ARN, TARGET_ARN).schedule_recurring(
            "Split_period", 48 * 3600, "split_bill_reminder", {}, start_at=first, end_at=first,
        )


def test_local_recurring_schedule_does_not_queue_another_run_after_its_end(monkeypatch, services):
    timers, seen = [], []

    class Timer:
        def __init__(self, delay, callback, args=()):
            self.callback, self.args = callback, args
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    router = Router()
    router.job("bounded")(lambda payload, telegram: seen.append(payload))
    monkeypatch.setattr(scheduler_module, "router", router)
    monkeypatch.setattr(scheduler_module.threading, "Timer", Timer)
    first = datetime.now(UTC)
    scheduler = InProcessScheduler(services.telegram)
    scheduler.schedule_recurring("bounded", 3600, "bounded", {}, start_at=first, end_at=first + timedelta(minutes=1))

    timers[0].callback(*timers[0].args)

    assert seen == [{}]
    assert len(timers) == 1
