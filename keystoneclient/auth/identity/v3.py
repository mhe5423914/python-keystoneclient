# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import abc
import logging

import six

from keystoneclient import access
from keystoneclient.auth.identity import base
from keystoneclient import exceptions

_logger = logging.getLogger(__name__)


class Auth(base.BaseIdentityPlugin):

    def __init__(self, auth_url, auth_methods,
                 trust_id=None,
                 domain_id=None,
                 domain_name=None,
                 project_id=None,
                 project_name=None,
                 project_domain_id=None,
                 project_domain_name=None):
        """Construct an Identity V3 Authentication Plugin.

        :param string auth_url: Identity service endpoint for authentication.
        :param list auth_methods: A collection of methods to authenticate with.
        :param string trust_id: Trust ID for trust scoping.
        :param string domain_id: Domain ID for domain scoping.
        :param string domain_name: Domain name for domain scoping.
        :param string project_id: Project ID for project scoping.
        :param string project_name: Project name for project scoping.
        :param string project_domain_id: Project's domain ID for project.
        :param string project_domain_name: Project's domain name for project.
        """

        super(Auth, self).__init__(auth_url=auth_url)

        self.auth_methods = auth_methods
        self.trust_id = trust_id
        self.domain_id = domain_id
        self.domain_name = domain_name
        self.project_id = project_id
        self.project_name = project_name
        self.project_domain_id = project_domain_id
        self.project_domain_name = project_domain_name

    @property
    def token_url(self):
        """The full URL where we will send authentication data."""
        return '%s/auth/tokens' % self.auth_url.rstrip('/')

    def get_auth_ref(self, session, **kwargs):
        headers = {}
        body = {'auth': {'identity': {}}}
        ident = body['auth']['identity']

        for method in self.auth_methods:
            name, auth_data = method.get_auth_data(session, self, headers)
            ident.setdefault('methods', []).append(name)
            ident[name] = auth_data

        if not ident:
            raise exceptions.AuthorizationFailure('Authentication method '
                                                  'required (e.g. password)')

        if ((self.domain_id or self.domain_name) and
                (self.project_id or self.project_name)):
            raise exceptions.AuthorizationFailure('Authentication cannot be '
                                                  'scoped to both domain '
                                                  'and project.')

        if self.domain_id:
            body['auth']['scope'] = {'domain': {'id': self.domain_id}}
        elif self.domain_name:
            body['auth']['scope'] = {'domain': {'name': self.domain_name}}
        elif self.project_id:
            body['auth']['scope'] = {'project': {'id': self.project_id}}
        elif self.project_name:
            scope = body['auth']['scope'] = {'project': {}}
            scope['project']['name'] = self.project_name

            if self.project_domain_id:
                scope['project']['domain'] = {'id': self.project_domain_id}
            elif self.project_domain_name:
                scope['project']['domain'] = {'name': self.project_domain_name}

        if self.trust_id:
            scope = body['auth'].setdefault('scope', {})
            scope['OS-TRUST:trust'] = {'id': self.trust_id}

        resp = session.post(self.token_url, json=body, headers=headers,
                            authenticated=False)
        return access.AccessInfoV3(resp.headers['X-Subject-Token'],
                                   **resp.json()['token'])

    @staticmethod
    def factory(auth_url, **kwargs):
        """Construct a plugin appropriate to your available arguments.

        This function is intended as a convenience and backwards compatibility.
        If you know the style of authorization you require then you should
        construct that plugin directly.
        """

        methods = []

        # NOTE(jamielennox): kwargs extraction is outside the if statement to
        # clear up additional args that might be passed but not valid for type.
        method_kwargs = PasswordMethod.extract_kwargs(kwargs)
        if method_kwargs.get('password'):
            methods.append(PasswordMethod(**method_kwargs))

        method_kwargs = TokenMethod.extract_kwargs(kwargs)
        if method_kwargs.get('token'):
            methods.append(TokenMethod(**method_kwargs))

        if not methods:
            msg = 'A username and password or token is required.'
            raise exceptions.AuthorizationFailure(msg)

        return Auth(auth_url, methods, **kwargs)


