"""Legacy deterministic reflection stub for compatibility trace generation."""

from __future__ import annotations

from relic_agent.episodes.models import OrgEpisode
from relic_agent.events.models import Event
from relic_agent.organization.models import AgentState
from relic_agent.reflection.models import (
    AgentMemory,
    AgentReflection,
    Wish,
    make_wish_fingerprint,
)


class ReflectionManager:
    def __init__(self) -> None:
        self.reflections: dict[str, AgentReflection] = {}
        self.wishes: dict[str, Wish] = {}
        self.memories: dict[str, AgentMemory] = {}
        self._reflection_sequence = 0
        self._wish_sequence = 0

    def reflect(
        self,
        *,
        tick: int,
        agent: AgentState,
        episodes: list[OrgEpisode],
        recent_events: list[Event],
    ) -> tuple[AgentReflection, Wish]:
        self._reflection_sequence += 1
        reflection_id = f"reflection_{self._reflection_sequence:05d}"
        episode_ids = [episode.episode_id for episode in episodes]
        event_ids = [event.event_id for event in recent_events]
        blockers = [
            event.payload.get("summary", event.event_type)
            for event in recent_events
            if event.event_type in {"task_blocked", "task_started"}
        ]
        target_problem = blockers[-1] if blockers else "coordination evidence is not yet explicit"
        reflection = AgentReflection(
            reflection_id=reflection_id,
            agent_id=agent.agent_id,
            tick=tick,
            source_episode_ids=episode_ids,
            source_event_ids=event_ids,
            source_object_ids=sorted(
                {object_id for event in recent_events for object_id in event.object_ids}
            ),
            self_assessment=f"{agent.display_name} reviewed active work and organization evidence.",
            team_assessment="The organization benefits from a repeatable ownership and review rule.",
            perceived_blockers=blockers,
            perceived_team_needs=["a visible task ownership and review protocol"],
            improvement_ideas=[
                {
                    "need_type": "protocol_need",
                    "description": "record ownership before work and require peer review before completion",
                    "urgency": 0.8,
                    "risk_if_unaddressed": "work can finish without accountable review",
                    "missing_support_type": "protocol",
                    "team_related": True,
                }
            ],
            raw_text="",
            trigger_reason="periodic_mock_reflection",
        )
        self._wish_sequence += 1
        wish_id = f"wish_{self._wish_sequence:05d}"
        related_objects = list(reflection.source_object_ids)
        wish = Wish(
            wish_id=wish_id,
            agent_id=agent.agent_id,
            source_reflection_id=reflection_id,
            source_reflection_ids=[reflection_id],
            supporting_agent_ids=[agent.agent_id],
            source_episode_id=episode_ids[-1] if episode_ids else None,
            source_event_ids=event_ids,
            raw_reflection_excerpt=reflection.self_assessment,
            wish_type="protocol_need",
            fingerprint=make_wish_fingerprint("protocol_need", target_problem, related_objects),
            interpreted_need="a visible task ownership and peer-review protocol",
            target_problem=target_problem,
            team_related=True,
            suggested_improvement="record ownership before work and peer review before completion",
            missing_support_type="protocol",
            urgency=0.8,
            expected_benefit="clear ownership and auditable review",
            risk_if_unaddressed="unreviewed work may be treated as complete",
            related_object_ids=related_objects,
            related_episode_ids=episode_ids,
            created_at_tick=tick,
            updated_at_tick=tick,
        )
        reflection.created_wish_ids.append(wish_id)
        self.reflections[reflection_id] = reflection
        self.wishes[wish_id] = wish
        memory = self.memories.setdefault(agent.agent_id, AgentMemory(agent_id=agent.agent_id))
        memory.reflections.append(reflection_id)
        memory.unresolved_needs.append(wish_id)
        memory.last_reflection_tick = tick
        for episode in episodes:
            if reflection_id not in episode.linked_reflection_ids:
                episode.linked_reflection_ids.append(reflection_id)
            if wish_id not in episode.linked_wish_ids:
                episode.linked_wish_ids.append(wish_id)
        return reflection, wish
