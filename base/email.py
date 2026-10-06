"""Sending email through listmonk.

Views call ``notify(trigger, user, data)`` for an app event (base/triggers.py). The
wording lives in the listmonk template for that trigger and the recipient's language,
so views pass data only. Anything sent through Django's own mail API (the console's
"Send test email", say) goes out with the "system" trigger's template.
"""
import threading

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.console import EmailBackend as ConsoleBackend
from django.db import connection, transaction
from django.urls import reverse
from django.utils import formats, timezone, translation
from django.utils.html import linebreaks, urlize

from . import email_templates, listmonk, triggers
from .models import EmailTemplate, SiteSettings


def text_to_html(text):
    return linebreaks(urlize(text, autoescape=True))


def template_for(trigger, lang):
    """The listmonk template ID for ``trigger`` in ``lang``, else in the default language."""
    rows = dict(EmailTemplate.objects.filter(trigger=trigger).values_list('language', 'template_id'))
    return rows.get(lang) or rows.get(settings.LANGUAGE_CODE) or next(iter(rows.values()), None)


def deliver(trigger, emails, data, lang=None):
    """Send now with ``trigger``'s template. Without listmonk, print the data instead."""
    site = SiteSettings.get_settings()
    data = {'org': site.company_name, **data}
    if not listmonk.is_configured():
        message = EmailMessage(
            f'[{trigger}] {triggers.BY_KEY[trigger].label}',
            '\n'.join(f'{key}: {value}' for key, value in data.items()), None, emails)
        message.listmonk_kind, message.listmonk_data = trigger, data
        message.send()
        return
    template = template_for(trigger, lang or settings.LANGUAGE_CODE)
    if not template:
        raise listmonk.ListmonkError(
            f'No listmonk template for "{trigger}". Use "Re-create missing templates" in Communication > Email.')
    listmonk.send_tx(template, emails, data, from_email=site.default_from_email or None)


def notify(trigger, user, data=None):
    """Email ``user`` about an app event, if that trigger is switched on in
    Communication > Email, in the language they last used the site in.

    With listmonk, the email is sent in the background after the transaction
    commits, so a slow or offline server never holds up the request.
    """
    if not user.email or not triggers.setting(trigger)[0]:
        return
    profile = getattr(user, 'profile', None)
    lang = (profile and profile.language) or settings.LANGUAGE_CODE
    data = {'name': user.first_name or user.username, **(data or {})}
    if not listmonk.is_configured():
        deliver(trigger, [user.email], data, lang)
        return
    transaction.on_commit(lambda: threading.Thread(
        target=_deliver_in_background, args=(trigger, user.email, data, lang), daemon=True).start())


def _deliver_in_background(trigger, email, data, lang):
    try:
        deliver(trigger, [email], data, lang)
    except listmonk.ListmonkError as exc:
        print(f'[listmonk] Could not send "{trigger}" email to {email}: {exc}')
    finally:
        connection.close()


def send_welcome_email(request, user):
    """'Account created' email, for sign-ups and volunteers added in the console."""
    notify('welcome', user, {
        'link': request.build_absolute_uri(reverse('explore_opportunities')),
        'id_code': user.profile.id_code,
    })


def announce_event(event, link, lang):
    """'New event published': a visual campaign to "Town Hall users" (plus any lists staff
    added), built from the trigger's visual template in ``lang``.

    Returns 'sent', 'draft', or None when the trigger is off or listmonk isn't set up.
    Raises ListmonkError so staff see what went wrong.
    """
    enabled, auto_send, extra_lists = triggers.setting('event_published')
    if not enabled or not listmonk.is_configured():
        return None
    site = SiteSettings.get_settings()
    if not site.listmonk_list_id:
        raise listmonk.ListmonkError('The "Town Hall users" list is missing. Save Organization > Backend to create it.')
    template_id = template_for('event_published', lang)
    if not template_id:
        raise listmonk.ListmonkError('No template for "New event published". Re-create it in Communication > Email.')
    template = listmonk.api('GET', f'/templates/{template_id}')
    if template.get('type') != 'campaign_visual' or not template.get('body_source'):
        raise listmonk.ListmonkError(f'"{template.get("name")}" is not a visual campaign template.')

    image_url = listmonk.upload_file(event.image) if event.image else ''
    with translation.override(lang):
        subject = str(triggers.BY_KEY['event_published'].subject) % {'event': event.title}
        date = formats.date_format(timezone.localtime(event.start_date), 'DATETIME_FORMAT')
    body, source = email_templates.fill_event(template, {
        '[event_title]': event.title,
        '[event_date]': date,
        '[event_location]': event.full_location,
        '[event_description]': event.description,
    }, link, image_url)
    lists = [site.listmonk_list_id] + [i for i in extra_lists if i != site.listmonk_list_id]
    listmonk.create_visual_campaign(event.title, subject, body, source, lists, send=auto_send)
    return 'sent' if auto_send else 'draft'


class ListmonkEmailBackend(BaseEmailBackend):
    """Django mail backend for messages sent with ``send_mail`` and friends: they go
    through listmonk with the "system" template, which shows the message as-is.

    Prints emails to the server console when listmonk isn't configured.
    """

    def send_messages(self, email_messages):
        if not listmonk.is_configured():
            return ConsoleBackend(fail_silently=self.fail_silently).send_messages(email_messages)
        sent = 0
        for message in email_messages:
            html = next((content for content, mimetype in getattr(message, 'alternatives', ())
                         if mimetype == 'text/html'), None) or text_to_html(message.body)
            try:
                deliver('system', message.recipients(), {
                    'subject': message.subject, 'body': message.body, 'html': html,
                }, translation.get_language())
            except listmonk.ListmonkError:
                if not self.fail_silently:
                    raise
            else:
                sent += 1
        return sent
