from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth import forms as auth_forms
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from education.models import Skill, TrainingModuleCompletion
from events.models import Event, EventFeedback, EventRoleSlot
from jobs.models import Shift

from .email import notify, send_welcome_email
from .models import AdminFeedback, Endorsement, HeroSection, Notification, PointsEntry, SiteSettings, Venue
from .utils import qr_svg


def _safe_next(request, fallback='home'):
    """The ?next= / POST next target if it points at this site, else the fallback URL."""
    target = request.POST.get('next') or request.GET.get('next')
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return reverse(fallback) if fallback and '/' not in fallback else fallback


def venue_detail(request, venue_id):
    """Public info page for a venue: what it offers, when it's open and what's on there."""
    venue = get_object_or_404(Venue.objects.prefetch_related('features', 'operating_hours'), pk=venue_id)
    events = venue.events.filter(published=True, end_date__gte=timezone.now()).order_by('start_date')
    return render(request, 'base/venue_detail.html', {
        'venue': venue,
        'is_open': venue.is_open_now(),
        'weekly_hours': venue.weekly_hours(),
        'feature_groups': venue.grouped_features(),
        'notes': venue.active_notes(),
        'events': events[:6],
        'roles': venue.roles.filter(permanent=True).order_by('name'),
    })


def home(request):
    hero, _created = HeroSection.objects.get_or_create(pk=1)
    now = timezone.now()
    hours = sum(shift.duration() for shift in Shift.objects.filter(end_time__isnull=False))
    featured_events = Event.objects.filter(featured=True, published=True, start_date__gte=now).order_by('start_date')[:3]
    return render(request, 'base/home.html', {
        'hero': hero,
        'active_users': User.objects.filter(is_active=True).count(),
        'hours_contributed': round(hours),
        'projects_completed': Event.objects.filter(end_date__lt=now).count(),
        'featured_events': featured_events,
    })


def theme_settings_edit(request):
    """Theme editing moved to the admin console (Organization Identity)."""
    return redirect('console_settings')


@login_required
def read_notification(request, notification_id):
    notification = get_object_or_404(request.user.notifications, pk=notification_id)
    notification.read = True
    notification.save(update_fields=['read'])
    link = notification.link
    if link and url_has_allowed_host_and_scheme(link, allowed_hosts={request.get_host()}):
        return redirect(link)
    return redirect('home')


@login_required
def clear_notifications(request):
    request.user.notifications.filter(read=False).update(read=True)
    referer = request.META.get('HTTP_REFERER')
    if referer and url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}):
        return redirect(referer)
    return redirect('home')


def _auth_page_context(request):
    """Real social proof for the login/signup pages: a 4-5 star event review and the volunteer count."""
    reviews = (EventFeedback.objects.filter(rating__gte=4).exclude(enjoyed='')
               .select_related('user__profile', 'event').order_by('?'))
    volunteers = User.objects.filter(is_active=True, is_staff=False)
    return {
        'next': request.GET.get('next', ''),
        'testimonial': reviews.first(),
        'volunteer_count': volunteers.count(),
        'recent_volunteers': volunteers.select_related('profile').order_by('-date_joined')[:3],
    }


def login_view(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request))

    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password')
        # Django's default auth uses username, so we look up the user by email first
        user_obj = User.objects.filter(email__iexact=email).first() if email else None
        user = authenticate(request, username=user_obj.username if user_obj else None, password=password)

        if user is not None:
            login(request, user)
            messages.success(request, _('Successfully logged in.'))
            return redirect(_safe_next(request))
        messages.error(request, _('Invalid email or password.'))

    return render(request, 'base/login.html', _auth_page_context(request))


def _notify_endorsed(request, target, skills, text=''):
    endorser = request.user.get_full_name() or request.user.username
    link = request.build_absolute_uri(reverse('profile_view', args=[target.username]))
    skill_names = ', '.join(skill.name for skill in skills)
    notify('endorsement_received', target, {'from_name': endorser, 'skills': skill_names, 'message': text, 'link': link})


