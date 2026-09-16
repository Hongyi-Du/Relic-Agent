"""Legacy mock episode manager retained for compatibility trace generation."""

from __future__ import annotations

from relic_agent.episodes.models import OrgEpisode
from relic_agent.events.models import Event


class EpisodeManager:
    def __init__(self) -> None:
        self.episodes: dict[str, OrgEpisode] = {}
        self._sequence = 0

    def observe(self, event: Event) -> OrgEpisode | None:
        if event.event_type == "task_started":
            return self._open("work_episode", event)
        if event.event_type == "proposal_created":
            return self._open("protocol_formation_episode", event)
        episode = self._matching_open_episode(event)
        if episode is not None:
            self._attach(episode, event)
            if event.event_type in {"task_completed", "protocol_adopted"}:
                episode.status = "resolved"
                episode.end_tick = event.tick
                episode.updated_at_tick = event.tick
                episode.outcome_summary = event.payload.get("summary", event.event_type)
            return episode
        return None

    def _open(self, episode_type: str, event: Event) -> OrgEpisode:
        self._sequence += 1
        episode = OrgEpisode(
            episode_id=f"episode_{self._sequence:05d}",
            episode_type=episode_type,
            start_tick=event.tick,
            trigger_event_id=event.event_id,
            trigger_object_id=event.object_ids[0] if event.object_ids else None,
            primary_agent_id=event.actor_id or None,
            problem_statement=event.payload.get("summary", event.event_type),
            title=event.payload.get("title", episode_type.replace("_", " ").title()),
            created_at_tick=event.tick,
            updated_at_tick=event.tick,
        )
        self._attach(episode, event)
        self.episodes[episode.episode_id] = episode
        return episode

    def _matching_open_episode(self, event: Event) -> OrgEpisode | None:
        object_ids = set(event.object_ids)
        for episode in reversed(list(self.episodes.values())):
            if episode.status != "open":
                continue
            if object_ids.intersection(episode.linked_object_ids):
                return episode
            if (
                event.event_type.startswith("proposal_")
                and episode.episode_type == "protocol_formation_episode"
            ):
                return episode
            if (
                event.event_type.startswith("protocol_")
                and episode.episode_type == "protocol_formation_episode"
            ):
                return episode
        return None

    @staticmethod
    def _attach(episode: OrgEpisode, event: Event) -> None:
        if event.event_id not in episode.linked_event_ids:
            episode.linked_event_ids.append(event.event_id)
        episode.add_participant(event.actor_id)
        for object_id in event.object_ids:
            episode.link_object(object_id)
        episode.timeline.append(
            {
                "tick": event.tick,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "actor_id": event.actor_id,
            }
        )
        episode.updated_at_tick = event.tick

    def open_for_agent(self, agent_id: str) -> list[OrgEpisode]:
        return [
            episode
            for episode in self.episodes.values()
            if episode.status == "open" and agent_id in episode.participants
        ]