@six.add_metaclass(abc.ABCMeta)
class AuthMethod(object):
    """One part of a V3 Authentication strategy.

    V3 Tokens allow multiple methods to be presented when authentication
    against the server. Each one of these methods is implemented by an
    AuthMethod.

    Note: When implementing an AuthMethod use the method_parameters
    and do not use positional arguments. Otherwise they can't be picked up by
    the factory method and don't work as well with AuthConstructors.
    """

    method_parameters = []

    def __init__(self, **kwargs):
        for param in self.method_parameters:
            setattr(self, param, kwargs.pop(param, None))

        if kwargs:
            msg = "Unexpected Attributes: %s" % ", ".join(kwargs.keys())
            raise AttributeError(msg)

    @classmethod
    def extract_kwargs(cls, kwargs):
        """Remove parameters related to this method from other kwargs."""
        return dict([(p, kwargs.pop(p, None))
                     for p in cls.method_parameters])

    @abc.abstractmethod
    def get_auth_data(self, session, auth, headers, **kwargs):
        """Return the authentication section of an auth plugin.

        :param Session session: The communication session.
        :param Auth auth: The auth plugin calling the method.
        :param dict headers: The headers that will be sent with the auth
                             request if a plugin needs to add to them.
        :return tuple(string, dict): The identifier of this plugin and a dict
                                     of authentication data for the auth type.
        """


@six.add_metaclass(abc.ABCMeta)
class AuthConstructor(Auth):
    """AuthConstructor is a means of creating an Auth Plugin that contains
    only one authentication method. This is generally the required usage.

    An AuthConstructor creates an AuthMethod based on the method's
    arguments and the auth_method_class defined by the plugin. It then
    creates the auth plugin with only that authentication method.
    """

    auth_method_class = None

    def __init__(self, auth_url, *args, **kwargs):
        method_kwargs = self.auth_method_class.extract_kwargs(kwargs)
        method = self.auth_method_class(*args, **method_kwargs)
        super(AuthConstructor, self).__init__(auth_url, [method], **kwargs)


class PasswordMethod(AuthMethod):

    method_parameters = ['user_id',
                         'username',
                         'user_domain_id',
                         'user_domain_name',
                         'password']

    def __init__(self, **kwargs):
        """Construct a User/Password based authentication method.

        :param string password: Password for authentication.
        :param string username: Username for authentication.
        :param string user_id: User ID for authentication.
        :param string user_domain_id: User's domain ID for authentication.
        :param string user_domain_name: User's domain name for authentication.
        """
        super(PasswordMethod, self).__init__(**kwargs)

    def get_auth_data(self, session, auth, headers, **kwargs):
        user = {'password': self.password}

        if self.user_id:
            user['id'] = self.user_id
        elif self.username:
            user['name'] = self.username

            if self.user_domain_id:
                user['domain'] = {'id': self.user_domain_id}
            elif self.user_domain_name:
                user['domain'] = {'name': self.user_domain_name}

        return 'password', {'user': user}


class Password(AuthConstructor):
    auth_method_class = PasswordMethod


class TokenMethod(AuthMethod):

    method_parameters = ['token']

    def __init__(self, **kwargs):
        """Construct a Auth plugin to fetch a token from a token.

        :param string token: Token for authentication.
        """
        super(TokenMethod, self).__init__(**kwargs)

    def get_auth_data(self, session, auth, headers, **kwargs):
        headers['X-Auth-Token'] = self.token
        return 'token', {'id': self.token}


class Token(AuthConstructor):
    auth_method_class = TokenMethod

    def __init__(self, auth_url, token, **kwargs):
        super(Token, self).__init__(auth_url, token=token, **kwargs)