def signup_view(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password')

        if not email or not password:
            messages.error(request, _('Email and password are required.'))
        elif User.objects.filter(email__iexact=email).exists():
            messages.error(request, _('An account with that email already exists.'))
        else:
            username = email[:150]
            if User.objects.filter(username=username).exists():
                import uuid
                username = f"{email.split('@')[0][:140]}_{uuid.uuid4().hex[:6]}"

            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
            )
            login(request, user)
            send_welcome_email(request, user)
            messages.success(request, _('Account created successfully!'))
            return redirect(_safe_next(request))

    return render(request, 'base/signup.html', _auth_page_context(request))


class PasswordResetForm(auth_forms.PasswordResetForm):
    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        """Send through the "Password reset requested" listmonk template, with the link as data."""
        link = '{}://{}{}'.format(context['protocol'], context['domain'], reverse(
            'password_reset_confirm', kwargs={'uidb64': context['uid'], 'token': context['token']}))
        notify('password_reset', context['user'], {'link': link})


class PasswordResetView(auth_views.PasswordResetView):
    """Email a reset link. Always shows the same confirmation, so it never reveals which emails exist."""
    form_class = PasswordResetForm
    template_name = 'base/password_reset_form.html'
    success_url = reverse_lazy('password_reset_done')


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = 'base/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')


def logout_view(request):
    logout(request)
    return redirect('home')


def _skills_context(profile):
    settings = SiteSettings.get_settings()
    user_skills = profile.skills.order_by('name')
    return {
        'user_skills': user_skills,
        'available_skills': Skill.objects.exclude(pk__in=user_skills.values('pk')).order_by('name'),
        'max_skills': settings.max_skills_per_user,
    }


@login_required
def edit_profile(request):
    profile = request.user.profile

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()

        user = request.user
        if email and email != user.email:
            if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                messages.error(request, _('That email address is already in use.'))
                return redirect('edit_profile')
            user.email = email

        user.first_name = first_name
        user.last_name = last_name
        user.save()

        profile.phone = request.POST.get('phone', '').strip()
        profile.location = request.POST.get('location', '').strip()
        profile.bio = request.POST.get('bio', '').strip()
        if 'avatar' in request.FILES:
            profile.avatar = request.FILES['avatar']
        profile.save()
        messages.success(request, _('Profile updated successfully!'))
        return redirect('edit_profile')

    return render(request, 'base/edit_profile.html', {'profile': profile, **_skills_context(profile)})


@login_required
@require_POST
def profile_skills(request):
    """HTMX: add or remove one of the current user's skills, re-render the widget."""
    profile = request.user.profile
    skill = get_object_or_404(Skill, pk=request.POST.get('skill_id'))
    context = _skills_context(profile)
    if request.POST.get('action') == 'add':
        if profile.skills.count() >= context['max_skills']:
            messages.warning(request, _('You can only add up to %(max)d skills.') % {'max': context['max_skills']})
        else:
            profile.skills.add(skill)
    else:
        profile.skills.remove(skill)
    return render(request, 'base/partials/profile_skills.html', _skills_context(profile))


@login_required
def change_password(request):
    if request.method == 'POST':
        current = request.POST.get('current_password', '')
        new_pw = request.POST.get('new_password', '')
        confirm = request.POST.get('confirm_password', '')

        if not request.user.check_password(current):
            messages.error(request, _('Current password is incorrect.'))
        elif len(new_pw) < 8:
            messages.error(request, _('New password must be at least 8 characters.'))
        elif new_pw != confirm:
            messages.error(request, _('Passwords do not match.'))
        else:
            request.user.set_password(new_pw)
            request.user.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, _('Password updated successfully.'))

    return redirect('edit_profile')


@login_required
@require_POST
def reset_kiosk_code(request):
    """HTMX: give the user a new kiosk check-in code; the old one stops working."""
    profile = request.user.profile
    profile.reset_id_code()
    messages.success(request, _('Your kiosk ID was changed. The old one no longer works.'))
    return render(request, 'base/partials/kiosk_code_card.html', {'profile': profile})


@login_required
def deactivate_account(request):
    if request.method == 'POST':
        password = request.POST.get('password', '')
        if request.user.check_password(password):
            request.user.is_active = False
            request.user.save()
            logout(request)
            messages.success(request, _('Your account has been deactivated.'))
            return redirect('home')
        else:
            messages.error(request, _('Incorrect password. Account not deactivated.'))

    return redirect('edit_profile')


