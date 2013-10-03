from .manager import Manager
from .constants import XERO_PAYROLL_API_URL, XERO_API_URL

class Xero(object):
    """An ORM-like interface to the Xero API"""

    OBJECT_LIST = (u'Contacts', u'Accounts', u'CreditNotes',
                   u'Currencies', u'Invoices', u'Organisation',
                   u'Payments', u'TaxRates', u'TrackingCategories')
    PAYROLL_OBJECT_LIST = (u'Employees', u'LeaveApplications', u'PayItems',
                           u'PayrollCalendars', u'PayRuns', u'Payslip',
                           u'Settings', u'SuperFunds', u'SuperFundProducts', 
                           u'Timesheets')

    def __init__(self, credentials):
        # Iterate through the list of objects we support, for
        # each of them create an attribute on our self that is
        # the lowercase name of the object and attach it to an
        # instance of a Manager object to operate on it
        for name in self.OBJECT_LIST:
            setattr(self, name.lower(), Manager(name, credentials.oauth, url=XERO_API_URL))
        
        for name in self.PAYROLL_OBJECT_LIST:
            setattr(self, name.lower(), Manager(name, credentials.oauth, url=XERO_PAYROLL_API_URL))
        
        self._organisation = None
    
    
    # A way to link directly to specific pages within Xero that we might
    # need users to access. This may move back into my application.
    @property
    def links(self):
        if self._organisation is None:
            self._organisation = self.organisation.all()
        
        return {
            'dashboard': 'https://my.xero.com//Action/OrganisationLogin/%(ShortCode)s' % self._organisation,
            'payroll_settings': 'https://payroll.xero.com/Settings?CID=%(ShortCode)s' % self._organisation,
            'earnings_rates': 'https://payroll.xero.com/Settings/PayslipItems/EarningsRates?CID=%(ShortCode)s' % self._organisation,
            
        }
