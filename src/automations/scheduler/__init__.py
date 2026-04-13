"""
Scheduler package — scheduled task management for the Agent.

Provides:
  - scheduler: global TaskScheduler instance (start/stop/add_task/remove_task)
  - store: task persistence (create_task/list_tasks/delete_task/update_task)
"""

from automations.scheduler.engine import scheduler

__all__ = ["scheduler"]
