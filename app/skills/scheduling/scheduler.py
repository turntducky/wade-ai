import time
import asyncio
import dateparser

from datetime import datetime

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.skills.registry import register_tool

wade_clock = AsyncIOScheduler()

async def _agent_wakeup_callback(task_prompt: str, job_id: str):
    """This is the function that gets called when a scheduled job triggers. It creates a synthetic system prompt and processes it through the orchestrator as if W.A.D.E. had just received it, but with a special session ID to indicate it's a background task."""
    from app.core.orchestrator import orchestrator
    process_agent_request = orchestrator.process

    print(f"\n⏰ [W.A.D.E. INTERNAL CLOCK] Waking up for task: {job_id}")
    
    synthetic_prompt = (
        f"--- SYSTEM ALERT: SCHEDULED TASK TRIGGERED ---\n"
        f"Job Name: {job_id}\n"
        f"Task Instructions: {task_prompt}\n"
        f"Execute this task immediately using your tools. If the result is important or requires human attention, "
        f"use your tools to write a summary in the workspace or update memory. If it is a routine background check with no errors, just log it silently."
    )

    try:
        background_session_id = f"cron_{job_id}_{int(time.time())}"
        
        print(f"🤖 [W.A.D.E.] Processing background job '{job_id}'...")
        
        async for chunk in process_agent_request(
            prompt=synthetic_prompt, 
            session_id=background_session_id, 
            is_system=True 
        ):
            print(chunk, end="", flush=True)
            
        print(f"\n✅ [W.A.D.E.] Background task '{job_id}' completed.")
        
    except Exception as e:
        print(f"\n❌ [W.A.D.E.] Fatal error in background task '{job_id}': {e}")

@register_tool("schedule_task")
async def schedule_task(job_name: str, task_prompt: str, run_date: str = "", cron_expression: str = "") -> str:
    """Allows W.A.D.E. to add jobs to his own internal clock."""
    try:
        if not wade_clock.running:
            wade_clock.start()

        run_date = str(run_date).strip() if run_date else ""
        cron_expression = str(cron_expression).strip() if cron_expression else ""

        if not run_date and not cron_expression:
            return (
                "Error: Missing time parameters. You MUST provide either 'run_date' (for one-off tasks, e.g., 'in 10 minutes') "
                "OR 'cron_expression' (for recurring tasks, e.g., '0 9 * * *'). Please examine the user's request and try again."
            )

        if cron_expression:
            if any(char.isalpha() for char in cron_expression) and not any(kw in cron_expression.lower() for kw in ["mon", "tue", "wed", "thu", "fri", "sat", "sun", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]):
                return f"Error: '{cron_expression}' looks like natural language, not a valid cron string. Use standard cron format (e.g., '0 9 * * *') or use the 'run_date' parameter instead."
            
            parts = cron_expression.split()
            if len(parts) != 5:
                return f"Error: Invalid cron expression '{cron_expression}'. It MUST be exactly 5 parts separated by spaces (minute hour day month day_of_week)."
            
            try:
                trigger = CronTrigger.from_crontab(cron_expression)
            except ValueError as ve:
                return f"Error: Invalid cron format. {str(ve)}. Please correct it and try again."
            
            schedule_type = f"recurring schedule: {cron_expression}"
            
        else:
            if "datetime" in run_date or "timedelta" in run_date or "(" in run_date:
                return "Error: 'run_date' must be plain natural language (e.g., 'tomorrow at noon', 'in 10 minutes'). Do not use Python code."

            dt = dateparser.parse(run_date, settings={'PREFER_DATES_FROM': 'future'})
            
            if not dt:
                return f"Error: Could not understand the date/time format '{run_date}'. Please rephrase it simply (e.g., 'in 5 minutes', 'tomorrow at 5pm')."
            
            if dt <= datetime.now():
                return f"Error: The parsed time ({dt.strftime('%Y-%m-%d %H:%M:%S')}) is in the past. If you meant a future time, specify it clearly (e.g., 'tomorrow')."

            trigger = DateTrigger(run_date=dt)
            schedule_type = f"one-time execution at {dt.strftime('%Y-%m-%d %H:%M:%S')}"

        wade_clock.add_job(
            _agent_wakeup_callback,
            trigger=trigger,
            args=[task_prompt, job_name],
            id=job_name,
            replace_existing=True
        )
        
        return f"Success: Task '{job_name}' scheduled for {schedule_type}."

    except Exception as e:
        return f"Scheduler Tool Error: {str(e)}"
    
if __name__ == "__main__":
    from datetime import timedelta
    
    async def run_test():
        print("--- TEST 1: Natural Language Task ---")
        
        res = await schedule_task(
            job_name="test_alarm", 
            task_prompt="Say hello to the terminal!", 
            run_date="in 3 seconds"
        )
        print(res)
        
        print("\n⏳ Waiting 5 seconds to let the alarm ring...")
        await asyncio.sleep(5)
        print("\n🏁 Test complete.")

    asyncio.run(run_test())