def profile_view(request, username):
    viewed_user = get_object_or_404(User, username=username, is_active=True)
    profile = viewed_user.profile
    now = timezone.now()

    # 5 latest endorsements for public feedback feed
    recent_endorsements = list(
        viewed_user.endorsements.select_related(
            'endorser', 'endorser__profile'
        ).prefetch_related('skills').order_by('-timestamp')[:5]
    )

    # 3 most recent training completions
    raw_completions = list(
        viewed_user.training_module_completions.select_related('training_module')
        .order_by('-completed_at')[:3]
    )
    completions = []
    for c in raw_completions:
        module = c.training_module
        expiry_date = None
        status = 'verified'
        if module.expires_after_days:
            expiry_date = c.completed_at + timedelta(days=module.expires_after_days)
            if expiry_date < now:
                status = 'expired'
            elif (expiry_date - now).days <= 60:
                status = 'expiring_soon'
        completions.append({
            'completion': c,
            'module': module,
            'expiry_date': expiry_date,
            'status': status,
        })

    # Recent event activity
    activity_history = []
    for slot in EventRoleSlot.objects.filter(
        signups=viewed_user
    ).select_related('event', 'role').order_by('-event__start_date')[:8]:
        duration_h = (slot.end_time - slot.start_time).total_seconds() / 3600
        activity_history.append({
            'event_title': slot.event.title,
            'role_name': slot.role.name,
            'date': slot.event.start_date,
            'hours': round(duration_h, 1),
        })

    # Impact stats
    total_hours = round(sum(
        s.duration() for s in Shift.objects.filter(user=viewed_user, end_time__isnull=False)
    ))
    projects_completed = EventRoleSlot.objects.filter(
        signups=viewed_user,
        event__end_date__lt=now,
    ).values('event').distinct().count()

    # Per-skill endorsement counts for endorsement summary rows
    counts = dict(viewed_user.endorsements.values('skills').annotate(n=Count('pk')).values_list('skills', 'n'))
    skill_endorsement_counts = [
        {'skill': skill, 'count': counts.get(skill.pk, 0)} for skill in profile.skills.all()
    ]

    # Up to 4 recent unique endorsers
    endorser_ids_seen = set()
    recent_endorsers = []
    for e in viewed_user.endorsements.select_related(
        'endorser', 'endorser__profile'
    ).order_by('-timestamp'):
        if e.endorser_id not in endorser_ids_seen:
            endorser_ids_seen.add(e.endorser_id)
            recent_endorsers.append(e.endorser)
            if len(recent_endorsers) >= 4:
                break
    total_endorsers = viewed_user.endorsements.values('endorser').distinct().count()

    return render(request, 'base/profile.html', {
        'viewed_user': viewed_user,
        'profile': profile,
        'recent_endorsements': recent_endorsements,
        'completions': completions,
        'activity_history': activity_history,
        'total_hours': total_hours,
        'projects_completed': projects_completed,
        'level': profile.level,
        'next_level': profile.next_level,
        'level_progress': profile.level_progress,
        'is_own_profile': request.user == viewed_user,
        # Only the volunteer sees how their points add up
        'points_history': (PointsEntry.objects.filter(user=viewed_user)
                           .select_related('shift__role', 'shift__event_role_slot__event', 'training_module')[:6]
                           if request.user == viewed_user else []),
        'verified_skill_ids': set(counts),
        'recent_endorsers': recent_endorsers,
        'extra_endorsers': max(0, total_endorsers - len(recent_endorsers)),
        'skill_endorsement_counts': skill_endorsement_counts,
    })


@login_required
def my_profile(request):
    return redirect('profile_view', username=request.user.username)


def terms_of_service(request):
    return render(request, 'base/terms_of_service.html', {
        'terms': SiteSettings.get_settings().terms_of_service,
    })


# ---------------------------------------------------------------------------
# Endorsements
# ---------------------------------------------------------------------------

