"""URL fields for connections to other services (listmonk, LibreTranslate, Bell Tower).

Django's URLValidator wants a dotted domain, so it rejects the single-label host names
that Docker, Compose and Kubernetes give services on an internal network
(``http://listmonk-marketing:9000``). These fields accept those as well.
"""
import re

from django import forms
from django.core.validators import URLValidator
from django.db import models
from django.utils.deconstruct import deconstructible
from django.utils.regex_helper import _lazy_re_compile


@deconstructible
class ServiceURLValidator(URLValidator):
    """URLValidator that also allows single-label hosts such as ``listmonk`` or
    ``belltower_web`` (underscores appear in Compose service names)."""
    schemes = ['http', 'https']
    single_label_re = r'(?:[a-z0-9¡-￿](?:[a-z0-9_¡-￿-]{0,61}[a-z0-9¡-￿])?)'
    host_re = '(' + URLValidator.hostname_re + URLValidator.domain_re + URLValidator.tld_re + '|localhost|' + single_label_re + ')'
    regex = _lazy_re_compile(
        r'^(?:[a-z0-9.+-]*)://'
        r'(?:[^\s:@/]+(?::[^\s:@/]*)?@)?'
        r'(?:' + URLValidator.ipv4_re + '|' + URLValidator.ipv6_re + '|' + host_re + ')'
        r'(?::[0-9]{1,5})?'
        r'(?:[/?#][^\s]*)?'
        r'\Z',
        re.IGNORECASE,
    )


class ServiceURLFormField(forms.URLField):
    default_validators = [ServiceURLValidator()]


class ServiceURLField(models.URLField):
    default_validators = [ServiceURLValidator()]

    def formfield(self, **kwargs):
        return super().formfield(**{'form_class': ServiceURLFormField, **kwargs})
