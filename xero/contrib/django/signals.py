from django.dispatch import Signal

xero_authorised = Signal(providing_args=['api', 'credentials'])