def endorsements_feed(request):
    endorsements = Endorsement.objects.filter(endorsed__is_active=True).select_related(
        'endorser', 'endorser__profile', 'endorsed', 'endorsed__profile', 'event',
    ).prefetch_related('skills').order_by('-timestamp')
    query = request.GET.get('q', '').strip()
    skill_id = request.GET.get('skill', '')
    if query:
        def person_matches(side):
            # Filters by who received the endorsement. Every word must match, so full names like "Jane Doe" work
            match = Q()
            for word in query.split():
                match &= (Q(**{f'{side}__first_name__icontains': word}) | Q(**{f'{side}__last_name__icontains': word})
                          | Q(**{f'{side}__username__icontains': word}))
            return match
        endorsements = endorsements.filter(person_matches('endorsed'))
    if skill_id.isdigit():
        endorsements = endorsements.filter(skills=skill_id)
    page = Paginator(endorsements, 8).get_page(request.GET.get('page'))
    context = {'page': page, 'query': query, 'skill_id': skill_id}

    if request.headers.get('HX-Request') == 'true':
        return render(request, 'base/partials/endorsement_feed.html', context)

    top = (Endorsement.objects.values('endorser').annotate(n=Count('pk')).order_by('-n').first())
    context.update({
        'categories': Skill.objects.annotate(n=Count('endorsements')).filter(n__gt=0).order_by('-n')[:8],
        'top_endorser': User.objects.filter(pk=top['endorser']).select_related('profile').first() if top else None,
        'top_endorser_count': top['n'] if top else 0,
        'total': Endorsement.objects.count(),
    })
    return render(request, 'base/endorsements.html', context)


def _worked_with(user, limit=6):
    """People who signed up for the same events as `user`, most shared events first."""
    events = EventRoleSlot.objects.filter(signups=user).values('event')
    return (User.objects.filter(commitments__event__in=events, is_active=True).exclude(pk=user.pk)
            .annotate(shared=Count('commitments__event', distinct=True)).order_by('-shared', 'first_name')
            .select_related('profile')[:limit])


@login_required
def endorse_people_search(request):
    query = request.GET.get('person_q', '').strip()
    people = User.objects.filter(is_active=True).exclude(pk=request.user.pk).select_related('profile')
    if query:
        people = people.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query)
                               | Q(username__icontains=query) | Q(profile__skills__name__icontains=query)).distinct()
        people = people.order_by('first_name')[:8]
    else:
        people = _worked_with(request.user)
    return render(request, 'base/partials/endorse_people.html', {'people': people, 'query': query})


@login_required
def give_endorsement(request):
    target = None
    username = request.POST.get('user') or request.GET.get('user')
    if username:
        target = User.objects.filter(username=username, is_active=True).exclude(pk=request.user.pk).select_related('profile').first()
    event = Event.objects.filter(pk=request.POST.get('event') or request.GET.get('event') or 0).first()

    if request.method == 'POST' and target:
        skill_ids = [int(s) for s in request.POST.getlist('skills') if s.isdigit()]
        text = request.POST.get('text', '').strip()
        if not skill_ids:
            messages.error(request, _('Choose at least one skill to endorse.'))
        else:
            skills = list(Skill.objects.filter(pk__in=skill_ids))
            Endorsement.give(request.user, target, skills, text=text, event=event)
            rating = request.POST.get('rating', '')
            if request.user.is_staff and rating.isdigit() and 1 <= int(rating) <= 5:
                AdminFeedback.objects.create(volunteer=target, author=request.user, rating=int(rating),
                                             note=request.POST.get('admin_note', '').strip())
            Notification.objects.create(
                user=target,
                message=_('%(name)s endorsed you!') % {'name': request.user.get_full_name() or request.user.username},
                link=request.build_absolute_uri(reverse('profile_view', args=[target.username])),
            )
            _notify_endorsed(request, target, skills, text)
            messages.success(request, _('Endorsement sent to %(name)s.') % {'name': target.get_full_name() or target.username})
            return redirect(_safe_next(request, fallback=reverse('give_endorsement')))

    already = set()
    target_skills = []
    if target:
        already = set(Endorsement.objects.filter(endorser=request.user, endorsed=target).values_list('skills', flat=True))
        target_skills = list(target.profile.skills.all())
        popular = Skill.objects.exclude(pk__in=[s.pk for s in target_skills]).annotate(
            n=Count('endorsements')).order_by('-n', 'name')[:max(0, 8 - len(target_skills))]
        target_skills += list(popular)

    return render(request, 'base/give_endorsement.html', {
        'target': target,
        'target_hours': target.profile.hours_contributed() if target else 0,
        'target_role': EventRoleSlot.objects.filter(signups=target).select_related('role').order_by('-start_time').first() if target else None,
        'skills': target_skills,
        'already': already,
        'event': event,
        'people': _worked_with(request.user),
        'feed': Endorsement.objects.select_related('endorser', 'endorser__profile', 'endorsed').prefetch_related('skills').order_by('-timestamp')[:4],
        'next': request.GET.get('next', ''),
    })


