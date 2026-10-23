import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Protocol

import boto3
from botocore.exceptions import ClientError

from core.router import router

logger = logging.getLogger(__name__)


class Scheduler(Protocol):
    def schedule(self, name: str, run_at: datetime, job: str, payload: dict) -> None: ...
    def schedule_recurring(self, name: str, interval_seconds: int, job: str, payload: dict,
                           *, start_at: datetime | None = None, end_at: datetime | None = None) -> None: ...
    def cancel(self, name: str) -> None: ...


def _recurring_start(interval_seconds, start_at):
    if start_at is None:
        return datetime.now(UTC) + timedelta(seconds=interval_seconds)
    if start_at.tzinfo is None or start_at.utcoffset() is None:
        raise ValueError("The first recurring run must include a timezone.")
    return start_at.astimezone(UTC)


def _recurring_end(first_run, end_at):
    if end_at is None:
        return None
    if end_at.tzinfo is None or end_at.utcoffset() is None or end_at <= first_run:
        raise ValueError("The recurring end must include a timezone and follow the first run.")
    return end_at.astimezone(UTC)


class EventBridgeScheduler:
    """Creates one-off and recurring schedules that invoke this Lambda."""

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

    def schedule_recurring(self, name, interval_seconds, job, payload, *, start_at=None, end_at=None):
        hours, remainder = divmod(interval_seconds, 3600)
        if hours < 1 or remainder:
            raise ValueError("Recurring schedules must use whole hours.")
        first_run = _recurring_start(interval_seconds, start_at)
        end = _recurring_end(first_run, end_at)
        bounds = {"EndDate": end, "ActionAfterCompletion": "DELETE"} if end is not None else {}
        try:
            self._client.create_schedule(
                Name=name, ClientToken=name,
                ScheduleExpression=f"rate({hours} {'hour' if hours == 1 else 'hours'})",
                StartDate=first_run,
                **bounds,
                FlexibleTimeWindow={"Mode": "OFF"},
                Target={"Arn": self._target_arn, "RoleArn": self._role_arn,
                        "Input": json.dumps({"job": job, "payload": payload})},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConflictException":
                raise

    def cancel(self, name):
        try:
            self._client.delete_schedule(Name=name)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                raise


class InProcessScheduler:
    """Runs jobs in this process for local runs. A real schedule would fire the deployed Lambda and reply from its bot."""

    def __init__(self, telegram):
        self._telegram = telegram
        self._recurring = {}
        self._lock = threading.RLock()

    def schedule(self, name, run_at, job, payload):
        delay = max(0.0, (run_at - datetime.now(UTC)).total_seconds())
        timer = threading.Timer(delay, router.dispatch_job, args=(job, payload, self._telegram), kwargs={"scheduler": self})
        timer.daemon = True
        timer.start()

    def schedule_recurring(self, name, interval_seconds, job, payload, *, start_at=None, end_at=None):
        if interval_seconds <= 0:
            raise ValueError("The reminder interval must be positive.")
        first_run = _recurring_start(interval_seconds, start_at)
        end = _recurring_end(first_run, end_at)
        interval = timedelta(seconds=interval_seconds)
        with self._lock:
            if name in self._recurring:
                return
            stopped = threading.Event()
            self._recurring[name] = (stopped, None)

        def queue(run_at):
            with self._lock:
                if stopped.is_set():
                    return
                if end is not None and run_at > end:
                    self.cancel(name)
                    return
                delay = max(0.0, (run_at - datetime.now(UTC)).total_seconds())
                timer = threading.Timer(delay, fire, args=(run_at,))
                timer.daemon = True
                self._recurring[name] = (stopped, timer)
                timer.start()

        def fire(run_at):
            if stopped.is_set():
                return
            if end is not None and datetime.now(UTC) >= end:
                self.cancel(name)
                return
            try:
                router.dispatch_job(job, payload, self._telegram, scheduler=self)
            except Exception:
                logger.exception("A local recurring job failed")
            finally:
                # Keep the original clock time even if a job runs slowly or
                # the computer wakes after missing one or more occurrences.
                next_run = run_at + interval
                now = datetime.now(UTC)
                if next_run <= now:
                    next_run += interval * ((now - next_run) // interval + 1)
                queue(next_run)

        queue(first_run)

    def cancel(self, name):
        with self._lock:
            existing = self._recurring.pop(name, None)
            if existing:
                stopped, timer = existing
                stopped.set()
                if timer:
                    timer.cancel()
