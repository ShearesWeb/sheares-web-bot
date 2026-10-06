import json
import threading
from datetime import UTC, datetime, timedelta, timezone

import boto3

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
