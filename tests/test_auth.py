"""
Copyright (c) 2018, 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import unicode_literals, absolute_import

from atomic_reactor.auth import HTTPBearerAuth, HTTPRegistryAuth, HTTPBasicAuthWithB64
from requests.auth import HTTPBasicAuth
import base64
import json
import pytest
import requests
import responses


BEARER_TOKEN = 'the-token'
BEARER_REALM_URL = 'https://registry.example.com/v2/auth'


def bearer_unauthorized_callback(request):
    headers = {'www-authenticate': 'Bearer realm={}'.format(BEARER_REALM_URL)}
    return (401, headers, json.dumps('unauthorized'))


def bearer_success_callback(request):
    assert request.headers['Authorization'] == 'Bearer {}'.format(BEARER_TOKEN)
    return (200, {}, json.dumps('success'))


def b64encode(username, password):
    return base64.b64encode('{}:{}'.format(username, password).encode('utf-8')).decode('utf-8')


class TestHTTPBearerAuth(object):

    @pytest.mark.parametrize('verify', (True, False))
    def test_initialization(self, verify):
        username = 'the-user'
        password = 'top-secret'
        auth_b64 = b64encode(username, password)
        access = ('pull', 'push')

        auth = HTTPBearerAuth(username=username, password=password, verify=verify, access=access,
                              auth_b64=auth_b64)

        assert auth.username == username
        assert auth.password == password
        assert auth.verify == verify
        assert auth.access == access
        assert auth.auth_b64 == auth_b64

    @responses.activate
    @pytest.mark.parametrize('repo_doesnt_exist_401', (True, False))
    @pytest.mark.parametrize(('auth_b64', 'username', 'password', 'basic_auth'), (
        (None, None, None, False),
        (None, 'spam', None, False),
        (None, None, 'bacon', False),
        (None, 'spam', 'bacon', True),
        (b64encode('spam', 'bacon'), None, None, True),
        (b64encode('spam', 'bacon'), 'spam', None, True),
        (b64encode('spam', 'bacon'), 'spam', 'bacon', True),
        (b64encode('spam', 'bacon'), None, 'bacon', True),
    ))
    def test_token_negotiation(self, repo_doesnt_exist_401, auth_b64, username, password,
                               basic_auth):

        def bearer_realm_callback(request):
            # Verify if username and password were provided, token is negotiated
            # with realm via basic auth.
            if basic_auth:
                creds = auth_b64 or b64encode(username, password)
                assert request.headers['authorization'] == 'Basic {}'.format(creds)
            else:
                assert 'authorization' not in request.headers

            return (200, {}, json.dumps({'token': BEARER_TOKEN}))

        responses.add_callback(responses.GET, BEARER_REALM_URL + '?scope=repository:fedora:pull',
                               callback=bearer_realm_callback, match_querystring=True)

        url = 'https://registry.example.com/v2/fedora/tags/list'

        responses.add_callback(responses.GET, url, callback=bearer_unauthorized_callback)
        if repo_doesnt_exist_401:
            responses.add_callback(responses.GET, url, callback=bearer_unauthorized_callback)
        else:
            responses.add_callback(responses.GET, url, callback=bearer_success_callback)

        auth = HTTPBearerAuth(username=username, password=password, auth_b64=auth_b64)
        response = requests.get(url, auth=auth)

        if repo_doesnt_exist_401:
            assert response.json() == 'unauthorized'
            assert response.status_code == requests.codes.not_found
        else:
            assert response.json() == 'success'
        assert len(responses.calls) == 3

    @responses.activate
    def test_token_cached_per_repo(self):
        responses.add(responses.GET, BEARER_REALM_URL + '?scope=repository:fedora:pull',
                      json={'token': BEARER_TOKEN}, match_querystring=True)
        responses.add(responses.GET, BEARER_REALM_URL + '?scope=repository:centos:pull',
                      json={'token': BEARER_TOKEN}, match_querystring=True)

        fedora_url = 'https://registry.example.com/v2/fedora/tags/list'
        responses.add_callback(responses.GET, fedora_url, callback=bearer_unauthorized_callback)
        responses.add(responses.GET, fedora_url, status=200, json='fedora-success')
        responses.add(responses.GET, fedora_url, status=200, json='fedora-success-also')

        centos_url = 'https://registry.example.com/v2/centos/tags/list'
        responses.add_callback(responses.GET, centos_url, callback=bearer_unauthorized_callback)
        responses.add(responses.GET, centos_url, status=200, json='centos-success')
        responses.add(responses.GET, centos_url, status=200, json='centos-success-also')

        auth = HTTPBearerAuth()

        assert requests.get(fedora_url, auth=auth).json() == 'fedora-success'
        assert requests.get(fedora_url, auth=auth).json() == 'fedora-success-also'

        assert requests.get(centos_url, auth=auth).json() == 'centos-success'
        assert requests.get(centos_url, auth=auth).json() == 'centos-success-also'

        assert len(responses.calls) == 8

    @responses.activate
    @pytest.mark.parametrize(('partial_url', 'repo'), (
        ('tags/list', 'fedora'),
        ('manifests/latest', 'fedora'),
        ('blobs/abcd12345', 'fedora'),
        ('blobs/uploads', 'fedora'),
        ('blobs/uploads/123456789', 'fedora'),
        ('tags/list', 'spam/fedora'),
        ('manifests/latest', 'spam/fedora'),
        ('blobs/abcd12345', 'spam/fedora'),
        ('blobs/uploads', 'spam/fedora'),
        ('blobs/uploads/123456789', 'spam/fedora'),
    ))
    def test_repo_extracted_from_url(self, partial_url, repo):
        responses.add(responses.GET, '{}?scope=repository:{}:pull'.format(BEARER_REALM_URL, repo),
                      json={'token': BEARER_TOKEN}, match_querystring=True)

        repo_url = 'https://registry.example.com/v2/{}/{}'.format(repo, partial_url)
        responses.add_callback(responses.GET, repo_url, callback=bearer_unauthorized_callback)
        responses.add(responses.GET, repo_url, status=200, json='success')

        auth = HTTPBearerAuth()

        assert requests.get(repo_url, auth=auth).json() == 'success'

    @responses.activate
    @pytest.mark.parametrize('partial_url', (
        'v2',
        '_catalog',
    ))
    def test_request_global_access(self, partial_url):
        responses.add(responses.GET, BEARER_REALM_URL, json={'token': BEARER_TOKEN},
                      match_querystring=True)

        repo_url = 'https://registry.example.com/{}'.format(partial_url)
        responses.add_callback(responses.GET, repo_url, callback=bearer_unauthorized_callback)
        responses.add(responses.GET, repo_url, status=200, json='success')

        auth = HTTPBearerAuth()

        assert requests.get(repo_url, auth=auth).json() == 'success'

    @responses.activate
    def test_non_401_error_propagated(self):

        def bearer_teapot_callback(request):
            headers = {'www-authenticate': 'Bearer realm={}'.format(BEARER_REALM_URL)}
            return (418, headers, json.dumps("I'm a teapot!"))

        url = 'https://registry.example.com/v2/fedora/tags/list'
        responses.add_callback(responses.GET, url, callback=bearer_teapot_callback)
        responses.add(responses.GET, url, status=200, json='success')  # Not actually called

        auth = HTTPBearerAuth()

        response = requests.get(url, auth=auth)
        assert response.json() == "I'm a teapot!"
        assert response.status_code == 418
        assert len(responses.calls) == 1

    @responses.activate
    def test_not_bearer_auth(self):
        url = 'https://registry.example.com/v2/fedora/tags/list'

        def unsupported_callback(request):
            headers = {'www-authenticate': 'Spam realm={}'.format(BEARER_REALM_URL)}
            return (401, headers, json.dumps('unauthorized'))

        responses.add_callback(responses.GET, url, callback=unsupported_callback)
        responses.add(responses.GET, url, status=200, json='success')  # Not actually called

        auth = HTTPBearerAuth()

        response = requests.get(url, auth=auth)
        assert response.json() == 'unauthorized'
        assert response.status_code == 401
        assert len(responses.calls) == 1


class TestHTTPRegistryAuth(object):

    def test_initialization(self):
        username = 'the-user'
        password = 'top-secret'
        auth_b64 = b64encode(username, password)
        auth = HTTPRegistryAuth(username=username, password=password, auth_b64=auth_b64)
        assert auth.username == 'the-user'
        assert auth.password == 'top-secret'
        assert auth.auth_b64 == auth_b64

    def test_v1(self):
        auth = HTTPRegistryAuth(username='username', password='password')
        with pytest.raises(NotImplementedError):
            requests.get('https://registry.example.com/v1/_ping', auth=auth)

    @responses.activate
    @pytest.mark.parametrize(('auth_b64', 'username', 'password', 'auth_type'), (
        (None, 'spam', 'bacon', 'basic'),
        (None, None, 'bacon', None),
        (None, 'spam', None, None),
        (None, None, None, None),
        (None, 'spam', 'bacon', 'bearer'),
        (None, None, 'bacon', 'bearer'),
        (None, 'spam', None, 'bearer'),
        (None, None, None, 'bearer'),
        (b64encode('spam', 'bacon'), None, None, 'basic'),
        (b64encode('spam', 'bacon'), 'spam', None, 'basic'),
        (b64encode('spam', 'bacon'), None, 'bacon', 'basic'),
        (b64encode('spam', 'bacon'), 'spam', 'bacon', 'basic'),
        (b64encode('spam', 'bacon'), None, None, 'bearer'),
        (b64encode('spam', 'bacon'), 'spam', None, 'bearer'),
        (b64encode('spam', 'bacon'), None, 'bacon', 'bearer'),
        (b64encode('spam', 'bacon'), 'spam', 'bacon', 'bearer'),
    ))
    def test_v2(self, auth_b64, username, password, auth_type):
        bearer_token = 'the-bearer-token'
        realm_url = 'https://registry.example.com/v2/auth'

        responses.add(responses.GET, '{}?scope=repository:fedora:pull'.format(realm_url),
                      json={'token': bearer_token}, match_querystring=True)

        def auth_callback(request):
            if auth_type == 'basic':
                creds = auth_b64 or b64encode(username, password)
                assert request.headers['authorization'] == 'Basic {}'.format(creds)
                return (200, {}, json.dumps('success'))

            if auth_type == 'bearer':
                header_value = 'Bearer {}'.format(bearer_token)
                if header_value != request.headers.get('authorization'):
                    auth_headers = {'www-authenticate': 'Bearer realm={}'.format(realm_url)}
                    return (401, auth_headers, json.dumps('unauthorized'))
                else:
                    return (200, {}, json.dumps('success'))

            assert 'authorization' not in request.headers
            return (200, {}, json.dumps('success'))

        repo_url = 'https://registry.example.com/v2/fedora/tags/list'
        responses.add_callback(responses.GET, repo_url, callback=auth_callback)

        auth = HTTPRegistryAuth(username=username, password=password, auth_b64=auth_b64)
        assert requests.get(repo_url, auth=auth).json() == 'success'
        if (username and password) or auth_b64:
            assert len(auth.v2_auths) == 2
            assert isinstance(auth.v2_auths[0], HTTPBearerAuth)
            assert isinstance(auth.v2_auths[1], (HTTPBasicAuth, HTTPBasicAuthWithB64))
        else:
            assert len(auth.v2_auths) == 1
            assert isinstance(auth.v2_auths[0], HTTPBearerAuth)
