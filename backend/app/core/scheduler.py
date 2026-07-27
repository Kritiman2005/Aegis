import asyncio
import json
import logging
from datetime import datetime, timedelta
from app.db.database import SessionLocal
from app.db.models import ScheduledJob
from app.mcp.registry import mcp_registry
from app.api.websocket import manager  # We will use this to send toasts

logger = logging.getLogger(__name__)

class SchedulerDaemon:
    def __init__(self):
        self._running = False
        self._task = None

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("Scheduler Daemon started.")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("Scheduler Daemon stopped.")

    async def _loop(self):
        while self._running:
            try:
                await self.check_and_run_jobs()
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")
            
            await asyncio.sleep(60)  # Check every minute

    def _calculate_next_run(self, cron_expr: str, from_time: datetime) -> datetime:
        # For simplicity in this local MVP, we'll support simple intervals
        if cron_expr == 'every_1_min':
            return from_time + timedelta(minutes=1)
        elif cron_expr == 'every_1_hour':
            return from_time + timedelta(hours=1)
        elif cron_expr == 'every_1_day':
            return from_time + timedelta(days=1)
        else:
            return from_time + timedelta(hours=24) # fallback

    async def check_and_run_jobs(self):
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            
            # Find jobs that are due
            due_jobs = db.query(ScheduledJob).filter(
                ScheduledJob.status == "active",
                ScheduledJob.next_run_at <= now
            ).all()

            if not due_jobs:
                return

            # Check Resource Contention: Is the system busy?
            # We consider it busy if any active websocket session is NOT in IDLE state.
            is_busy = False
            for session in manager.sessions.values():
                if session.state.name != "IDLE":
                    is_busy = True
                    break

            for job in due_jobs:
                # Job Staleness Policy
                if now - job.next_run_at > timedelta(hours=1):
                    logger.warning(f"Job {job.id} skipped due to staleness (>1 hour late).")
                    job.status = "missed"
                    # Notify UI
                    await manager.broadcast_toast(f"Scheduled job {job.id} missed due to system being busy.")
                    db.commit()
                    continue

                if is_busy:
                    logger.info(f"System is busy. Deferring job {job.id}.")
                    continue # Will try again in 1 minute

                # Run the job
                logger.info(f"Executing scheduled job {job.id}")
                try:
                    await self._execute_frozen_plan(job, db)
                except ValueError as ve:
                    logger.error(f"Job {job.id} failed due to schema drift: {ve}")
                    job.status = "FAILED - SCHEMA DRIFT"
                    await manager.broadcast_toast(f"Scheduled job failed due to schema drift.")
                except Exception as ex:
                    logger.error(f"Job {job.id} failed: {ex}")
                    job.status = "failed"
                    await manager.broadcast_toast(f"Scheduled job failed: {ex}")

                # Calculate next run
                if job.status == "active":
                    job.last_run_at = now
                    job.next_run_at = self._calculate_next_run(job.cron_expression, now)
                
                db.commit()

        finally:
            db.close()

    async def _execute_frozen_plan(self, job: ScheduledJob, db):
        plan = json.loads(job.frozen_plan_json)
        
        # 1. Loud Failure Schema Check
        valid_tools = {t["name"] for t in mcp_registry.list_all_tools()}
        for step in plan:
            tool_name = step.get("tool")
            if tool_name not in valid_tools:
                raise ValueError(f"Tool `{tool_name}` is no longer available. Aborting job.")

        # 2. Execute
        for step in plan:
            tool_name = step.get("tool")
            arguments = step.get("arguments", {})
            logger.info(f"[Job {job.id}] Executing {tool_name}")
            
            await asyncio.to_thread(
                lambda t=tool_name, a=arguments: mcp_registry.call_tool(t, a)
            )
            
        await manager.broadcast_toast(f"Scheduled plan (Job {job.id}) completed successfully.")

scheduler_daemon = SchedulerDaemon()
