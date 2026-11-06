import pytest


def _save(client, **fields):
    return client.put('/api/v1/security/settings/', fields, format='json')


@pytest.mark.django_db
def test_no_link_by_default_and_it_is_public(public_client):
    got = public_client.get('/api/v1/security/login-notice/').data
    assert got['nda_link_url'] == '' and got['nda_link_text'] == 'Acceptable Use Policy (AUP)'


@pytest.mark.django_db
def test_admin_sets_a_link_and_guests_can_read_it(admin_api_client, public_client):
    r = _save(admin_api_client, nda_link_url='https://policy.corp.example/nda-aup.html', nda_link_text='Read the AUP')
    assert r.status_code == 200, r.data
    got = public_client.get('/api/v1/security/login-notice/').data
    assert got['nda_link_url'] == 'https://policy.corp.example/nda-aup.html' and got['nda_link_text'] == 'Read the AUP'


@pytest.mark.django_db
@pytest.mark.parametrize('bad', ['javascript:alert(1)', 'data:text/html,hi', 'ftp://x.example/a', '//x.example/a', 'not a url', 'https://'])
def test_only_full_http_and_https_addresses_are_accepted(admin_api_client, bad):
    assert _save(admin_api_client, nda_link_url=bad).status_code == 400


@pytest.mark.django_db
def test_the_link_can_be_cleared(admin_api_client, public_client):
    _save(admin_api_client, nda_link_url='https://a.example/x')
    assert _save(admin_api_client, nda_link_url='').status_code == 200
    assert public_client.get('/api/v1/security/login-notice/').data['nda_link_url'] == ''
