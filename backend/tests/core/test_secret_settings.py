import pytest

from backend import settings as project_settings


@pytest.fixture
def debug_off(monkeypatch):
    monkeypatch.setattr(project_settings, 'DEBUG', False)


@pytest.fixture
def debug_on(monkeypatch):
    monkeypatch.setattr(project_settings, 'DEBUG', True)


def test_missing_secret_is_rejected(monkeypatch, debug_on):
    monkeypatch.delenv('SOME_SECRET', raising=False)
    with pytest.raises(RuntimeError, match='must be set'):
        project_settings._require_secret('SOME_SECRET')


@pytest.mark.parametrize('value', [
    'django-insecure-development-key-for-coneshare',
    'django-insecure-anything-else',
    'supersecrettoken',
    'changeme',
])
def test_published_defaults_are_rejected_even_in_debug(monkeypatch, debug_on, value):
    monkeypatch.setenv('SOME_SECRET', value)
    with pytest.raises(RuntimeError, match='publicly known default'):
        project_settings._require_secret('SOME_SECRET')


def test_short_secret_rejected_outside_debug(monkeypatch, debug_off):
    monkeypatch.setenv('SOME_SECRET', 'a' * (project_settings.MIN_SECRET_LENGTH - 1))
    with pytest.raises(RuntimeError, match='at least'):
        project_settings._require_secret('SOME_SECRET')


def test_short_secret_allowed_in_debug(monkeypatch, debug_on):
    monkeypatch.setenv('SOME_SECRET', 'short-but-unique')
    assert project_settings._require_secret('SOME_SECRET') == 'short-but-unique'


def test_strong_secret_accepted(monkeypatch, debug_off):
    value = 'f' * 64
    monkeypatch.setenv('SOME_SECRET', f'  {value}  ')
    assert project_settings._require_secret('SOME_SECRET') == value
