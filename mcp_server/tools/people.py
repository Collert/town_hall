"""People: volunteers' and staff's profiles, endorsements and feedback. Read-only:
the connector never changes anything about a person."""
from django.contrib.auth.models import User
from django.db.models import Avg, Q
from django.utils import timezone

from base.models import Endorsement
from events.models import EventFeedback
from jobs.models import Shift

from . import get_or_error, integer, local, person, string, text, tool
from .events import _event


def _shift_role(shift):
    role = shift.event_role_slot.role if shift.event_role_slot_id else shift.role
    return role.name if role else None


@tool('search_people', 'Search people',
      'Find volunteers and staff by name, email or skill. Use get_person for a full profile.',
      {
          'query': string('Words to match in name, username, email or skills; empty lists everyone'),
          'limit': integer('At most 50 (default 20)', minimum=1),
      }, read_only=True)
def search_people(ctx, query='', limit=20):
    users = User.objects.filter(is_active=True).select_related('profile')
    for word in text(query).split():
        users = users.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word) | Q(username__icontains=word)
                             | Q(email__icontains=word) | Q(profile__skills__name__icontains=word))
    users = users.distinct().order_by('-profile__impact_points')[:min(limit, 50)]
    return [{
        **person(u), 'staff': u.is_staff, 'impact_points': u.profile.impact_points,
        'level': str(u.profile.level) if u.profile.level else None,
        'skills': [s.name for s in u.profile.skills.all()],
    } for u in users] or 'Nobody matches.'


@tool('get_person', 'Get a person\'s profile',
      'A person\'s full profile: contact details, skills, level and impact points, hours, training finished, '
      'certifications, shifts, endorsements from teammates, the event feedback they gave, and private staff notes.',
      {'user_id': integer('The person')}, ['user_id'], read_only=True)
def get_person(ctx, user_id):
    user = get_or_error(User.objects.select_related('profile'), user_id, 'person')
    profile = user.profile
    now = timezone.now()
    shifts = Shift.objects.filter(user=user).select_related('event_role_slot__event', 'role', 'event_role_slot__role').order_by('-start_time')
    endorsements = (Endorsement.objects.filter(endorsed=user).select_related('endorser', 'event')
                    .prefetch_related('skills').order_by('-timestamp'))
    return {
        **person(user),
        'username': user.username, 'email': user.email, 'phone': profile.phone or None,
        'location': profile.location or None, 'bio': profile.bio or None,
        'staff': user.is_staff, 'joined': local(user.date_joined),
        'level': str(profile.level) if profile.level else None, 'impact_points': profile.impact_points,
        'hours_contributed': profile.hours_contributed(),
        'skills': [s.name for s in profile.skills.all()],
        'permanent_roles': [r.name for r in profile.permanent_roles.all()],
        'training_completed': [
            {'module': c.training_module.title, 'completed': local(c.completed_at)}
            for c in user.training_module_completions.select_related('training_module').order_by('-completed_at')
        ],
        'certifications': [
            {'certificate': c.certificate.name, 'verified': c.verified, 'rejected': c.rejected,
             'expires': local(c.expiration_date)}
            for c in user.certifications.select_related('certificate')
        ],
        'upcoming_shifts': [
            {'event_id': s.event_id, 'event': s.event.title, 'role': s.role.name, 'start': local(s.start_time), 'slot_id': s.pk}
            for s in user.commitments.filter(end_time__gt=now).select_related('event', 'role').order_by('start_time')[:15]
        ],
        'recent_shifts': [{
            'event': sh.event_role_slot.event.title if sh.event_role_slot_id else None,
            'role': _shift_role(sh),
            'start': local(sh.start_time), 'hours': round(sh.duration(), 1) if sh.end_time else None,
        } for sh in shifts[:15]],
        'endorsements_received': [{
            'from': person(e.endorser), 'skills': [s.name for s in e.skills.all()], 'text': e.text or None,
            'event': e.event.title if e.event_id else None, 'date': local(e.timestamp),
        } for e in endorsements[:30]],
        'endorsements_given': user.given_endorsements.count(),
        'event_feedback_given': [{
            'event': f.event.title, 'rating': f.rating, 'enjoyed': f.enjoyed or None,
            'suggestions': f.suggestions or None, 'date': local(f.created_at),
        } for f in user.event_feedback.select_related('event').order_by('-created_at')[:20]],
        'staff_notes': profile.admin_notes or None,
        'staff_ratings': [{
            'rating': f.rating, 'note': f.note or None, 'by': person(f.author) if f.author else None, 'date': local(f.timestamp),
        } for f in user.admin_feedback.select_related('author')[:20]],
    }


@tool('get_event_feedback', 'Get event feedback',
      'What volunteers said about an event in the post-event survey, and the endorsements teammates gave each other '
      'there. Useful when planning the next edition of an event.',
      {'event_id': integer('The event')}, ['event_id'], read_only=True)
def get_event_feedback(ctx, event_id):
    event = _event(event_id)
    feedback = EventFeedback.objects.filter(event=event).select_related('user').order_by('-created_at')
    rating = feedback.aggregate(avg=Avg('rating'))['avg']
    return {
        'event_id': event.pk, 'event': event.title,
        'responses': feedback.count(), 'average_rating': round(rating, 2) if rating else None,
        'feedback': [{
            'from': person(f.user), 'rating': f.rating, 'enjoyed': f.enjoyed or None, 'suggestions': f.suggestions or None,
        } for f in feedback],
        'endorsements': [{
            'from': person(e.endorser), 'to': person(e.endorsed), 'skills': [s.name for s in e.skills.all()], 'text': e.text or None,
        } for e in Endorsement.objects.filter(event=event).select_related('endorser', 'endorsed').prefetch_related('skills')],
        'post_event_statement': event.post_event_statement or None,
    }
