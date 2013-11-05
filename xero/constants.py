
XERO_BASE_URL = "https://api.xero.com"
XERO_PARTNER_BASE_URL = "https://api-partner.network.xero.com"

# Authorise url is always the same, regardless of application type?

AUTHORIZE_URL = "%s/oauth/Authorize" % XERO_BASE_URL

REQUEST_TOKEN_URL = "%s/oauth/RequestToken"
ACCESS_TOKEN_URL = "%s/oauth/AccessToken"
XERO_API_URL = "%s/api.xro/2.0"
XERO_PAYROLL_API_URL = "%s/payroll.xro/1.0"