@login_required
@require_POST
def quick_endorse(request):
    """HTMX: one-click endorsement from a teammate card; returns the updated chip."""
    target = get_object_or_404(User, pk=request.POST.get('user_id'), is_active=True)
    skill = get_object_or_404(Skill, pk=request.POST.get('skill_id'))
    event = Event.objects.filter(pk=request.POST.get('event_id') or 0).first()
    if target != request.user:
        mine = Endorsement.objects.filter(endorser=request.user, endorsed=target)
        created = not mine.filter(skills=skill).exists()
        if created:
            # Chips clicked for the same teammate and event add up to one endorsement
            endorsement = mine.filter(event=event, text='').order_by('-timestamp').first()
            if endorsement:
                endorsement.skills.add(skill)
            else:
                Endorsement.give(request.user, target, [skill], event=event)
            Notification.objects.create(
                user=target,
                message=_('%(name)s endorsed you for %(skill)s!') % {
                    'name': request.user.get_full_name() or request.user.username, 'skill': skill.name},
                link=request.build_absolute_uri(reverse('profile_view', args=[target.username])),
            )
            _notify_endorsed(request, target, [skill])
    return render(request, 'base/partials/quick_endorse_chip.html', {
        'teammate': target, 'skill': skill, 'endorsed': True, 'event': event,
    })


# ---------------------------------------------------------------------------
# Volunteer resume
# ---------------------------------------------------------------------------

def _resume_experience(person, now):
    """Roles the volunteer has served in, newest first, with hours from completed shifts and the events worked."""
    roles = {}

    def entry(role):
        return roles.setdefault(role.pk, {'role': role, 'hours': 0, 'events': {}, 'first': None, 'last': None})

    def seen(item, when):
        item['first'] = min(item['first'], when) if item['first'] else when
        item['last'] = max(item['last'], when) if item['last'] else when

    shifts = (Shift.objects.filter(user=person, end_time__isnull=False)
              .select_related('role', 'event_role_slot__role', 'event_role_slot__event'))
    for shift in shifts:
        slot = shift.event_role_slot
        role = slot.role if slot else shift.role
        if not role:
            continue
        item = entry(role)
        item['hours'] += shift.duration()
        seen(item, shift.start_time)
        if slot:
            item['events'][slot.event_id] = slot.event

    # Past sign-ups count as experience even when no shift was clocked
    for slot in (EventRoleSlot.objects.filter(signups=person, event__end_date__lt=now)
                 .select_related('role', 'event')):
        item = entry(slot.role)
        item['events'][slot.event_id] = slot.event
        seen(item, slot.start_time)

    for role in person.profile.permanent_roles.prefetch_related('venue'):
        entry(role).update(role=role, permanent=True)

    experience = []
    for item in roles.values():
        events = sorted(item['events'].values(), key=lambda e: e.start_date, reverse=True)
        experience.append({
            **item,
            'hours': round(item['hours'], 1),
            'event_count': len(events),
            'events': events[:4],
            'more_events': max(0, len(events) - 4),
        })
    experience.sort(key=lambda i: (not i.get('permanent'), -(i['last'].timestamp() if i['last'] else 0)))
    return experience


