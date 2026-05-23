from __future__ import annotations

import re
import json
import logging

from typing import Any

from app.core.task_store import Task
from app.services.inference_client import InferenceClient
from app.core.classifier import _DESTRUCTIVE as _DANGER_TOKENS

logger = logging.getLogger("wade.planner")

_SHORT_MSG_LIMIT = 30

_SIMPLE_RE = re.compile(
    r"""
    ^(
        # Greetings / closings
        hey\b|hi\b|hello\b|howdy\b|yo\b|sup\b|hiya\b|
        good\s*(morning|afternoon|evening|night)|
        bye\b|goodbye\b|see\s+you|take\s+care|later\b|
        # Affirmations / social
        thanks?\b|thank\s+you|cheers\b|cool\b|ok(ay)?\b|
        got\s+it|sounds?\s+good|great\b|awesome\b|perfect\b|
        no\s+worries|np\b|sure\b|yep\b|nope\b|lol\b|
        # Existing simple prefixes (migrated from tuple to regex)
        what\s|who\s|when\s|where\s|why\s|how\s|
        tell\s+me|show\s+me|is\s|are\s|does\s|do\s|
        can\s|will\s|would\s|could\s|should\s|
        get\s|check\s|search\s|run\s|read\s|show\s|
        open\s|list\s|find\s|fetch\s|look\s|ping\s|
        play\s|pause\s|stop\s|calculate\s|convert\s|
        translate\s|summarize\s|summarise\s
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

def _is_simple(goal: str) -> bool:
    stripped = goal.strip()
    words = frozenset(re.findall(r"\b\w+\b", stripped.lower()))
    if words & _DANGER_TOKENS:
        return False
    if len(stripped) <= _SHORT_MSG_LIMIT and "," not in stripped:
        return True
    return bool(_SIMPLE_RE.match(stripped))

_PLANNER_SYSTEM = """\
You are a task planner. Decompose the user's goal into a structured plan.

Return ONLY a JSON object with these top-level fields:
  "success_criteria" : list of strings — explicit, testable outcomes that define goal completion
  "constraints"      : list of strings — things that must NOT happen during execution
  "steps"            : list of step objects

Each step object has:
  "goal"             : string — what this step does
  "expected_outcome" : string — what a correct result for this specific step looks like
  "depends_on"       : list of int — 0-based indices of steps that must complete first
  "requires_network" : bool
  "is_reversible"    : bool — false ONLY for destructive/external actions

Rules:
- success_criteria must be explicit and testable (not vague like "it worked").
- expected_outcome describes the step's specific output, not the overall goal.
- Steps with no dependencies can run in parallel.
- Return the JSON object only. No explanation, no markdown fences.

Example for "search for X and Y, then synthesize":
{
  "success_criteria": ["Search results for X retrieved", "Search results for Y retrieved", "Synthesis written"],
  "constraints": ["Do not modify any files"],
  "steps": [
    {"goal": "search web for X", "expected_outcome": "List of relevant URLs about X", "depends_on": [], "requires_network": true, "is_reversible": true},
    {"goal": "search web for Y", "expected_outcome": "List of relevant URLs about Y", "depends_on": [], "requires_network": true, "is_reversible": true},
    {"goal": "synthesize results from step 0 and step 1", "expected_outcome": "Coherent summary combining X and Y", "depends_on": [0, 1], "requires_network": false, "is_reversible": true}
  ]
}"""

class PlannerAgent:
    """Agent responsible for decomposing a goal into (GoalAnchor, list[Task])."""

    def __init__(self, client: InferenceClient) -> None:
        self._client = client
        self._bus = None

    def set_event_bus(self, bus: Any) -> None:
        self._bus = bus

    def needs_planning(self, goal: str) -> bool:
        return not _is_simple(goal)

    async def decompose(
        self, goal: str, network_available: bool = True
    ) -> tuple:
        """Decompose a complex goal into a GoalAnchor and list of Tasks. If the goal is simple, return it as a single Task without calling the LLM."""
        from app.agents.critic import GoalAnchor

        if not self.needs_planning(goal):
            anchor = GoalAnchor(
                original_goal=goal,
                success_criteria=["Task executed without error"],
                constraints=[],
            )
            return anchor, [Task(goal=goal)]

        system_prompt = _PLANNER_SYSTEM
        if self._bus is not None:
            recent = self._bus.get_recent_state(5)
            if recent:
                events_txt = "\n".join(
                    f"- [{e['type']}] {e['source']}: {json.dumps(e['payload'])}"
                    for e in recent
                )
                system_prompt = (
                    f"{_PLANNER_SYSTEM}\n\n"
                    f"Current system state (recent events):\n{events_txt}"
                )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": goal},
        ]
        raw, _ = await self._client.chat("planner", messages, json_format=True)

        text = raw.strip()
        import re
        m = re.search(r"```(?:json)?\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
        else:
            first_brace = text.find("{")
            last_brace = text.rfind("}")
            first_bracket = text.find("[")
            last_bracket = text.rfind("]")

            starts = [i for i in [first_brace, first_bracket] if i != -1]
            ends = [i for i in [last_brace, last_bracket] if i != -1]

            if starts and ends:
                start = min(starts)
                end = max(ends) + 1

                if start < end:
                    text = text[start:end]

        success_criteria: list[str] = []
        constraints: list[str] = []
        steps: list[dict] = []

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                steps = [s for s in parsed if isinstance(s, dict)]
            elif isinstance(parsed, dict):
                success_criteria = parsed.get("success_criteria") or []
                constraints = parsed.get("constraints") or []
                steps = [s for s in (parsed.get("steps") or []) if isinstance(s, dict)]
            else:
                raise ValueError("Expected JSON object or array")
            if not steps:
                raise ValueError("No valid step objects found")
        except (json.JSONDecodeError, ValueError):
            logger.warning("[PLANNER] Invalid JSON, falling back. raw=%s", raw[:200])
            anchor = GoalAnchor(original_goal=goal, success_criteria=[], constraints=[])
            return anchor, [Task(goal=goal)]

        tasks: list[Task] = []
        orig_to_new_id: dict[int, str] = {}

        for orig_idx, step in enumerate(steps):
            needs_net = bool(step.get("requires_network", False))
            if needs_net and not network_available:
                logger.info("[PLANNER] Skipping offline step: %s", step.get("goal"))
                continue

            raw_deps = step.get("depends_on") or []
            mapped_deps = [orig_to_new_id[d] for d in raw_deps if d in orig_to_new_id]
            dropped = [d for d in raw_deps if d not in orig_to_new_id]
            if dropped:
                logger.warning("[PLANNER] Step %d deps %s were skipped (offline); dependency dropped", orig_idx, dropped)

            _eo = step.get("expected_outcome", "")
            t = Task(
                goal=str(step.get("goal", goal)),
                created_by="planner",
                requires_network=needs_net,
                is_reversible=bool(step.get("is_reversible", True)),
                depends_on=mapped_deps,
                expected_outcome=str(_eo).strip() if _eo else None,
            )
            orig_to_new_id[orig_idx] = t.id
            tasks.append(t)

        if not tasks:
            anchor = GoalAnchor(original_goal=goal, success_criteria=success_criteria, constraints=constraints)
            return anchor, [Task(goal=goal)]

        anchor = GoalAnchor(
            original_goal=goal,
            success_criteria=success_criteria,
            constraints=constraints,
        )
        return anchor, tasks