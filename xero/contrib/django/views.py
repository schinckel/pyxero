import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django import forms

from xero.api import Xero
from xero.auth import PublicCredentials
from xero.exceptions import XeroUnauthorized, XeroBadRequest

from .signals import xero_authorised

logger = logging.getLogger('pyxero')


# Set CONSUMER_KEY, CONSUMER_SECRET, PAYROLL_SCOPE, CALLBACK_URL
config = {
    'CONSUMER_KEY': settings.XERO_CONSUMER_KEY,
    'CONSUMER_SECRET': settings.XERO_CONSUMER_SECRET,
    'SCOPE': getattr(settings, 'XERO_SCOPE', []),
    'CALLBACK_NAME': getattr(settings, 'XERO_CALLBACK_NAME', None)
}


class XeroOauthCallbackForm(forms.Form):
    """
    An uber-simple form, that just makes it easier for us to
    check that the data coming back from Xero was valid.
    """
    oauth_token = forms.CharField()
    oauth_verifier = forms.CharField()


@login_required
def xero_oauth_callback(request):
    """
    A view that will handle the callback from the Xero server on
    successful authorisation.
    
    Will send the signal 'xero_authorised', with an instance of the
    Xero object (as `api`), and the credentials object.
    
    You should be able to just hook this up in your urlconf.
    
    TODO: Handle invalid data better: it just currently redirects
    back to the same view, which would probably then re-ask for
    authentication...
    """
    form = XeroOauthCallbackForm(request.GET)
    
    if form.is_valid():
        credentials = PublicCredentials(**request.session['xero_credentials'])
        try:
            credentials.verify(form.cleaned_data['oauth_verifier'])
        except XeroUnauthorized as exc:
            logger.error('Unable to authorise')
            # Display a nicer error?
        else:
            request.session['xero_credentials'] = credentials.state
        
            api = Xero(credentials)
            # self.request.session['xero_organisation'] = api.organisation.all()
        
            xero_authorised.send(
                sender=request,
                api=api,
                credentials=credentials
            )
        
    return redirect(request.session.pop('xero_return_url'))


def reauthorise(request):
    credentials = PublicCredentials(
        config['CONSUMER_KEY'], 
        config['CONSUMER_SECRET'],
        callback_uri=request.build_absolute_uri(reverse(config['CALLBACK_NAME'])),
        scope=config['SCOPE']
    )
    
    request.session['xero_credentials'] = credentials.state
    
    if request.is_ajax():
        request.session['xero_return_url'] = request.build_absolute_uri(request.META['HTTP_REFERER'])
        template_name = 'xero/auth/ajax.html'
    else:
        request.session['xero_return_url'] = request.build_absolute_uri()
        template_name = 'xero/auth/page.html'
    
    return render(request, template_name, {'credentials': credentials})


class XeroMixin(object):
    """
    A mixin that can be included in any view that will need to access
    the Xero API.
    
    This will handle ensuring that authentication has taken place, and
    will handle push for reauthentication if it is no longer valid.
    
    You _should_ use this in conjuction with the `xero_oauth_callback`
    view, as it looks for `xero_credentials` in the session, which that
    view function will set for you.
    
    This also sets a property on the view class instance, called
    api, which contains a Xero() instance, with the verified credentials.
    """
    def reauthorise(self, request=None):
        return reauthorise(request or self.request)

    @method_decorator(login_required)
    def dispatch(self, request, *args, **kwargs):
        # We can't just pop it and continue, as that would then deauth when
        # we came back from Xero.
        if 'xero-force-reauth' in request.GET:
            request.session.pop('xero_credentials', None)
            return redirect(request.path)
        
        credentials = request.session.get('xero_credentials', None)
        
        if not credentials or not credentials['verified']:
            return reauthorise(request)
        
        credentials = PublicCredentials(**credentials)
        self.api = Xero(credentials)
        
        try:
            return super(XeroMixin, self).dispatch(request, *args, **kwargs)
        except XeroUnauthorized:
            return reauthorise(request)
        
        # Handle other errors?
