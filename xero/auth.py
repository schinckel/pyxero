import datetime
from urlparse import parse_qs
from urllib import urlencode

import requests
from requests_oauthlib import OAuth1, OAuth1Session
from oauthlib.oauth1 import SIGNATURE_RSA, SIGNATURE_TYPE_AUTH_HEADER

from .constants import XERO_BASE_URL, XERO_PARTNER_BASE_URL
from .constants import REQUEST_TOKEN_URL, AUTHORIZE_URL, ACCESS_TOKEN_URL
from .exceptions import *


class PrivateCredentials(object):
    """An object wrapping the 2-step OAuth process for Private Xero API access.

    Usage:

     1) Construct a PrivateCredentials() instance:

        >>> from xero.auth import PrivateCredentials
        >>> credentials = PrivateCredentials(<consumer_key>, <rsa_key>)

        rsa_key should be a multi-line string, starting with:

            -----BEGIN RSA PRIVATE KEY-----\n

     2) Use the credentials:

        >>> from xero import Xero
        >>> xero = Xero(credentials)
        >>> xero.contacts.all()
        ...
    """
    BASE_URL = XERO_BASE_URL
    
    def __init__(self, consumer_key, rsa_key):
        self.consumer_key = consumer_key
        self.rsa_key = rsa_key

        # Private API uses consumer key as the OAuth token.
        self.oauth_token = consumer_key

        self.oauth = OAuth1(
            self.consumer_key,
            resource_owner_key=self.oauth_token,
            rsa_key=self.rsa_key,
            signature_method=SIGNATURE_RSA,
            signature_type=SIGNATURE_TYPE_AUTH_HEADER,
        )


