"""Unit coverage for the public ``relic-agent-v2`` configuration surface."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from relic_agent.config import GENERIC_CONFIG_SCHEMA_VERSION, ConfigError, load_config


def _payload(agent_count: int = 2) -> dict:
    agents = [
        {
            "id": f"agent-{index}",
            "display_name": f"Agent {index}",
            "role": "role-that-is-not-canonical" if index == 0 else "reviewer",
            "provider": "primary" if index % 2 == 0 else "reviewer",
            "model": f"model-{index}",
            "skills": {"planning": 0.9, "review": 0.4},
            "profile": {"risk_tolerance": 0.3},
            "work_schedule": {"kind": "steady", "hours": ["09:00", "17:00"]},
            "tools": ["files", "task_board"],
            "permissions": ["assign_task"],
            "initial_context": {"brief": "work on the configured task"},
        }
        for index in range(agent_count)
    ]
    return {
        "schema_version": GENERIC_CONFIG_SCHEMA_VERSION,
        "organization": {
            "id": "research-lab",
            "name": "Research Lab",
            "description": "A configurable organization.",
            "brief": "Coordinate research, implementation, and review.",
            "shared_goals": ["produce a validated result"],
            "topology": {"teams": {"core": ["agent-0"]}},
            "channels": ["general", "review"],
            "shared_workspace": {"root": "workspace"},
            "initial_documents": [{"id": "brief", "title": "Brief"}],
            "initial_artifacts": [],
        },
        "providers": {
            "primary": {
                "type": "openai_compatible",
                "base_url_env": "MODEL_BASE_URL",
                "api_key_env": "MODEL_API_KEY",
                "default_model": "model-a",
                "timeout_seconds": 12,
                "retry_count": 2,
            },
            "reviewer": {
                "type": "mock",
                "default_model": "review-model",
            },
        },
        "agents": agents,
        "tasks": [
            {
                "id": "task-1",
                "title": "Build initial prototype",
                "description": "Produce a prototype and validation report.",
                "priority": 5,
                "owner": "agent-0",
                "collaborators": ["agent-1"] if agent_count > 1 else [],
                "dependencies": [],
                "input_artifacts": ["brief"],
                "expected_deliverables": ["prototype", "report"],
                "acceptance_criteria": ["prototype runs", "report is reviewable"],
                "deadline": "2026-10-01",
                "metadata": {"kind": "generic"},
            }
        ],
        "tools": {
            "builtins": [
                {
                    "id": "files",
                    "schema": {"type": "object"},
                    "timeout_seconds": 4,
                    "side_effect_policy": "read_only",
                },
                "task_board",
            ],
            "plugins": [],
        },
        "governance": {
            "approval_mode": "agent",
            "approver_members": ["agent-1"] if agent_count > 1 else ["agent-0"],
            "quorum": 1,
            "proposal_review_delay_ticks": 2,
            "deadlock_behavior": "escalate",
            "role_permissions": {"reviewer": ["approve_protocol"]} if agent_count > 1 else {},
            "decision_visibility": "public",
            "protocol_adoption_threshold": 0.6,
            "amendment_threshold": 0.5,
            "retirement_behavior": "review",
        },
        "protocols": {
            "initial": [
                {
                    "id": "evidence-review",
                    "name": "Evidence review",
                    "description": "Review evidence before adoption.",
                    "source": "initial",
                    "trigger": "before_release",
                    "action": "review",
                }
            ],
            "allow_proposals": True,
            "allow_adoption": True,
            "allow_revision": True,
            "allow_retirement": True,
        },
        "learning": {
            "profile_conditioning": True,
            "capability_learning": True,
            "institutionalization": True,
            "reflection": {
                "enabled": True,
                "cadence_ticks": 6,
                "per_agent_cooldown": 2,
                "salience_threshold": 0.2,
            },
            "wish_extraction": True,
            "wish": {"enabled": True, "cap": 4, "dedup": True},
            "proposal_generation": True,
            "proposal": {"enabled": True, "promotion_threshold": 0.7, "cap": 3},
            "protocol_formation": True,
            "protocol": {"enabled": True, "dedup": True, "review_delay_ticks": 2},
            "company_skill_memory": True,
            "governance_approval": True,
            "executable_workflow": True,
            "external_signal_loop": False,
        },
        "runtime": {"seed": 42, "ticks": 8, "decision_mode": "profile_policy"},
        "prompts": {
            "organization_brief": "Coordinate the configured organization.",
            "global_grounding_rules": ["Use shared evidence."],
            "agent_instruction_suffix": "Keep updates concise.",
            "task_context_template": "Task: {title}",
        },
        "observability": {"public_trace": True, "inspector": True},
    }


def _write_config(tmp_path: Path, payload: dict, name: str = "organization.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.unit
def test_generic_config_exposes_typed_and_normalized_sections_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    config = load_config(_write_config(tmp_path, _payload()))

    assert config.mode == "generic"
    assert config.is_generic
    assert config.organization_id == "research-lab"
    assert config.name == "Research Lab"
    assert config.runtime.seed == 42
    assert config.runtime.ticks == 8
    assert config.runtime.decision_mode == "profile_policy"
    assert config.generic is not None
    assert len(config.agents) == 2
    assert config.agents[0].role == "role-that-is-not-canonical"
    assert config.providers["primary"].api_key_env == "MODEL_API_KEY"
    assert config.data is not None
    assert config.data["agents"][0]["model"] == "model-0"
    assert config.data["governance"]["quorum"] == 1
    assert config.data["learning"]["reflection"]["cadence_ticks"] == 6
    assert config.data["protocols"]["initial"][0]["source"] == "initial"


@pytest.mark.unit
@pytest.mark.parametrize("agent_count", [1, 2, 3, 8, 12])
def test_generic_config_accepts_arbitrary_roster_sizes(tmp_path: Path, agent_count: int) -> None:
    payload = _payload(agent_count)
    # The common fixture's reviewer references are only meaningful for rosters
    # of two or more.  A one-agent organization uses the fallback approver.
    config = load_config(_write_config(tmp_path, payload, f"org-{agent_count}.yaml"))
    assert len(config.agents) == agent_count
    assert config.data["organization"]["name"] == "Research Lab"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p["agents"][0].update(provider="missing"), "unknown provider"),
        (lambda p: p["agents"][0].update(tools=["missing-tool"]), "unknown tools"),
        (lambda p: p["tasks"][0].update(owner="missing"), "unknown agent"),
        (lambda p: p["governance"].update(approver_members=["missing"]), "unknown agents"),
        (lambda p: p["organization"].update(invented=True), "unknown keys"),
        (lambda p: p["learning"].update(reflection={"invented": True}), "unknown keys"),
    ],
)
def test_generic_config_rejects_unknown_or_unresolvable_values(
    tmp_path: Path, mutate, message: str
) -> None:
    payload = _payload()
    mutate(payload)
    with pytest.raises(ConfigError, match=message):
        load_config(_write_config(tmp_path, payload))


@pytest.mark.unit
def test_generic_config_rejects_duplicate_ids_and_bad_types(tmp_path: Path) -> None:
    duplicate = _payload()
    duplicate["agents"][1]["id"] = duplicate["agents"][0]["id"]
    with pytest.raises(ConfigError, match="duplicate stable ids"):
        load_config(_write_config(tmp_path, duplicate, "duplicate.yaml"))

    malformed = _payload()
    malformed["runtime"]["seed"] = True
    with pytest.raises(ConfigError, match="runtime.seed"):
        load_config(_write_config(tmp_path, malformed, "malformed.yaml"))


@pytest.mark.unit
def test_custom_tool_plugin_is_imported_and_registered_locally(tmp_path: Path) -> None:
    plugin = tmp_path / "echo_plugin.py"
    plugin.write_text(
        "def create_tool():\n    return {'name': 'echo'}\n",
        encoding="utf-8",
    )
    payload = _payload()
    payload["tools"]["plugins"] = [
        {
            "id": "echo",
            "path": plugin.name,
            "entrypoint": "create_tool",
            "schema": {"type": "object"},
            "timeout_seconds": 3,
            "side_effect_policy": "none",
        }
    ]
    payload["agents"][0]["tools"].append("echo")
    config = load_config(_write_config(tmp_path, payload))

    assert "echo" in config.data["tools"]["plugin_ids"]
    assert config.generic is not None
    assert config.generic.tools.plugins[0].entrypoint == "create_tool"


@pytest.mark.unit
def test_plugin_import_failure_is_reported_without_provider_validation(tmp_path: Path) -> None:
    payload = _payload()
    payload["tools"]["plugins"] = [{"id": "missing", "module": "does.not.exist"}]
    with pytest.raises(ConfigError, match="plugin import failed"):
        load_config(_write_config(tmp_path, payload))
