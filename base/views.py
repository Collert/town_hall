from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from .models import SiteSettings, HeroSection
from events.models import Event
from .forms import SiteSettingsForm
from django.contrib.auth.models import User
from django.contrib.auth import logout, login, authenticate, update_session_auth_hash
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from education.models import Skill

def home(request):
    hero, _ = HeroSection.objects.get_or_create(pk=1)
    active_users = User.objects.filter(is_active=True).count()
    hours_contributed = 120000  # TODO: replace with real query
    projects_completed = 500    # TODO: replace with real query
    from django.utils import timezone
    featured_events = Event.objects.filter(featured=True, start_date__gte=timezone.now()).order_by('start_date')[:3]
    return render(request, 'base/home.html', {
        'hero': hero,
        'active_users': active_users,
        'hours_contributed': hours_contributed,
        'projects_completed': projects_completed,
        'featured_events': featured_events,
    })


def theme_settings_edit(request):
    """View to edit site theme colors."""
    settings = SiteSettings.get_settings()
    
    if request.method == 'POST':
        form = SiteSettingsForm(request.POST, instance=settings)
        if form.is_valid():
            form.save()
            messages.success(request, _('Theme colors updated successfully!'))
            return redirect('theme_settings_edit')
    else:
        form = SiteSettingsForm(instance=settings)
    
    # Group fields for the template
    field_groups = {
        _('Primary Colors'): ['color_primary', 'color_primary_dark', 'color_primary_light'],
        _('Accent Colors'): ['color_accent', 'color_accent_dark', 'color_accent_light'],
        _('Background Colors'): ['color_bg_primary', 'color_bg_secondary', 'color_bg_white', 'color_bg_dark'],
        _('Text Colors'): ['color_text_primary', 'color_text_secondary', 'color_text_light', 'color_text_white'],
        _('Border & Divider'): ['color_border', 'color_divider'],
        _('Status Colors'): ['color_success', 'color_warning', 'color_error'],
        _('Dark Mode - Backgrounds'): ['dark_bg_primary', 'dark_bg_secondary', 'dark_bg_tertiary'],
        _('Dark Mode - Text'): ['dark_text_primary', 'dark_text_secondary', 'dark_text_tertiary'],
        _('Dark Mode - Borders'): ['dark_border', 'dark_divider'],
    }
    
    return render(request, 'base/site_settings.html', {
        'form': form,
        'field_groups': field_groups,
    })

def read_notification(request, notification_id):
    notification = get_object_or_404(request.user.notifications, pk=notification_id)
    notification.read = True
    notification.save()
    return redirect(notification.link or 'home')

def clear_notifications(request):
    request.user.notifications.filter(read=False).update(read=True)
    return redirect(request.META.get('HTTP_REFERER', 'home'))

def download_ics(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    response = HttpResponse(event.generate_ics(), content_type='text/calendar')
    response['Content-Disposition'] = f'attachment; filename="event-{event.pk}.ics"'
    return response

def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        try:
            # Django's default auth uses username, so we look up the user by email first
            user_obj = User.objects.get(email=email)
            username = user_obj.username
        except User.DoesNotExist:
            username = None
            
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            messages.success(request, _('Successfully logged in.'))
            return redirect('home')
        else:
            messages.error(request, _('Invalid email or password.'))
            
    return render(request, 'base/login.html')

def signup_view(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '')
        last_name = request.POST.get('last_name', '')
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        if User.objects.filter(email=email).exists():
            messages.error(request, _('An account with that email already exists.'))
        elif not email or not password:
            messages.error(request, _('Email and password are required.'))
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
                last_name=last_name
            )
            login(request, user)
            messages.success(request, _('Account created successfully!'))
            return redirect('home')
            
    return render(request, 'base/signup.html')

def logout_view(request):
    logout(request)
    return redirect('home')


