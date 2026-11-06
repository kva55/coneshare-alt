import pytest


@pytest.mark.django_db
def test_notice_is_public_and_on_by_default(public_client):
    r = public_client.get('/api/v1/security/login-notice/')
    assert r.status_code == 200
    assert r.data['enabled'] is True
    assert r.data['text'].startswith('Attention: You are authenticating')


@pytest.mark.django_db
def test_admin_can_switch_off_and_edit(admin_api_client, public_client):
    r = admin_api_client.put('/api/v1/security/settings/', {'login_notice_enabled': False, 'login_notice_text': 'Custom.'}, format='json')
    assert r.status_code == 200, r.data
    got = public_client.get('/api/v1/security/login-notice/').data
    assert (got['enabled'], got['text']) == (False, 'Custom.')


@pytest.mark.django_db
def test_monitoring_is_on_by_default_and_capture_actions_stop_when_off(admin_api_client):
    from security.capture import CAPTURE_EVENTS, effective_actions
    from security.services import snapshot
    assert snapshot()['monitoring_enabled'] is True
    assert any(v != 'ignore' for v in effective_actions({}).values())
    r = admin_api_client.put('/api/v1/security/settings/', {'monitoring_enabled': False}, format='json')
    assert r.status_code == 200, r.data
    assert set(effective_actions({}).values()) == {'ignore'} and set(effective_actions({})) == set(CAPTURE_EVENTS)