class PublicCredentials(object):
    """An object wrapping the 3-step OAuth process for Public Xero API access.

    Usage:

     1) Construct a PublicCredentials() instance:

        >>> from xero import PublicCredentials
        >>> credentials = PublicCredentials(<consumer_key>, <consumer_secret>)

     2) Visit the authentication URL:

        >>> credentials.url

        If a callback URI was provided (e.g., https://example.com/oauth),
        the user will be redirected to a URL of the form:

        https://example.com/oauth?oauth_token=<token>&oauth_verifier=<verifier>&org=<organization ID>

        from which the verifier can be extracted. If no callback URI is
        provided, the verifier will be shown on the screen, and must be
        manually entered by the user.

     3) Verify the instance:

        >>> credentials.verify(<verifier string>)

     4) Use the credentials.

        >>> from xero import Xero
        >>> xero = Xero(credentials)
        >>> xero.contacts.all()
        ...
    """
    
    BASE_URL = XERO_BASE_URL
    
    def __init__(self, consumer_key, consumer_secret,
                 callback_uri=None, verified=False,
                 oauth_token=None, oauth_token_secret=None,
                 oauth_session_handle=None,
                 scope=None, expiry=None, cert=None, rsa_key=None):
        """Construct the auth instance.

        Must provide the consumer key and secret.
        A callback URL may be provided as an option. If provided, the
        Xero verification process will redirect to that URL when the
        authentication has completed.
        
        The scope_list should be provided when required by the API,
        for instance, this is required when accessing the PayrollAPI.
        """
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.callback_uri = callback_uri
        self.verified = verified
        # It seems there is something in this list that is breaking.
        # if scope == 'FULL_API':
        #     from .api import Xero
        #     self.scope_list = ['payroll.%s' % s.lower() for s in Xero.PAYROLL_OBJECT_LIST]
        # else:
        self.scope = list(scope or [])
        self._oauth = None
        self.expiry = expiry
        self.cert = cert
        self.rsa_key = rsa_key
        self.oauth_session_handle = oauth_session_handle

        if oauth_token and oauth_token_secret:
            if self.verified:
                # If provided, this is a fully verified set of
                # credentials. Store the oauth_token and secret
                # and initialize OAuth around those
                self._init_oauth(oauth_token, oauth_token_secret)
            else:
                # If provided, we are reconstructing an initalized
                # (but non-verified) set of public credentials.
                self.oauth_token = oauth_token
                self.oauth_token_secret = oauth_token_secret

        else:
            oauth = OAuth1(
                consumer_key,
                client_secret=self.consumer_secret,
                callback_uri=self.callback_uri
            )
            
            response = requests.post(url=REQUEST_TOKEN_URL % self.BASE_URL, auth=oauth, cert=self.cert)

            if response.status_code == 200:
                credentials = parse_qs(response.text)
                self.oauth_token = credentials.get('oauth_token')[0]
                self.oauth_token_secret = credentials.get('oauth_token_secret')[0]

            elif response.status_code == 400:
                raise XeroBadRequest(response)

            elif response.status_code == 401:
                raise XeroUnauthorized(response)

            elif response.status_code == 403:
                raise XeroForbidden(response)

            elif response.status_code == 404:
                raise XeroNotFound(response)

            elif response.status_code == 500:
                raise XeroInternalError(response)

            elif response.status_code == 501:
                raise XeroNotImplemented(response)

            elif response.status_code == 503:
                # Two 503 responses are possible. Rate limit errors
                # return encoded content; offline errors don't.
                # If you parse the response text and there's nothing
                # encoded, it must be a not-available error.
                payload = parse_qs(response.text)
                if payload:
                    raise XeroRateLimitExceeded(response, payload)
                else:
                    raise XeroNotAvailable(response)
            else:
                raise XeroExceptionUnknown(response)

    def _init_oauth(self, oauth_token, oauth_token_secret):
        "Store and initialize the OAuth credentials"
        self.oauth_token = oauth_token
        self.oauth_token_secret = oauth_token_secret
        self.verified = True

        self._oauth = OAuth1(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=self.oauth_token,
            resource_owner_secret=self.oauth_token_secret
        )

    @property
    def state(self):
        """Obtain the useful state of this credentials object so that
        we can reconstruct it independently.
        """
        return dict(
            (attr, getattr(self, attr))
            for attr in (
                'consumer_key', 'consumer_secret', 'callback_uri',
                'verified', 'oauth_token', 'oauth_token_secret',
                'oauth_session_handle',
                'expiry', 'scope', 'cert', 'rsa_key',
            )
            if getattr(self, attr) is not None
        )
    
    def verify(self, verifier):
        "Verify an OAuth token"

        # Construct the credentials for the verification request
        oauth = OAuth1(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=self.oauth_token,
            resource_owner_secret=self.oauth_token_secret,
            verifier=verifier
        )
        
        if self.oauth_session_handle:
            data = {'oauth_session_handle': self.oauth_session_handle}
        # Make the verification request, gettiung back an access token
        response = requests.post(url=ACCESS_TOKEN_URL % self.BASE_URL, auth=oauth, cert=self.cert)
        self._handle_verification_response(response)

    def _handle_verification_response(self, response):
        "Helper method to handle response from verify/refresh_token"
        if response.status_code == 200:
            credentials = parse_qs(response.text)
            # Initialize the oauth credentials
            
            self._init_oauth(
                credentials.get('oauth_token')[0],
                credentials.get('oauth_token_secret')[0],
            )
            
            expires_time = int(credentials.get('oauth_expires_in', [1800])[0])
            self.expiry = datetime.datetime.now() + datetime.timedelta(seconds=expires_time)
            self.oauth_session_handle = credentials.get('oauth_session_handle', [None])[0]
        elif response.status_code == 400:
            raise XeroBadRequest(response)

        elif response.status_code == 401:
            raise XeroUnauthorized(response)

        elif response.status_code == 403:
            raise XeroForbidden(response)

        elif response.status_code == 404:
            raise XeroNotFound(response)

        elif response.status_code == 500:
            raise XeroInternalError(response)

        elif response.status_code == 501:
            raise XeroNotImplemented(response)

        elif response.status_code == 503:
            # Two 503 responses are possible. Rate limit errors
            # return encoded content; offline errors don't.
            # If you parse the response text and there's nothing
            # encoded, it must be a not-available error.
            payload = parse_qs(response.text)
            if payload:
                raise XeroRateLimitExceeded(response, payload)
            else:
                raise XeroNotAvailable(response)
        else:
            raise XeroExceptionUnknown(response)

    @property
    def url(self):
        "Returns the URL that can be visited to obtain a verifier code"
        
        data = {
            'oauth_token': self.oauth_token,
        }
        if self.callback_uri:
            data['oauth_callback'] = self.callback_uri        
        if self.scope:
            data['scope'] = ','.join(self.scope)

        url = AUTHORIZE_URL + '?' + urlencode(data)
        
        return url
        

    @property
    def oauth(self):
        "Returns the requests-compatible OAuth object"
        if self._oauth is None:
            raise XeroNotVerified("Public credentials haven't been verified")
        return self._oauth


