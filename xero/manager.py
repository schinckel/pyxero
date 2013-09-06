from datetime import date, datetime, timedelta
from decimal import Decimal
from urlparse import parse_qs
from uuid import UUID
from xml.dom.minidom import parseString
from xml.etree.ElementTree import tostring, SubElement, Element
import json
import logging
import re
import urllib

import requests

from .exceptions import *

logger = logging.getLogger('pyxero')

def isplural(word):
    return word[-1].lower() == 's'

def singular(word):
    if isplural(word):
        return word[:-1]
    return word

DATE = re.compile(
    r'^(\/Date\((?P<timestamp>-?\d+)((?P<offset_h>[-+]\d\d)(?P<offset_m>\d\d))?\)\/)'
    r'|'
    r'((?P<year>\d{4})-(?P<month>[0-2]\d)-0?(?P<day>[0-3]\d)'
    r'T'
    r'(?P<hour>[0-5]\d):(?P<minute>[0-5]\d):(?P<second>[0-6]\d))$'
)

def parse_date(string, force_datetime=False):
    matches = DATE.match(string)
    if not matches:
        return None
    
    values = dict([
        (
            k, 
            v if v[0] in '+-' else int(v)
        ) for k,v in matches.groupdict().items() if v and int(v)
    ])
    
    if 'timestamp' in values:
        return datetime.utcfromtimestamp(
            int(values['timestamp']) / 1000.0
        ) + timedelta(
            hours=int(values.get('offset_h', 0)),
            minutes=int(values.get('offset_m', 0))
        )
    
    if len(values) > 3 or force_datetime:
        return datetime(**values)
    
    return date(**values)

def object_hook(dct):
    for key,value in dct.items():
        if isinstance(value, basestring):
            value = parse_date(value)
            if value:
                dct[key] = value
            
    return dct