def volunteer_resume(request, username):
    person = get_object_or_404(User.objects.select_related('profile'), username=username, is_active=True)
    profile = person.profile
    now = timezone.now()

    endorsement_counts = dict(Endorsement.objects.filter(endorsed=person)
                              .values_list('skills').annotate(n=Count('pk')))
    skills = [{'skill': s, 'count': endorsement_counts.get(s.pk, 0)} for s in profile.skills.all()]
    skills.sort(key=lambda s: -s['count'])

    training = []
    for completion in (TrainingModuleCompletion.objects.filter(user=person)
                       .select_related('training_module').order_by('-completed_at')):
        module = completion.training_module
        expires = completion.completed_at + timedelta(days=module.expires_after_days) if module.expires_after_days else None
        if not expires or expires > now:
            training.append({'name': module.title, 'date': completion.completed_at, 'expires': expires})

    certifications = [c for c in (person.certifications.filter(verified=True).select_related('certificate')
                                  .order_by('-issued_at')) if c.status == 'verified']

    profile_url = request.build_absolute_uri(reverse('profile_view', args=[person.username]))
    return render(request, 'base/volunteer_resume.html', {
        'person': person,
        'profile': profile,
        # Contact details stay off the public copy
        'show_contact': request.user == person or request.user.is_staff,
        'hours': profile.hours_contributed(),
        'events_count': EventRoleSlot.objects.filter(signups=person, event__end_date__lt=now).values('event').distinct().count(),
        'experience': _resume_experience(person, now),
        'skills': skills,
        'training': training,
        'certifications': certifications,
        'credentials_count': len(training) + len(certifications),
        'endorsements': (Endorsement.objects.filter(endorsed=person).exclude(text='')
                         .select_related('endorser', 'endorser__profile').prefetch_related('skills')
                         .order_by('-timestamp')[:2]),
        'profile_url': profile_url,
        'qr_svg': qr_svg(profile_url),
        'issued': now,
    })


# ---------------------------------------------------------------------------
# Impact record
# ---------------------------------------------------------------------------

def impact_record(request, username):
    person = get_object_or_404(User.objects.select_related('profile'), username=username, is_active=True)
    profile = person.profile
    now = timezone.now()
    top_skills = (Endorsement.objects.filter(endorsed=person).values('skills__name')
                  .annotate(n=Count('pk')).order_by('-n')[:3])
    record_url = request.build_absolute_uri(reverse('impact_record', args=[person.username]))
    return render(request, 'base/impact_record.html', {
        'person': person,
        'profile': profile,
        'hours': profile.hours_contributed(),
        'projects': EventRoleSlot.objects.filter(signups=person, event__end_date__lt=now).values('event').distinct().count(),
        'modules': TrainingModuleCompletion.objects.filter(user=person).count(),
        'top_skills': top_skills,
        'endorsement': Endorsement.objects.filter(endorsed=person).exclude(text='').select_related('endorser', 'endorser__profile').prefetch_related('skills').order_by('-timestamp').first(),
        'record_id': f'{profile.id_code}-{person.pk:05d}',
        'qr_svg': qr_svg(record_url),
        'record_url': record_url,
        'issued': now,
    })


@login_required
def open_tasks(request):
    """Open Bell Tower signed in as the user's linked account (made if needed), on a list
    when ``?list=<EventTaskList id>`` is given. Uses a one-time sign-in link, so nobody
    needs a Bell Tower password. Staff accounts in Bell Tower can't get links: they're
    sent to Bell Tower to sign in themselves."""
    from events.models import EventTaskList
    from . import belltower

    cfg = belltower.config()
    back = request.META.get('HTTP_REFERER') or reverse('home')
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = reverse('home')
    if not belltower.is_connected(cfg):
        messages.error(request, _('Task lists are not set up yet.'))
        return redirect(back)
    task_list = EventTaskList.objects.filter(pk=request.GET.get('list') or 0, belltower_url=cfg['url']).first()
    next_path = f'/lists/{task_list.belltower_id}/' if task_list else '/'
    try:
        username = belltower.linked_username(request.user)
        if not username:
            messages.error(request, _('Add an email address to your account to use task lists.'))
            return redirect('edit_profile')
        return redirect(belltower.login_link(username, next_path))
    except belltower.BellTowerError as exc:
        if exc.status == 403:  # a staff account in Bell Tower: sign in there directly
            return redirect(belltower.list_web_url(task_list.belltower_id, cfg) if task_list else cfg['url'] + '/')
        messages.error(request, _("Couldn't open your tasks right now: %(error)s") % {'error': exc})
        return redirect(back)
