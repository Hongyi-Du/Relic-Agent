import json
from pathlib import Path

import pytest

from relic_agent.cli import main
from relic_agent.config import load_config
from relic_agent.replay import load_trace
from relic_agent.runtime import OrganizationRuntime

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
@pytest.mark.parametrize(('path', 'count'), [
    ('configs/minimal.yaml', 2),
    ('configs/default.yaml', 8),
    ('configs/mixed-model.yaml', 2),
    ('configs/custom-governance.yaml', 2),
    ('examples/generic/custom-tool.yaml', 2),
])
def test_examples_build_real_generic_organizations(path, count, tmp_path):
    config = load_config(ROOT / path)
    runtime = OrganizationRuntime(config)
    result = runtime.run(output_root=tmp_path, ticks=12, run_id='example')
    trace = load_trace(result.trace_path)
    assert len(runtime.world.agents) == count
    assert len(trace['frames'][-1]['organization']['agents']) == count
    assert trace['frames'][-1]['organization']['config_summary']
    assert 'research-note' in runtime.world.tasks
    assert result.status == 'completed'


@pytest.mark.integration
def test_init_edit_validate_run_replay(tmp_path, capsys):
    project = tmp_path / 'my-org'
    assert main(['init', str(project)]) == 0
    capsys.readouterr()
    for name in ('organization.yaml', '.env.example', 'README.md', 'tools', 'prompts'):
        assert (project / name).exists()
    config = project / 'organization.yaml'
    text = config.read_text().replace('Small Research Team', 'My Independent Lab')
    config.write_text(text)
    assert main(['validate', '--config', str(config)]) == 0
    capsys.readouterr()
    assert main(['run', '--config', str(config), '--output-root', str(tmp_path / 'out'),
                 '--ticks', '3', '--run-id', 'first']) == 0
    result = json.loads(capsys.readouterr().out)
    trace = load_trace(result['trace_path'])
    assert trace['frames'][-1]['organization']['name'] == 'My Independent Lab'
    assert len(trace['frames'][-1]['organization']['agents']) == 2
    assert main(['replay', '--trace', result['trace_path']]) == 0
    capsys.readouterr()
    assert main(['init', str(project)]) == 2
    assert config.read_text() == text
