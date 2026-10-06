import json
import threading
from datetime import UTC, datetime
from typing import Protocol

import boto3

from core.router import router


class Scheduler(Protocol):
    def schedule(self, name: str, run_at: datetime, job: str, payload: dict) -> None: ...


class EventBridgeScheduler:
    """Creates one-off schedules that invoke this Lambda with a job event."""

    def __init__(self, role_arn, target_arn):
        self._client = boto3.client("scheduler")
        self._role_arn = role_arn
        self._target_arn = target_arn

    def schedule(self, name, run_at, job, payload):
        # at() expressions are evaluated in UTC unless a schedule timezone is given.
        run_at_utc = run_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        self._client.create_schedule(
            Name=name,
            ScheduleExpression=f"at({run_at_utc})",
            Target={
                "Arn": self._target_arn,
                "RoleArn": self._role_arn,
                "Input": json.dumps({"job": job, "payload": payload}),
            },
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
        )


class InProcessScheduler:
    """Runs jobs in this process for local runs. A real schedule would fire the deployed Lambda and reply from its bot."""

    def __init__(self, telegram):
        self._telegram = telegram

    def schedule(self, name, run_at, job, payload):
        delay = max(0.0, (run_at - datetime.now(UTC)).total_seconds())
        timer = threading.Timer(delay, router.dispatch_job, args=(job, payload, self._telegram))
        timer.daemon = True
        timer.start()
