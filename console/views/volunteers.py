import csv
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.models import Group, User
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base.email import notify, send_welcome_email, text_to_html
from base import points
from base.models import AdminFeedback, Endorsement, Notification, PointsEntry, SiteSettings
from education.models import Skill, TrainingModule, TrainingModuleCompletion, UserCertification
from events.models import EventRoleSlot
from jobs.models import Shift

from ..decorators import staff_required
from ..forms import NewVolunteerForm, VolunteerProfileForm, VolunteerUserForm
from ..utils import activity_feed, ids_from, page_window, paginate, posted_ids

ACTIVE_WINDOW_DAYS = 90


def _recently_active_ids():
    """Users with a shift, a sign-up or a new account inside the activity window."""
    since = timezone.now() - timedelta(days=ACTIVE_WINDOW_DAYS)
    ids = set(Shift.objects.filter(start_time__gte=since).values_list('user_id', flat=True))
    ids |= set(EventRoleSlot.signups.through.objects.filter(
        eventroleslot__start_time__gte=since).values_list('user_id', flat=True))
    ids |= set(User.objects.filter(date_joined__gte=timezone.now() - timedelta(days=30)).values_list('pk', flat=True))
    return ids


def _hours_by_user(user_ids, since=None):
    shifts = Shift.objects.filter(user_id__in=user_ids, end_time__isnull=False)
    if since:
        shifts = shifts.filter(start_time__gte=since)
    totals = {}
    for shift in shifts:
        totals[shift.user_id] = totals.get(shift.user_id, 0) + shift.duration()
    return totals


def _volunteer_page(request):
    users = User.objects.filter(is_staff=False).select_related('profile').prefetch_related('profile__skills').order_by('first_name', 'last_name', 'username')
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if query:
        users = users.filter(
            Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(username__icontains=query)
            | Q(email__icontains=query) | Q(profile__skills__name__icontains=query) | Q(profile__id_code=query)
        ).distinct()
    recent = _recently_active_ids()
    if status == 'active':
        users = users.filter(is_active=True, pk__in=recent)
    elif status == 'inactive':
        users = users.filter(is_active=True).exclude(pk__in=recent)
    elif status == 'deactivated':
        users = users.filter(is_active=False)

    page = paginate(request, users, per_page=10)
    ids = [u.pk for u in page]
    totals = _hours_by_user(ids)
    weekly = _hours_by_user(ids, since=timezone.now() - timedelta(days=7))
    shift_counts = dict(
        Shift.objects.filter(user_id__in=ids).values('user_id').annotate(n=Count('pk')).values_list('user_id', 'n')
    )
    for user in page:
        user.status = 'deactivated' if not user.is_active else 'active' if user.pk in recent else 'inactive'
        user.total_hours = round(totals.get(user.pk, 0), 1)
        user.week_hours = round(weekly.get(user.pk, 0), 1)
        user.shift_count = shift_counts.get(user.pk, 0)
    return {'page': page, 'pages': page_window(page), 'query': query, 'status': status}


@staff_required
def volunteer_list(request):
    context = _volunteer_page(request)
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'console/partials/volunteer_table.html', context)

    volunteers = User.objects.filter(is_staff=False)
    total = volunteers.count()
    month_ago = timezone.now() - timedelta(days=30)
    active_month = len(
        set(Shift.objects.filter(start_time__gte=month_ago).values_list('user_id', flat=True))
        | set(TrainingModuleCompletion.objects.filter(completed_at__gte=month_ago).values_list('user_id', flat=True))
    )
    four_weeks = _hours_by_user(volunteers.values_list('pk', flat=True), since=timezone.now() - timedelta(days=28))
    avg_weekly = round(sum(four_weeks.values()) / len(four_weeks) / 4, 1) if four_weeks else 0
    context.update({
        'total_volunteers': total,
        'new_this_quarter': volunteers.filter(date_joined__gte=timezone.now() - timedelta(days=90)).count(),
        'active_month': active_month,
        'active_percent': round(active_month / total * 100) if total else 0,
        'avg_weekly_hours': avg_weekly,
        'top_skills': Skill.objects.annotate(n=Count('user_profiles')).filter(n__gt=0).order_by('-n')[:6],
        'new_form': NewVolunteerForm(),
    })
    return render(request, 'console/volunteer_list.html', context)


