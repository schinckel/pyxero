from xml.dom.minidom import parseString
from xml.etree.ElementTree import tostring, SubElement, Element
from datetime import datetime
from dateutil.parser import parse
import urllib
import requests
from urlparse import parse_qs
from decimal import Decimal
from uuid import UUID

from .constants import XERO_API_URL
from .exceptions import *

def isplural(word):
    return word[-1].lower() == 's'

def singular(word):
    if isplural(word):
        return word[:-1]
    return word

class Manager(object):
    DECORATED_METHODS = ('get', 'save', 'filter', 'all', 'put')
    
    # Field names we need to convert to native python types.
    DATETIME_FIELDS = (u'UpdatedDateUTC', u'Updated', u'FullyPaidOnDate', 
                       u'DateTimeUTC', u'CreatedDateUTC', )
    DATE_FIELDS = (u'DueDate', u'Date',  u'PaymentDate', 
                   u'StartDate', u'EndDate',
                   u'PeriodLockDate', u'DateOfBirth',
                   u'OpeningBalanceDate',
                   )
    BOOLEAN_FIELDS = (u'IsSupplier', u'IsCustomer', u'IsDemoCompany',
                      u'PaysTax', u'IsAuthorisedToApproveTimesheets',
                      u'IsAuthorisedToApproveLeave', u'HasHELPDebt',
                      u'AustralianResidentForTaxPurposes',
                      u'TaxFreeThresholdClaimed', u'HasSFSSDebt',
                      u'EligibleToReceiveLeaveLoading',
                      u'IsExemptFromTax', u'IsExemptFromSuper',
                      )
    DECIMAL_FIELDS = (u'Hours', u'NumberOfUnit')
    INTEGER_FIELDS = (u'FinancialYearEndDay', u'FinancialYearEndMonth')
    PLURAL_EXCEPTIONS = {'Addresse': 'Address'}
    
    # Fields that should not be sent to the server.
    NO_SEND_FIELDS = (u'UpdatedDateUTC',)
    # Currently experimenting with just converting all things ending in 'ID'
    # to UUID values.
    GUID_FIELDS = (u'EmployeeID',)
    
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

    def walk_dom(self, dom):
        tree_list = tuple()
        for node in dom.childNodes:
            tagName = getattr(node, 'tagName', None)
            if tagName:
                tree_list += (tagName, self.walk_dom(node),)
            else:
                data = node.data.strip()
                if data:
                    tree_list += (node.data.strip(),)
        return tree_list

    def convert_to_dict(self, deep_list):
        out = {}

        if len(deep_list) > 2:
            lists = [l for l in deep_list if isinstance(l, tuple)]
            keys = [l for l in deep_list if isinstance(l, unicode)]

            if len(keys) > 1 and len(set(keys)) == 1:
                # This is a collection... all of the keys are the same.
                return [self.convert_to_dict(data) for data in lists]

            for key, data in zip(keys, lists):
                if not data:
                    # Skip things that are empty tags?
                    continue

                if len(data) == 1:
                    # we're setting a value
                    # check to see if we need to apply any special
                    # formatting to the value
                    val = data[0]
                    if key in self.DECIMAL_FIELDS:
                        val = Decimal(val)
                    elif key in self.BOOLEAN_FIELDS:
                        val = True if val.lower() == 'true' else False
                    elif key in self.DATETIME_FIELDS:
                        val = parse(val)
                    elif key in self.DATE_FIELDS:
                        val = parse(val).date()
                    elif key in self.GUID_FIELDS or key.endswith('ID'):
                        val = UUID(val)
                    elif key in self.INTEGER_FIELDS:
                        val = int(val)
                    
                    data = val
                else:
                    # We have a deeper data structure, that we need
                    # to recursively process.
                    data = self.convert_to_dict(data)
                    # Which may itself be a collection. Quick, check!
                    if isinstance(data, dict) and isplural(key) and [singular(key)] == data.keys():
                        data = [data[singular(key)]]

                out[key] = data

        elif len(deep_list) == 2:
            key = deep_list[0]
            data = self.convert_to_dict(deep_list[1])

            # If our key is repeated in our child object, but in singular
            # form (and is the only key), then this object is a collection.
            if isplural(key) and [singular(key)] == data.keys():
                data = [data[singular(key)]]

            out[key] = data
        else:
            out = deep_list[0]
        return out

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
        root_elm = Element(self.name)
        
        if not isinstance(data, (list, tuple)):
            data = [data]

        for d in data:
            sub_elm = SubElement(root_elm, self.singular)
            self.dict_to_xml(sub_elm, d)

        return tostring(root_elm)

    def _get_results(self, data):
        response = data[u'Response']
        # Need custom handling for Organisation, as it returns
        #   {'Organisations':{'Organisation': {}}}
        # ie, the pluralised name is in the response.
        if self.name + 's' in response:
            response = response[self.name + 's']
        
        result = response.get(self.name, {})
        
        if isinstance(result, dict) and self.singular in result:
            return result[self.singular]

        return result


    def _get_data(self, func, name):
        def wrapper(*args, **kwargs):
            uri, method, body, headers = func(*args, **kwargs)
            if body and 'xml' in body:
                body = body['xml']
                if not headers:
                    headers = {}
                headers['Content-Type'] = 'application/xml'
            response = getattr(requests, method)(uri, data=body, headers=headers, auth=self.oauth)

            if response.status_code == 200:
                if response.headers['content-type'] == 'application/pdf':
                    return response.text
                # parseString takes byte content, not unicode.
                dom = parseString(response.text.encode(response.encoding))
                data = self.convert_to_dict(self.walk_dom(dom))
                results = self._get_results(data)

                if name == 'get':
                    if isinstance(results, list):
                        if not len(results):
                            return {}
                        if len(results) == 1:
                            return results[0]
                        raise Exception('Multiple objects returned')

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
        uri = '/'.join([self.url, self.name])
        if kwargs:
            if 'since' in kwargs:
                val = kwargs['since']
                headers = self.prepare_filtering_date(val)
                del kwargs['since']

            def get_filter_params():
                if key in self.BOOLEAN_FIELDS:
                    return 'true' if kwargs[key] else 'false'
                elif key in self.DATETIME_FIELDS:
                    return kwargs[key].isoformat()
                elif key in self.GUID_FIELDS or key.endswith('ID') or isinstance(kwargs[key], UUID):
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
                uri += '&page=%s' % page
            else:
                uri += '?page=%s' % page
        
        return uri, 'get', None, headers

    def all(self):
        uri = '/'.join([self.url, self.name])
        return uri, 'get', None, None