@login_required
def edit_profile(request):
    profile = request.user.profile
    settings = SiteSettings.get_settings()
    all_skills = Skill.objects.all().order_by('name')

    if request.method == 'POST':
        action = request.POST.get('action', 'save')

        if action == 'add_skill':
            skill_id = request.POST.get('skill_id')
            skill = get_object_or_404(Skill, pk=skill_id)
            current_count = profile.skills.count()
            if current_count >= settings.max_skills_per_user:
                return JsonResponse({'error': 'limit', 'max': settings.max_skills_per_user}, status=400)
            profile.skills.add(skill)
            return JsonResponse({'ok': True, 'id': skill.pk, 'name': skill.name})

        if action == 'remove_skill':
            skill_id = request.POST.get('skill_id')
            skill = get_object_or_404(Skill, pk=skill_id)
            profile.skills.remove(skill)
            return JsonResponse({'ok': True})

        # Main save
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        location = request.POST.get('location', '').strip()
        bio = request.POST.get('bio', '').strip()

        user = request.user
        if email and email != user.email:
            if User.objects.filter(email=email).exclude(pk=user.pk).exists():
                messages.error(request, _('That email address is already in use.'))
                return redirect('edit_profile')
            user.email = email

        user.first_name = first_name
        user.last_name = last_name
        user.save()

        profile.phone = phone
        profile.location = location
        profile.bio = bio

        if 'avatar' in request.FILES:
            profile.avatar = request.FILES['avatar']

        profile.save()
        messages.success(request, _('Profile updated successfully!'))
        return redirect('edit_profile')

    return render(request, 'base/edit_profile.html', {
        'profile': profile,
        'all_skills': all_skills,
        'user_skills': list(profile.skills.values('id', 'name')),
        'user_skill_ids': list(profile.skills.values_list('id', flat=True)),
        'max_skills': settings.max_skills_per_user,
    })


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
    from django.utils import timezone
    from datetime import timedelta
    from education.models import TrainingModuleCompletion
    from events.models import EventRoleSlot
    from jobs.models import Shift
    from .models import Endorsement, Level

    viewed_user = get_object_or_404(User, username=username, is_active=True)
    profile = viewed_user.profile
    now = timezone.now()

    # 5 latest endorsements for public feedback feed
    recent_endorsements = list(
        viewed_user.endorsements.select_related(
            'endorser', 'endorser__profile', 'skill'
        ).order_by('-timestamp')[:5]
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

    # Level & progress
    level = profile.level()
    next_level = Level.objects.filter(
        min_points__gt=profile.impact_points
    ).order_by('min_points').first()
    level_progress = 0
    if level and next_level:
        range_pts = next_level.min_points - level.min_points
        earned = profile.impact_points - level.min_points
        level_progress = min(100, round((earned / range_pts) * 100)) if range_pts > 0 else 100
    elif level and not next_level:
        level_progress = 100

    # Per-skill endorsement counts for endorsement summary rows
    skill_endorsement_counts = []
    for skill in profile.skills.all():
        count = viewed_user.endorsements.filter(skill=skill).count()
        skill_endorsement_counts.append({'skill': skill, 'count': count})

    # Verified skill IDs (endorsed at least once)
    verified_skill_ids = set(
        viewed_user.endorsements.values_list('skill_id', flat=True).distinct()
    )

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
    extra_endorsers = max(0, total_endorsers - len(recent_endorsers))

    return render(request, 'base/profile.html', {
        'viewed_user': viewed_user,
        'profile': profile,
        'recent_endorsements': recent_endorsements,
        'completions': completions,
        'activity_history': activity_history,
        'total_hours': total_hours,
        'projects_completed': projects_completed,
        'level': level,
        'next_level': next_level,
        'level_progress': level_progress,
        'is_own_profile': request.user == viewed_user,
        'verified_skill_ids': verified_skill_ids,
        'recent_endorsers': recent_endorsers,
        'extra_endorsers': extra_endorsers,
        'skill_endorsement_counts': skill_endorsement_counts,
    })


@login_required
def my_profile(request):
    return redirect('profile_view', username=request.user.username)
