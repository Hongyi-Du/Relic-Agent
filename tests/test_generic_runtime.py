"""Behavior checks for the generic builder on the shared simulation loop."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from environments.org_env.backend.simulation import OrgWorld
from relic_agent.config import load_config
from relic_agent.runtime import OrganizationRuntime
from relic_agent.runtime.builder import GenericOrgWorld, build_generic_world
from relic_agent.runtime.providers import ProviderConfigurationError

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path, data):
    path = tmp_path / 'organization.yaml'
    path.write_text(yaml.safe_dump(data))
    return load_config(path)


def _base():
    return yaml.safe_load((ROOT / 'configs/minimal.yaml').read_text())


@pytest.mark.parametrize('count', [1, 2, 3, 8, 12])
def test_arbitrary_rosters_step_without_product_or_canonical_roles(tmp_path, count):
    data = _base()
    template = data['agents'][0]
    data['agents'] = [dict(deepcopy(template), id=f'person-{i}', role=f'custom_role_{i}') for i in range(count)]
    data['tasks'][0]['owner'] = None
    data['tasks'][0]['collaborators'] = []
    data['governance'] = {'approval_mode':'auto','quorum':1}
    runtime = OrganizationRuntime(_config(tmp_path, data))
    result = runtime.run(output_root=tmp_path / 'runs', ticks=6)
    assert GenericOrgWorld.step is OrgWorld.step
    assert len(runtime.world.agents) == count
    assert runtime.world.product is None
    assert 'LanternScout' not in result.trace_path.read_text()
    assert result.completed_task_count == 1


def test_mixed_routes_tasks_and_plugin_are_executed(tmp_path):
    runtime = OrganizationRuntime(load_config(ROOT / 'configs/mixed-model.yaml'))
    result = runtime.run(output_root=tmp_path, ticks=12)
    stats = json.loads(result.manifest_path.read_text())['providers']
    assert stats['agents']['researcher']['response_model_counts']['mock-writer'] > 0
    assert stats['agents']['reviewer']['response_model_counts']['mock-critic'] > 0
    assert result.completed_task_count == 1
    custom = OrganizationRuntime(load_config(ROOT / 'examples/generic/custom-tool.yaml'))
    result = custom.run(output_root=tmp_path, ticks=12)
    assert any(e.get('tool_id') == 'word_count' and e['status'] == 'completed' for e in custom.world.events)
    assert 'word-count-report' in custom.world.company.files
    assert result.completed_task_count == 1


def test_provider_credentials_fail_only_on_actual_use(tmp_path, monkeypatch):
    data = _base()
    data['providers']['local'] = {'type':'generic_http','base_url_env':'UNSET_RELIC_ENDPOINT',
                                'api_key_env':'UNSET_RELIC_KEY','default_model':'user-model'}
    monkeypatch.delenv('UNSET_RELIC_ENDPOINT', raising=False)
    monkeypatch.delenv('UNSET_RELIC_KEY', raising=False)
    world = build_generic_world(_config(tmp_path, data))
    with pytest.raises(ProviderConfigurationError, match='missing_base_url_environment'):
        world.step()


def test_live_owner_checks_acceptance_without_collaborators(tmp_path, monkeypatch):
    from types import SimpleNamespace
    data = _base()
    data['tasks'][0]['collaborators'] = []
    data['providers']['local'] = {'type': 'generic_http', 'default_model': 'reviewing-owner'}
    world = build_generic_world(_config(tmp_path, data))
    reviews = iter([{'approved': False, 'feedback': 'Add supporting evidence'},
                    {'approved': True, 'feedback': 'Evidence is present'}])
    prompts = []

    def assess(system, user, schema):
        prompts.append((system, user))
        return next(reviews)

    monkeypatch.setattr(world.provider_registry, 'client_for_agent', lambda aid: SimpleNamespace(
        generate_text=lambda *args: 'Draft evidence and conclusion', generate_json=assess))
    world.work_on_generic_task('researcher', 'research-note')
    first = world.complete_generic_task('researcher', 'research-note', {})
    assert first['status'] == 'pending'
    assert 'research-note' in world.task_revision_requests
    assert world.tasks['research-note'].status.value != 'done'
    world.work_on_generic_task('researcher', 'research-note')
    second = world.complete_generic_task('researcher', 'research-note', {})
    assert second['status'] == 'completed'
    assert len(prompts) == 2
    assert 'Conclusion has supporting evidence' in str(prompts)
    assert 'Draft evidence and conclusion' in str(prompts)


def test_observability_and_private_workspace_have_effect(tmp_path, monkeypatch):
    data = _base()
    data['providers']['local']['api_key_env'] = 'TEST_RELIC_SECRET'
    monkeypatch.setenv('TEST_RELIC_SECRET','a-private-runtime-value')
    data['agents'][0]['private_workspace'] = {'root':'my-private-space','files':[
        {'id':'private-note','content':'a-private-runtime-value'}]}
    data['observability'] = {'public_trace':False,'inspector':False,'local_debug':True,
                             'token_logging':False,'cost_logging':False}
    runtime = OrganizationRuntime(_config(tmp_path, data))
    result = runtime.run(output_root=tmp_path / 'runs', ticks=3)
    assert result.trace_path is None
    assert not (result.run_directory / 'trace.json').exists()
    assert (result.run_directory / 'debug.json').exists()
    assert runtime.world.personal['researcher'].personal_workspace_id == 'my-private-space'
    for path in result.run_directory.iterdir():
        assert 'a-private-runtime-value' not in path.read_text()
    assert 'private-note' not in (result.run_directory / 'workspace.json').read_text()
    metrics = json.loads(result.manifest_path.read_text())['providers']
    assert 'usage_totals' not in str(metrics)
    assert 'cost_usd' not in str(metrics)


def test_schedule_task_dependencies_and_initial_inputs_affect_work(tmp_path):
    data = _base()
    data['agents'][0]['work_schedule'] = {'active_ticks':[2,4,6]}
    data['organization']['initial_documents'] = [{'id':'input-note','content':'specific input evidence'}]
    data['tasks'][0]['input_artifacts'] = ['input-note']
    data['tasks'][0]['collaborators'] = []
    config = _config(tmp_path, data)
    world = build_generic_world(config)
    assert 'specific input evidence' in world.agent_prompt('researcher','research-note')
    world.step()
    assert not any(action['agent_id']=='researcher' for action in world.action_log)
    world.step()
    assert any(action['agent_id']=='researcher' for action in world.action_log)
    assert world.tasks['research-note'].status.value == 'in_progress'


def test_reviewer_model_cannot_read_linked_private_artifacts(monkeypatch):
    from environments.org_env.backend.workspace.objects import FileObject, Visibility
    from types import SimpleNamespace
    world = build_generic_world(load_config(ROOT / 'configs/minimal.yaml'))
    world.work_on_generic_task('researcher', 'research-note')
    world.company.register_file(FileObject(object_id='private-evidence', owner_id='researcher',
        visibility=Visibility.PRIVATE, raw_payload='ONLY_THE_OWNER_CAN_READ_THIS',
        linked_task_ids=['research-note']))
    calls = []
    def capture(system, user):
        calls.append((system, user))
        return 'reviewed'
    monkeypatch.setattr(world.provider_registry, 'client_for_agent',
                        lambda aid: SimpleNamespace(generate_text=capture))
    world.review_generic_task('reviewer', 'research-note')
    assert calls
    assert 'ONLY_THE_OWNER_CAN_READ_THIS' not in str(calls)
    world.company.files['research-note'].visibility = Visibility.PRIVATE
    assert not world.deliverables_ready('research-note')
