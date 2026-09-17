"""Stage acceptance through public configuration and the shared world loop."""
import json
from pathlib import Path

import pytest
import yaml

from relic_agent.config import load_config
from relic_agent.runtime import OrganizationRuntime
from relic_agent.runtime.builder import build_generic_world

ROOT = Path(__file__).resolve().parents[1]


def _configured(tmp_path, data):
    path = tmp_path / 'organization.yaml'
    path.write_text(yaml.safe_dump(data))
    return load_config(path)


def test_research_example_exposes_a_connected_emergent_lifecycle(tmp_path):
    runtime = OrganizationRuntime(load_config(ROOT / 'configs/default.yaml'))
    result = runtime.run(output_root=tmp_path, ticks=72)
    world = runtime.world
    assert world.episode_manager.episodes
    assert world.reflection_manager.reflections
    assert world.reflection_manager.wishes
    from_wishes = [p for p in world.proposal_manager.proposals.values()
                   if p.source_wish_id or p.source_wish_ids]
    assert from_wishes
    adopted = [p for p in from_wishes if p.status == 'adopted']
    assert adopted
    trace = json.loads(result.trace_path.read_text())
    frame = trace['frames'][-1]
    protocols = [p for p in frame['organization']['protocols'] if p['origin'] == 'emergent']
    tools = [item for item in frame['lineage'] if item['kind'] == 'tool']
    assert any(item.get('created_from_proposal_id') in {p.proposal_id for p in adopted}
               for item in protocols + tools)
    assert protocols  # Protocol formation also remains available from repeated episodes.
    memory = json.loads((result.run_directory / 'organization-memory.json').read_text())
    assert {item['lineage_id'] for item in tools} <= {item['tool_id'] for item in memory['tools']}
    assert 'LanternScout' not in result.trace_path.read_text()
    assert result.completed_task_count == 3


@pytest.mark.parametrize(('section', 'target'), [
    ('reflection', 'reflections'), ('wish_extraction', 'wishes'),
    ('proposal_generation', 'proposals'), ('protocol_formation', 'protocol_specs'),
])
def test_learning_switches_disable_real_lifecycle_outputs(tmp_path, section, target):
    data = yaml.safe_load((ROOT / 'configs/default.yaml').read_text())
    data['learning'][section] = False
    world = build_generic_world(_configured(tmp_path, data))
    for _ in range(48):
        world.step()
    manager = world.reflection_manager if target in {'reflections', 'wishes'} else world.proposal_manager
    assert not getattr(manager, target)


def test_exported_protocol_package_roundtrips_rule_semantics_and_origin(tmp_path):
    data = yaml.safe_load((ROOT / 'configs/custom-governance.yaml').read_text())
    data['protocols']['initial'][0] = {
        'id':'evidence-first', 'name':'Evidence before completion',
        'definition': {'trigger_condition':'complete_task', 'affected_actions':['complete_task'],
                       'required_fields':['supporting-evidence'], 'required_steps':['check evidence'],
                       'enforcement_action':'block', 'enforcement_rule':'Require supporting-evidence',
                       'scope':'shared tasks', 'sunset_rule':'Retire through a reviewed proposal',
                       'exception_rule':'No automatic exceptions',
                       'responsible_roles':{'reviewer':['quality_reviewer']}}}
    first = OrganizationRuntime(_configured(tmp_path, data))
    result = first.run(output_root=tmp_path / 'runs', ticks=1)
    package = result.run_directory / 'protocols.json'
    exported = json.loads(package.read_text())
    assert exported['protocols'][0]['definition']['enforcement_action'] == 'block'
    data['protocols']['initial'] = []
    data['protocols']['packages'] = [str(package)]
    loaded = build_generic_world(_configured(tmp_path, data))
    initial_spec = first.world.proposal_manager.protocol_specs['evidence-first']
    loaded_spec = loaded.proposal_manager.protocol_specs['evidence-first']
    for key, value in exported['protocols'][0]['definition'].items():
        assert getattr(initial_spec, key) == getattr(loaded_spec, key) == value
    assert loaded.protocol_origins['evidence-first'] == 'loaded'
