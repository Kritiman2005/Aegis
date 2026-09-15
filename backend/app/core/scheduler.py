import asyncio
import json
import logging
from datetime import datetime, timedelta
from app.db.database import SessionLocal
from app.db.models import ScheduledJob
from app.mcp.registry import mcp_registry
from app.api.websocket import manager, agent_sessions  # ConnectionManager (for toasts) + live ChatAgent sessions (for busy check)

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

            try:
                await self.check_and_run_workflow_triggers()
            except Exception as e:
                logger.error(f"Error checking workflow schedule triggers: {e}")

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
            # We consider it busy if any active ChatAgent session is NOT in IDLE state.
            # (AgentState is a plain class of string constants, not an Enum — session.state
            # is already the string itself, e.g. "IDLE", so no ".name" lookup needed.)
            is_busy = False
            for session in agent_sessions.values():
                if session.state != "IDLE":
                    is_busy = True
                    break

            for job in due_jobs:
                # Job Staleness Policy
                if now - job.next_run_at > timedelta(hours=1):
                    logger.warning(f"Job {job.id} skipped due to staleness (>1 hour late).")
                    job.status = "missed"
                    # Notify UI
                    await manager.broadcast_json({"type": "toast", "content": f"Scheduled job {job.id} missed due to system being busy."})
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
                    await manager.broadcast_json({"type": "toast", "content": "Scheduled job failed due to schema drift."})
                except Exception as ex:
                    logger.error(f"Job {job.id} failed: {ex}")
                    job.status = "failed"
                    await manager.broadcast_json({"type": "toast", "content": f"Scheduled job failed: {ex}"})

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
            
        await manager.broadcast_json({"type": "toast", "content": f"Scheduled plan (Job {job.id}) completed successfully."})

    async def check_and_run_workflow_triggers(self):
        """
        The workflow-canvas equivalent of check_and_run_jobs, on the same
        60s cadence — scans every saved Workflow's graph for
        "schedule_trigger" nodes (data.intervalMinutes, user-set on the
        node itself) and runs app.core.workflows.engine.run_schedule_workflow
        for whichever ones are due. Due-ness is tracked per (workflow, node)
        in WorkflowNodeState under key "next_run_at" — a trigger newly added
        to a graph gets its first next_run_at set one interval from now
        (not run immediately the moment it's saved) the first time this
        loop ever sees it.
        """
        from app.db.database import SessionLocal
        from app.db.models import Workflow, WorkflowNodeState
        from app.core.workflows.engine import run_schedule_workflow

        now = datetime.utcnow()
        due: list[tuple[int, str]] = []

        db = SessionLocal()
        try:
            for wf in db.query(Workflow).all():
                try:
                    graph = json.loads(wf.graph_json)
                except (TypeError, ValueError):
                    continue

                for node in graph.get("nodes", []):
                    data = node.get("data", {})
                    if data.get("kind") != "schedule_trigger":
                        continue

                    node_id = node["id"]
                    interval_minutes = data.get("intervalMinutes")
                    if not interval_minutes or interval_minutes <= 0:
                        continue  # not configured yet — nothing to schedule

                    state = db.query(WorkflowNodeState).filter(
                        WorkflowNodeState.workflow_id == wf.id,
                        WorkflowNodeState.node_id == node_id,
                        WorkflowNodeState.key == "next_run_at",
                    ).first()

                    if not state or not state.value:
                        next_run_at = now + timedelta(minutes=interval_minutes)
                        if state:
                            state.value = next_run_at.isoformat()
                        else:
                            db.add(WorkflowNodeState(
                                workflow_id=wf.id, node_id=node_id,
                                key="next_run_at", value=next_run_at.isoformat(),
                            ))
                        db.commit()
                        continue

                    if datetime.fromisoformat(state.value) <= now:
                        due.append((wf.id, node_id))
                        state.value = (now + timedelta(minutes=interval_minutes)).isoformat()
                        db.commit()
        finally:
            db.close()

        for workflow_id, node_id in due:
            try:
                logger.info(f"Running schedule trigger '{node_id}' on workflow {workflow_id}")
                await run_schedule_workflow(workflow_id, node_id)
            except Exception as e:
                logger.error(f"Schedule trigger '{node_id}' on workflow {workflow_id} failed: {e}")
                await manager.broadcast_json({
                    "type": "toast",
                    "content": f"Scheduled workflow step failed: {e}",
                })

scheduler_daemon = SchedulerDaemon()