class PartnerCredentials(PublicCredentials):
    """An object wrapping the 3-step OAuth process for Parter Xero API access.
    
    A PartnerApplication is only available upon request from Xero.
    
    Usage:
    
    1) Construct a PartnerCredentials() instance:
    
       >>> from xero import PartnerCredentials
       >>> credentials = PartnerCredentials(
           <consumer_key>, <consumer_secret>,
           cert=(<path-to-entrust-cert>, <path-to-entrust-key>),
           rsa_key=<rsa_key>
       )
    
       cert should be a 2-tuple, with paths to the two files that
       would have been generated when you post-process the certificate
       that you generated as part of the Partner upgrade program.
        
       rsa_key should be a multi-line string, starting with:
    
           -----BEGIN RSA PRIVATE KEY-----\n
    
       This key must be the private key for which you have uploaded the
       matching public key.
        
    2) Visit the authentication URL:

       >>> credentials.url

       If a callback URI was provided (e.g., https://example.com/oauth),
       the user will be redirected to a URL of the form:

       https://example.com/oauth?oauth_token=<token>&oauth_verifier=<verifier>&org=<organization ID>

       from which the verifier can be extracted. If no callback URI is
       provided, the verifier will be shown on the screen, and must be
       manually entered by the user.

    3) Verify the instance:

       >>> credentials.verify(<verifier string>)

    4) Use the credentials.

       >>> from xero import Xero
       >>> xero = Xero(credentials)
       >>> xero.contacts.all()
    
    5) Refresh your token (as required).
    
       Part of the benefit of the Partner Application is that you may
       refresh expired tokens. The PartnerCredentials() instance you
       generated initially (or re-created from credentials.state) has
       a method that will refresh a token:
       
       >>> credentials = PartnerCredentials(**stored_credentials)
       >>> credentials.refresh_token()
       
       You may refresh a token at any time, as long as the time-frame
       for `oauth_authorization_expires_in` has not passed.
    
    """
    BASE_URL = XERO_PARTNER_BASE_URL
    
    def _init_oauth(self, oauth_token, oauth_token_secret):
        "Store and initialize the OAuth credentials"
        self.oauth_token = oauth_token
        self.oauth_token_secret = oauth_token_secret
        self.verified = True
        
        self._oauth = OAuth1(
            self.consumer_key,
            resource_owner_key=self.oauth_token,
            resource_owner_secret=self.oauth_token_secret,
            rsa_key=self.rsa_key,
            signature_method=SIGNATURE_RSA,
            signature_type=SIGNATURE_TYPE_AUTH_HEADER,
        )
        
    
    def refresh_token(self):
        "Refresh the token, if possible."
        oauth = OAuth1(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=self.oauth_token,
            resource_owner_secret=self.oauth_token_secret,
        )
        params={
            'oauth_session_handle': self.oauth_session_handle,
            'oauth_token': self.oauth_token,
        }
        response = requests.get(url=ACCESS_TOKEN_URL % self.BASE_URL, params=params, auth=self.oauth, cert=self.cert)
        
        self._handle_verification_response(response)