class Manager(object):
    DECORATED_METHODS = ('get', 'save', 'filter', 'all', 'put')
    
    # Field names we need to convert to native python types.
    PLURAL_EXCEPTIONS = {'Addresse': 'Address'}
    
    # Fields that should not be sent to the server.
    NO_SEND_FIELDS = (u'UpdatedDateUTC',)
    
    def __init__(self, name, oauth, url):
        self.oauth = oauth
        self.name = name
        self.url = url

        # setup our singular variants of the name
        # only if the name ends in s
        if name[-1] == "s":
            self.singular = name[:len(name)-1]
        else:
            self.singular = name

        for method_name in self.DECORATED_METHODS:
            method = getattr(self, method_name)
            setattr(self, method_name, self._get_data(method, method_name))

    def dict_to_xml(self, root_elm, data):
        if not isinstance(data, dict):
            root_elm.text = data
            return root_elm
        
        for key in data.keys():
            if key in self.NO_SEND_FIELDS:
                continue
            
            sub_data = data[key]
            elm = SubElement(root_elm, key)

            is_list = isinstance(sub_data, (list, tuple))
            is_plural = key[len(key)-1] == "s"
            plural_name = key[:len(key)-1]

            # Key references a dict. Unroll the dict
            # as it's own XML node with subnodes
            if isinstance(sub_data, dict):
                self.dict_to_xml(elm, sub_data)

            # Key references a list/tuple
            elif is_list:
                # key name is a plural. This means each item
                # in the list needs to be wrapped in an XML
                # node that is a singular version of the list name.
                if is_plural:
                    plural_name = self.PLURAL_EXCEPTIONS.get(plural_name, plural_name)
                    for d in sub_data:
                        # The XML module will complain about Decimal objects
                        # Need to manually convert to str objects.
                        if isinstance(d, Decimal):
                            d = str(d)
                        self.dict_to_xml(SubElement(elm, plural_name), d)

                # key name isn't a plural. Just insert the content
                # as an XML node with subnodes
                else:
                    for d in sub_data:
                        self.dict_to_xml(elm, d)

            # Normal element - just inser the data.
            else:
                elm.text = str(sub_data)

        return root_elm

    def _prepare_data_for_save(self, data):
        if self.name == self.singular:
            root_elm = Element(self.name + 's')
        else:
            root_elm = Element(self.name)
        
        if not isinstance(data, (list, tuple)):
            data = [data]

        for d in data:
            sub_elm = SubElement(root_elm, self.singular)
            self.dict_to_xml(sub_elm, d)

        return tostring(root_elm)

    def _get_results(self, response):
        # Need custom handling for Organisation, as it returns
        #   {'Organisations':{'Organisation': {}}}
        # ie, the pluralised name is in the response.
        if self.name + 's' in response:
            return response[self.name + 's'][0]
        
        return response[self.name]


    def _get_data(self, func, name):
        def wrapper(*args, **kwargs):
            uri, method, body, headers = func(*args, **kwargs)
            if not headers:
                headers = {}
            headers['Accept'] = 'application/json'
            import time
            start = time.time()
            response = getattr(requests, method)(uri, data=body, headers=headers, auth=self.oauth)
            finish = time.time()
            logger.debug("Request to %s took %s", uri, finish-start)
            
            # There is a bug with the Xero API when asking for JSON, and
            # when there is a validation error. So, we re-run the request
            # asking for XML, and deal with the validation error later.
            # We can still get rid of the dom-walking code, as we don't need
            # to create the dict structure from error messages.
            # See https://community.xero.com/developer/discussion/26001/
            # for details.
            if response.status_code == 500:
                if response.request.headers.get('Accept', None) == 'application/json':
                    logger.debug("****\n\nRe-running request!")
                    start = time.time()
                    response = getattr(requests, method)(uri, data=body, headers={}, auth=self.oauth)
                    finish = time.time()
                    logger.debug("Request to %s took %s", uri, finish-start)
            
            logger.debug(response.text)
            
            if response.status_code == 200:
                if response.headers['content-type'] == 'application/pdf':
                    return response.text
                
                content = response.text.encode(response.encoding)
                
                # Can't use response.json(), we want to convert dates.
                data = json.loads(content, object_hook=object_hook)
                
                results = self._get_results(data)

                if name == 'get':
                    if isinstance(results, list):
                        if not len(results):
                            return {}
                        if len(results) == 1:
                            return results[0]
                        raise Exception('Multiple objects returned')
                elif name == 'all':
                    while not len(results) % 100:
                        batch = self.filter(page=len(results)/100 + 1)
                        if not len(batch):
                            break
                        results.extend(batch)

                return results

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

        return wrapper

    def get(self, id, headers=None):
        uri = '/'.join([self.url, self.name, id])
        return uri, 'get', None, headers

    def save_or_put(self, data, method='post', headers=None):
        uri = '/'.join([self.url, self.name])
        body = {'xml': self._prepare_data_for_save(data)}
        return uri, method, body, headers

    def save(self, data):
        return self.save_or_put(data, method='post')

    def put(self, data):
        return self.save_or_put(data, method='put')

    def prepare_filtering_date(self, val):
        if isinstance(val, datetime):
            val = val.strftime('%a, %d %b %Y %H:%M:%S GMT')
        else:
            val = '"%s"' % val
        return {'If-Modified-Since': val}

    def filter(self, **kwargs):
        headers = None
        page = kwargs.pop('page', None)
        since = kwargs.pop('since', None)
        
        uri = '/'.join([self.url, self.name])
        if kwargs:
            def get_filter_params():
                if isinstance(kwargs[key], bool):
                    return str(kwargs[key]).lower()
                elif isinstance(kwargs[key], datetime):
                    return kwargs[key].strftime('DateTime(%Y, %m, %d, %H, %M, %S)')
                elif isinstance(kwargs[key], date):
                    return kwargs[key].strftime('DateTime(%Y, %m, %d)')
                elif key.endswith('ID') or isinstance(kwargs[key], UUID):
                    return 'Guid("%s")' % kwargs[key]
                else:
                    return '"%s"' % str(kwargs[key])

            def generate_param(key):
                parts = key.split("__")
                field = key.replace('_', '.')
                fmt = '%s==%s'
                if len(parts) == 2:
                    # support filters:
                    # Name__Contains=John becomes Name.Contains("John")
                    if parts[1] in ["contains", "startswith", "endswith"]:
                        field = parts[0]
                        fmt = ''.join(['%s.', parts[1], '(%s)'])

                return fmt % (
                    field,
                    get_filter_params()
                )

            params = [generate_param(key) for key in kwargs.keys()]

            if params:
                uri += '?where=' + urllib.quote('&&'.join(params))
        
        if page:
            if '?' in uri:
                uri += '&page=%s' % int(page)
            else:
                uri += '?page=%s' % int(page)
        
        if since:
            headers = self.prepare_filtering_date(since)
        
        return uri, 'get', None, headers

    def all(self):
        uri = '/'.join([self.url, self.name])
        return uri, 'get', None, None
