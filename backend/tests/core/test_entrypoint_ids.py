import importlib.util
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / 'docker.dist' / 'build' / 'entrypoint.py'


@pytest.fixture(scope='module')
def entrypoint():
    spec = importlib.util.spec_from_file_location('coneshare_entrypoint', ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unset_id_is_none(entrypoint, monkeypatch):
    monkeypatch.delenv('PUID', raising=False)
    assert entrypoint._parse_id('PUID') is None


def test_numeric_id_is_accepted(entrypoint, monkeypatch):
    monkeypatch.setenv('PUID', ' 1000 ')
    assert entrypoint._parse_id('PUID') == 1000


@pytest.mark.parametrize('value', [
    '1000; touch /tmp/pwned',
    '$(id)',
    '1000 coneshare && sh',
    '-1',
    '0',
    '99999999999',
    '1e3',
])
def test_injection_and_out_of_range_values_abort(entrypoint, monkeypatch, value):
    monkeypatch.setenv('PGID', value)
    with pytest.raises(SystemExit):
        entrypoint._parse_id('PGID')


def test_change_ugid_never_uses_a_shell(entrypoint, monkeypatch):
    calls = []
    monkeypatch.setattr(entrypoint, 'check_call', lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setenv('PUID', '1001')
    monkeypatch.setenv('PGID', '1002')
    entrypoint.change_ugid()
    assert calls == [
        ((['usermod', '-o', '-u', '1001', 'coneshare'],), {}),
        ((['groupmod', '-o', '-g', '1002', 'coneshare'],), {}),
    ]
