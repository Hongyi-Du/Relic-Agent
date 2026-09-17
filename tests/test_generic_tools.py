from types import SimpleNamespace as NS
from pathlib import Path

from environments.org_env.backend.workspace.company import CompanyWorkspace
from environments.org_env.backend.entities.work import Task
from relic_agent.config import load_config
from relic_agent.runtime.tools import GenericToolRegistry

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_permission_validation_execution_artifact_and_event():
    config = load_config(ROOT / 'examples/generic/custom-tool.yaml')
    world = NS(world_tick=1, events=[], tasks={'research-note': Task(task_id='research-note', title='Note')},
               company=CompanyWorkspace(members={'researcher', 'reviewer'}))
    registry = GenericToolRegistry(config, world)
    denied = registry.execute('reviewer', 'word_count', {'text': 'two words'})
    assert denied == {'status': 'failed', 'error_type': 'PermissionError'}
    invalid = registry.execute('researcher', 'word_count', {'text': 5})
    assert invalid == {'status': 'failed', 'error_type': 'ValueError'}
    result = registry.execute('researcher', 'word_count', {'text': 'two words'})
    assert result['status'] == 'completed'
    assert result['result']['word_count'] == 2
    assert 'word-count-report' in world.company.files
    assert world.events[-1]['type'] == 'tool_execution'
    assert world.events[-1]['tool_id'] == 'word_count'
    assert 'text' not in world.events[-1]


def test_malformed_plugin_artifacts_do_not_partially_change_workspace():
    config = load_config(ROOT / 'examples/generic/custom-tool.yaml')
    world = NS(world_tick=1, events=[], tasks={}, company=CompanyWorkspace())
    registry = GenericToolRegistry(config, world)
    registry.functions['word_count'] = lambda arguments, context: {
        'artifacts':[{'id':'valid','content':'first'}, {'content':'missing id'}]}
    assert registry.execute('researcher', 'word_count', {'text':'example'})['status'] == 'failed'
    assert not world.company.files


def test_plugin_errors_and_timeout_do_not_publish_private_payload(tmp_path):
    import yaml
    data = yaml.safe_load((ROOT / 'configs/minimal.yaml').read_text())
    (tmp_path / 'unsafe.py').write_text(
        'import time\ndef execute(arguments, context):\n'
        '    if arguments.get("slow"):\n        time.sleep(0.1)\n'
        '    raise RuntimeError("PRIVATE_SECRET_TOKEN")\n')
    data['tools']['plugins'] = [{'id':'unsafe','path':'unsafe.py','timeout_seconds':0.01}]
    data['agents'][0]['tools'].append('unsafe')
    path=tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(data))
    config=load_config(path)
    world=NS(world_tick=1, events=[], tasks={}, company=CompanyWorkspace())
    registry=GenericToolRegistry(config, world)
    assert registry.execute('researcher','unsafe',{})['error_type'] == 'RuntimeError'
    assert registry.execute('researcher','unsafe',{'slow':True})['status'] == 'timeout'
    assert 'PRIVATE_SECRET_TOKEN' not in str(world.events)


def test_read_only_builtin_cannot_mutate_workspace():
    config = load_config(ROOT / 'configs/minimal.yaml')
    from copy import deepcopy
    data = deepcopy(config.data)
    for tool in data['tools']['builtins']:
        if tool['id'] == 'files':
            tool['side_effect_policy'] = 'read_only'
    world = NS(world_tick=1, events=[], tasks={},
               company=CompanyWorkspace(members={'researcher', 'reviewer'}))
    registry = GenericToolRegistry(data, world)
    assert registry.execute('researcher', 'files', {'operation':'write','id':'unwanted'})['status'] == 'failed'
    assert not world.company.files


def test_pending_review_is_not_reported_as_success():
    from relic_agent.runtime.builder import build_generic_world, GenericExecution
    from agent_sdk.lived.core.contracts import ActionCandidate
    world = build_generic_world(load_config(ROOT / 'configs/minimal.yaml'))
    world.work_on_generic_task('researcher', 'research-note')
    result = GenericExecution().execute('researcher', ActionCandidate('complete_task', {'task_id':'research-note'}), world)
    assert not result.success
    assert result.failure_reason == 'PendingReview'
    assert world.tasks['research-note'].status.value != 'done'
    world.review_generic_task('reviewer', 'research-note')
    result = GenericExecution().execute('researcher', ActionCandidate('complete_task', {'task_id':'research-note'}), world)
    assert result.success
    assert world.tasks['research-note'].status.value == 'done'
