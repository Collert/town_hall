from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _


class FirstRunSetupForm(forms.Form):
    """The first account on a fresh install, which becomes the superuser."""
    company_name = forms.CharField(label=_('Organization name'), max_length=30)
    first_name = forms.CharField(label=_('First Name'), max_length=150)
    last_name = forms.CharField(label=_('Last Name'), max_length=150, required=False)
    email = forms.EmailField(label=_('Email Address'))
    password = forms.CharField(label=_('Password'), widget=forms.PasswordInput)
    password_confirm = forms.CharField(label=_('Confirm Password'), widget=forms.PasswordInput)

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('password')
        if password and password != cleaned.get('password_confirm'):
            self.add_error('password_confirm', _("The passwords don't match."))
        elif password:
            user = User(username=cleaned.get('email', ''), email=cleaned.get('email', ''),
                        first_name=cleaned.get('first_name', ''), last_name=cleaned.get('last_name', ''))
            try:
                validate_password(password, user)
            except forms.ValidationError as error:
                self.add_error('password', error)
        return cleaned