@staff_required
def export_volunteers(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="volunteers.csv"'
    writer = csv.writer(response)
    writer.writerow(['Name', 'Email', 'Phone', 'Location', 'Volunteer ID', 'Joined', 'Active', 'Impact points', 'Hours', 'Skills'])
    users = User.objects.filter(is_staff=False).select_related('profile').prefetch_related('profile__skills')
    totals = _hours_by_user(users.values_list('pk', flat=True))
    for user in users:
        profile = user.profile
        writer.writerow([
            user.get_full_name() or user.username, user.email, profile.phone, profile.location, profile.id_code,
            user.date_joined.date().isoformat(), 'yes' if user.is_active else 'no', profile.impact_points,
            round(totals.get(user.pk, 0), 1), ', '.join(s.name for s in profile.skills.all()),
        ])
    return response


@staff_required
@require_POST
def volunteer_create(request):
    form = NewVolunteerForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect('console_volunteers')
    data = form.cleaned_data
    username = data['email'][:150]
    if User.objects.filter(username=username).exists():
        username = f"{data['email'].split('@')[0][:140]}_{User.objects.count()}"
    user = User.objects.create_user(
        username=username, email=data['email'], password=data['password'],
        first_name=data['first_name'], last_name=data['last_name'],
    )
    send_welcome_email(request, user)
    messages.success(request, _('%(name)s was added. Their kiosk ID is %(code)s.') % {
        'name': user.get_full_name(), 'code': user.profile.id_code})
    return redirect('console_volunteer_edit', user_id=user.pk)


@staff_required
def volunteer_detail(request, user_id):
    volunteer = get_object_or_404(User.objects.select_related('profile'), pk=user_id)
    profile = volunteer.profile

    if request.method == 'POST':
        rating = request.POST.get('rating', '')
        if rating.isdigit() and 1 <= int(rating) <= 5:
            AdminFeedback.objects.create(volunteer=volunteer, author=request.user, rating=int(rating),
                                         note=request.POST.get('note', '').strip())
            messages.success(request, _('Private feedback saved.'))
        else:
            messages.error(request, _('Choose a rating from 1 to 5 stars.'))
        return redirect('console_volunteer_detail', user_id=volunteer.pk)

    now = timezone.now()
    completions = {c.training_module_id: c for c in volunteer.training_module_completions.all()}
    modules = TrainingModule.objects.filter(Q(started_by=volunteer) | Q(pk__in=completions.keys())).distinct()
    training = []
    for module in modules:
        completion = completions.get(module.pk)
        training.append({
            'module': module,
            'completion': completion,
            'progress': 100 if completion else module.completion_percentage_for_user(volunteer),
        })
    training.sort(key=lambda t: (t['completion'] is None, -(t['completion'].completed_at.timestamp() if t['completion'] else 0)))

    shifts = list(volunteer.shifts.filter(end_time__isnull=False).select_related('role', 'event_role_slot__event').order_by('-start_time'))
    show_all = request.GET.get('history') == 'all'

    endorsements = volunteer.endorsements.select_related('endorser', 'event').prefetch_related('skills').order_by('-timestamp')
    feedback = AdminFeedback.objects.filter(volunteer=volunteer).select_related('author')

    return render(request, 'console/volunteer_detail.html', {
        'volunteer': volunteer,
        'profile': profile,
        'total_hours': round(sum(s.duration() for s in shifts), 1),
        'completed_modules': len(completions),
        'tracked_modules': len(training),
        'events_attended': EventRoleSlot.objects.filter(signups=volunteer, event__end_date__lt=now).values('event').distinct().count(),
        'training': training,
        'history': shifts if show_all else shifts[:4],
        'history_more': max(0, len(shifts) - 4) if not show_all else 0,
        'certifications': UserCertification.objects.filter(user=volunteer).select_related('certificate'),
        'skill_counts': endorsements.values('skills__name').annotate(n=Count('pk')).order_by('-n')[:5],
        'endorsements': endorsements[:4],
        'endorsement_total': endorsements.count(),
        'feedback': feedback[:3],
        'average_rating': feedback.aggregate(avg=Avg('rating'))['avg'],
        'upcoming': EventRoleSlot.objects.filter(signups=volunteer, end_time__gte=now).select_related('event', 'role').order_by('start_time')[:3],
        'points_entries': (PointsEntry.objects.filter(user=volunteer)
                           .select_related('shift__role', 'shift__event_role_slot__event', 'training_module', 'created_by')
                           [:None if request.GET.get('points') == 'all' else 8]),
        'points_entry_total': PointsEntry.objects.filter(user=volunteer).count(),
        'can_manage_access': _can_manage_access(request.user, volunteer),
        'groups': Group.objects.order_by('name'),
        'volunteer_group_ids': set(volunteer.groups.values_list('pk', flat=True)),
    })


@staff_required
@require_POST
def adjust_points(request, user_id):
    volunteer = get_object_or_404(User, pk=user_id)
    reason = request.POST.get('reason', '').strip()[:200]
    try:
        amount = int(request.POST.get('amount', ''))
    except ValueError:
        amount = 0
    if not amount or not reason:
        messages.error(request, _('Enter a non-zero number of points and a reason.'))
    else:
        points.adjust(volunteer, amount, reason, request.user)
        messages.success(request, _('%(amount)+d points recorded for %(name)s.') % {
            'amount': amount, 'name': volunteer.get_full_name() or volunteer.username})
    return redirect(reverse('console_volunteer_detail', args=[volunteer.pk]) + '#points')


def _can_manage_access(actor, target):
    """Staff status and groups are changed by superusers or staff with Django's "change user"
    permission. Nobody edits their own access, and only superusers touch other superusers."""
    if actor == target or (target.is_superuser and not actor.is_superuser):
        return False
    return actor.is_superuser or actor.has_perm('auth.change_user')


@staff_required
@require_POST
def volunteer_access(request, user_id):
    volunteer = get_object_or_404(User, pk=user_id)
    if not _can_manage_access(request.user, volunteer):
        messages.error(request, _("You don't have permission to change this person's access."))
        return redirect('console_volunteer_detail', user_id=volunteer.pk)

    volunteer.is_staff = request.POST.get('is_staff') == 'on'
    if request.user.is_superuser:
        volunteer.is_superuser = request.POST.get('is_superuser') == 'on'
        volunteer.is_staff = volunteer.is_staff or volunteer.is_superuser
    with transaction.atomic():
        volunteer.save(update_fields=['is_staff', 'is_superuser'])
        volunteer.groups.set(Group.objects.filter(pk__in=posted_ids(request, 'group_ids')))
    messages.success(request, _('Access updated for %(name)s.') % {'name': volunteer.get_full_name() or volunteer.username})
    return redirect('console_volunteer_detail', user_id=volunteer.pk)


@staff_required
def volunteer_edit(request, user_id):
    volunteer = get_object_or_404(User.objects.select_related('profile'), pk=user_id)
    profile = volunteer.profile
    max_skills = SiteSettings.get_settings().max_skills_per_user

    if request.method == 'POST':
        user_form = VolunteerUserForm(request.POST, instance=volunteer, prefix='user')
        profile_form = VolunteerProfileForm(request.POST, request.FILES, instance=profile, prefix='profile')
        skill_ids = posted_ids(request, 'skill_ids')[:max_skills]
        if volunteer == request.user and not request.POST.get('user-is_active'):
            messages.error(request, _("You can't deactivate your own account here."))
        elif user_form.is_valid() and profile_form.is_valid():
            with transaction.atomic():
                user_form.save()
                profile_form.save()
                profile.skills.set(Skill.objects.filter(pk__in=skill_ids))
            messages.success(request, _('Profile changes saved.'))
            return redirect('console_volunteer_detail', user_id=volunteer.pk)
        else:
            messages.error(request, _('Please fix the highlighted fields.'))
        selected_skills = Skill.objects.filter(pk__in=skill_ids)
    else:
        user_form = VolunteerUserForm(instance=volunteer, prefix='user')
        profile_form = VolunteerProfileForm(instance=profile, prefix='profile')
        selected_skills = profile.skills.all()

    shifts = volunteer.shifts.filter(end_time__isnull=False)
    return render(request, 'console/volunteer_edit.html', {
        'volunteer': volunteer,
        'profile': profile,
        'user_form': user_form,
        'profile_form': profile_form,
        'selected_skills': selected_skills,
        'max_skills': max_skills,
        'total_hours': round(sum(s.duration() for s in shifts), 1),
        'events_led': volunteer.coordinated_events.count(),
        'recent': _recent_engagement(volunteer),
    })


def _recent_engagement(volunteer):
    items = []
    for slot in EventRoleSlot.objects.filter(signups=volunteer).select_related('event', 'role').order_by('-start_time')[:3]:
        items.append({'title': slot.event.title, 'detail': slot.role.name, 'time': slot.start_time, 'accent': False})
    for completion in volunteer.training_module_completions.select_related('training_module')[:3]:
        items.append({'title': completion.training_module.title, 'detail': _('Training completed'),
                      'time': completion.completed_at, 'accent': True})
    items.sort(key=lambda i: i['time'], reverse=True)
    return items[:4]


@staff_required
@require_POST
def message_volunteer(request, user_id):
    volunteer = get_object_or_404(User, pk=user_id)
    text = request.POST.get('message', '').strip()
    if text:
        link = request.POST.get('link') or None
        Notification.objects.create(user=volunteer, message=text[:255], link=link)
        notify('volunteer_message', volunteer, {'message': text, 'message_html': text_to_html(text), 'link': link or ''})
        messages.success(request, _('Message sent to %(name)s.') % {'name': volunteer.get_full_name() or volunteer.username})
    else:
        messages.error(request, _('Write a message before sending.'))
    return redirect('console_volunteer_detail', user_id=volunteer.pk)


@staff_required
@require_POST
def toggle_active(request, user_id):
    volunteer = get_object_or_404(User, pk=user_id)
    if volunteer == request.user:
        messages.error(request, _("You can't deactivate your own account."))
    else:
        volunteer.is_active = not volunteer.is_active
        volunteer.save(update_fields=['is_active'])
        if volunteer.is_active:
            messages.success(request, _('Account reactivated.'))
        else:
            messages.info(request, _('Account deactivated. They can no longer sign in.'))
    return redirect('console_volunteer_detail', user_id=volunteer.pk)


# ---------------------------------------------------------------------------
# Skill chip editor endpoints (shared by roles, modules and volunteers)
# ---------------------------------------------------------------------------

@staff_required
def skill_search(request):
    query = request.GET.get('skill_q', '').strip()
    exclude = ids_from(request.GET, 'skill_ids')
    skills = Skill.objects.exclude(pk__in=exclude)
    if query:
        skills = skills.filter(name__icontains=query).order_by('name')
    else:
        skills = skills.annotate(n=Count('user_profiles')).order_by('-n', 'name')
    return render(request, 'console/partials/skill_suggestions.html', {
        'skills': skills[:8],
        'query': query,
        'can_create': bool(query) and not Skill.objects.filter(name__iexact=query).exists(),
    })


@staff_required
def skill_chip(request):
    if request.GET.get('skill_id'):
        skill = get_object_or_404(Skill, pk=request.GET['skill_id'])
    else:
        name = request.GET.get('name', '').strip()[:50]
        if not name:
            return HttpResponseBadRequest()
        skill = Skill.objects.filter(name__iexact=name).first() or Skill.objects.create(name=name)
    return render(request, 'console/partials/skill_chip.html', {'skill': skill})
