"""The "Help me plan" panel on an event's console pages (console/planner.py does the work).

The panel's log polls ``planner_chat`` while the assistant is busy. The button can be
dismissed for good (Profile.ai_planner_hidden); Organization > Backend brings it back.
"""
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base import ai
from events.models import Event, PlanningChat

from .. import planner
from ..decorators import staff_required


def _render_log(request, chat):
    items, replies = planner.transcript(chat)
    return render(request, 'console/partials/planner_log.html', {
        'event': chat.event, 'chat': chat, 'items': items, 'replies': [] if chat.busy else replies,
    })


def _start(request, chat):
    planner.start(chat, request.build_absolute_uri('/'))


@staff_required
def planner_chat(request, event_id):
    """The conversation. The first time it's opened, the assistant greets the organizer."""
    event = get_object_or_404(Event, pk=event_id)
    if not ai.is_enabled():
        return HttpResponse(status=204)
    chat, _created = PlanningChat.objects.get_or_create(event=event, user=request.user)
    if planner.is_stale(chat):
        chat.busy = False
        chat.error = _('The assistant stopped unexpectedly. Send your message again.')
        chat.save()
    if not chat.messages and not chat.busy:
        chat.messages = [{'role': 'user', 'content': planner.kickoff_text(chat, request.LANGUAGE_CODE)}]
        _start(request, chat)
    return _render_log(request, chat)


@staff_required
@require_POST
def planner_send(request, event_id):
    chat = get_object_or_404(PlanningChat, event_id=event_id, user=request.user)
    text = request.POST.get('message', '').strip()[:4000]
    if text and not chat.busy and ai.is_enabled():
        planner.add_user_message(chat, text)
        _start(request, chat)
    return _render_log(request, chat)


@staff_required
@require_POST
def planner_reset(request, event_id):
    """Start over: forget the conversation and greet again from the event as it is now."""
    chat = get_object_or_404(PlanningChat, event_id=event_id, user=request.user)
    if not chat.busy:
        chat.messages = []
        chat.error = ''
        chat.save()
    return planner_chat(request, event_id)


@staff_required
@require_POST
def planner_dismiss(request):
    profile = request.user.profile
    profile.ai_planner_hidden = True
    profile.save(update_fields=['ai_planner_hidden'])
    messages.info(request, _('"Help me plan" is hidden. You can bring it back in Organization > Backend.'))
    response = HttpResponse('')
    response['HX-Trigger'] = 'closeDialog'
    return response


@staff_required
@require_POST
def planner_show(request):
    profile = request.user.profile
    profile.ai_planner_hidden = False
    profile.save(update_fields=['ai_planner_hidden'])
    messages.success(request, _('"Help me plan" is back on event pages.'))
    return redirect('console_settings_backend')
