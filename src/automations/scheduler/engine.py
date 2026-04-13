"""
Scheduler engine — APScheduler-based cron scheduler with persistent task recovery.

Usage:
    from automations.scheduler import scheduler
    scheduler.start()   # Call once at application startup
    scheduler.stop()    # Call at shutdown
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from automations.scheduler.store import list_tasks, get_task
from automations.scheduler.executor import execute_task

logger = logging.getLogger("scheduler.engine")


class TaskScheduler:
    """APScheduler wrapper that manages scheduled agent tasks."""

    def __init__(self):
        self._scheduler = BackgroundScheduler(
            job_defaults={
                "coalesce": True,       # Collapse missed runs into one
                "max_instances": 1,     # No parallel runs of the same task
                "misfire_grace_time": 300,  # 5 min grace for missed fires
            }
        )
        self._started = False

    def start(self):
        """Start the scheduler and reload all persisted tasks."""
        if self._started:
            return

        self._scheduler.start()
        self._started = True
        logger.info("Scheduler started")

        # Reload persisted tasks
        self._reload_tasks()

    def stop(self):
        """Shut down the scheduler gracefully."""
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("Scheduler stopped")

    def add_task(self, task: dict):
        """Register a task with APScheduler using its cron expression."""
        task_id = task["id"]
        cron = task["cron"]

        try:
            trigger = CronTrigger.from_crontab(cron)
        except ValueError as e:
            logger.error(f"Invalid cron expression '{cron}' for task {task_id}: {e}")
            raise ValueError(f"Invalid cron expression: {cron}") from e

        self._scheduler.add_job(
            func=self._execute_wrapper,
            trigger=trigger,
            id=task_id,
            args=[task_id],
            replace_existing=True,
            name=task.get("task_name", task_id),
        )
        logger.info(f"Task scheduled: {task_id} with cron '{cron}'")

    def remove_task(self, task_id: str):
        """Remove a task from APScheduler."""
        try:
            self._scheduler.remove_job(task_id)
            logger.info(f"Task removed from scheduler: {task_id}")
        except Exception:
            logger.debug(f"Task {task_id} was not in scheduler (already removed?)")

    def update_task_schedule(self, task_id: str, cron: str = None, enabled: bool = None):
        """Update a task's schedule or pause/resume it."""
        if enabled is False:
            self._scheduler.pause_job(task_id)
            logger.info(f"Task paused: {task_id}")
            return
        elif enabled is True:
            try:
                self._scheduler.resume_job(task_id)
                logger.info(f"Task resumed: {task_id}")
            except Exception:
                # Job might not exist (e.g., was disabled before restart), re-add it
                task = get_task(task_id)
                if task:
                    self.add_task(task)

        if cron:
            try:
                trigger = CronTrigger.from_crontab(cron)
                self._scheduler.reschedule_job(task_id, trigger=trigger)
                logger.info(f"Task rescheduled: {task_id} with cron '{cron}'")
            except ValueError as e:
                raise ValueError(f"Invalid cron expression: {cron}") from e

    def get_next_run(self, task_id: str) -> str | None:
        """Get the next scheduled run time for a task."""
        job = self._scheduler.get_job(task_id)
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None

    def _execute_wrapper(self, task_id: str):
        """Wrapper that loads the latest task data before execution."""
        task = get_task(task_id)
        if not task:
            logger.warning(f"Task {task_id} not found in store, removing from scheduler")
            self.remove_task(task_id)
            return

        if not task.get("enabled", True):
            logger.info(f"Task {task_id} is disabled, skipping execution")
            return

        execute_task(task)

    def _reload_tasks(self):
        """Reload all enabled tasks from the persistent store."""
        tasks = list_tasks()
        loaded = 0
        for task in tasks:
            if task.get("enabled", True):
                try:
                    self.add_task(task)
                    loaded += 1
                except ValueError as e:
                    logger.error(f"Failed to reload task {task['id']}: {e}")
        logger.info(f"Reloaded {loaded} scheduled task(s) from store")


# Global singleton
scheduler = TaskScheduler()
