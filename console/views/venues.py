from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base.models import Venue, VenueFeature, VenueNote

from ..decorators import staff_required
from ..forms import VenueFeatureForm, VenueForm, VenueHoursForm, VenueNoteForm
from ..utils import LANGUAGE_CODES, lang_field


def feature_groups():
    """[(category key, label, [features])] for the feature picker, in category order."""
    features = list(VenueFeature.objects.all())
    return [
        (key, label, [f for f in features if f.category == key])
        for key, label in VenueFeature.CATEGORY_CHOICES
    ]


@staff_required
def venue_list(request):
    now = timezone.now()
    venues = Venue.objects.annotate(
        upcoming_count=Count('events', filter=Q(events__end_date__gte=now), distinct=True),
        role_count=Count('roles', distinct=True),
    ).prefetch_related('features', 'operating_hours')

    query = request.GET.get('q', '').strip()
    feature = request.GET.get('feature', '')
    if query:
        venues = venues.filter(
            Q(name__icontains=query) | Q(address__icontains=query) | Q(description__icontains=query)
        ).distinct()
    if feature.isdigit():
        venues = venues.filter(features__pk=feature)

    context = {
        'venues': venues.order_by('name'),
        'query': query,
        'feature': feature,
        'features': VenueFeature.objects.all(),
        'total_venues': Venue.objects.count(),
    }
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'console/partials/venue_grid.html', context)
    return render(request, 'console/venue_list.html', context)


@staff_required
def venue_edit(request, venue_id=None):
    venue = get_object_or_404(Venue, pk=venue_id) if venue_id else None

    if request.method == 'POST':
        form = VenueForm(request.POST, request.FILES, instance=venue)
        hours_form = VenueHoursForm(request.POST, venue=venue)
        if form.is_valid() and hours_form.is_valid():
            with transaction.atomic():
                venue = form.save()
                hours_form.save(venue)
            messages.success(request, _('Venue "%(name)s" saved.') % {'name': venue.name})
            return redirect('console_venue_edit', venue_id=venue.pk)
        messages.error(request, _('Please fix the highlighted fields.'))
        selected = [int(pk) for pk in request.POST.getlist('features') if pk.isdigit()]
    else:
        form = VenueForm(instance=venue)
        hours_form = VenueHoursForm(venue=venue)
        selected = list(venue.features.values_list('pk', flat=True)) if venue else []

    context = {
        'form': form,
        'hours_form': hours_form,
        'venue': venue,
        'feature_groups': feature_groups(),
        'selected_features': selected,
        'feature_form': VenueFeatureForm(prefix='feature'),
    }
    if venue:
        context.update({
            'notes': venue.active_notes(),
            'note_form': VenueNoteForm(prefix='note'),
            'upcoming_events': venue.events.filter(end_date__gte=timezone.now()).order_by('start_date')[:5],
            'roles': venue.roles.order_by('name'),
        })
    return render(request, 'console/venue_edit.html', context)


@staff_required
@require_POST
def venue_delete(request, venue_id):
    venue = get_object_or_404(Venue, pk=venue_id)
    with transaction.atomic():
        # Events keep their location as a typed address instead of losing it.
        for event in venue.events.all():
            for code in LANGUAGE_CODES:
                name = getattr(venue, lang_field('name', code)) or venue.name
                setattr(event, lang_field('location', code), ', '.join(part for part in (name, venue.address) if part))
            event.venue = None
            event.save()
        venue.delete()
    messages.success(request, _('Venue deleted.'))
    return redirect('console_venues')


@staff_required
@require_POST
def venue_feature_add(request):
    """HTMX: create a custom feature and re-render the picker with it ticked."""
    selected = [int(pk) for pk in request.POST.getlist('features') if pk.isdigit()]
    form = VenueFeatureForm(request.POST, prefix='feature')
    if form.is_valid():
        name = form.cleaned_data['name'].strip()
        feature = VenueFeature.objects.filter(name__iexact=name).first()
        if not feature:
            feature = form.save(commit=False)
            feature.order = (VenueFeature.objects.order_by('-order').values_list('order', flat=True).first() or 0) + 1
            for code in LANGUAGE_CODES:  # same name everywhere until someone translates it
                setattr(feature, lang_field('name', code), getattr(feature, lang_field('name', code)) or name)
            feature.save()
            messages.success(request, _('Feature "%(name)s" added.') % {'name': feature.name})
        selected.append(feature.pk)
        form = VenueFeatureForm(prefix='feature')
    return render(request, 'console/partials/venue_features.html', {
        'feature_groups': feature_groups(),
        'selected_features': selected,
        'feature_form': form,
    })


@staff_required
@require_POST
def venue_note_add(request, venue_id):
    venue = get_object_or_404(Venue, pk=venue_id)
    form = VenueNoteForm(request.POST, instance=VenueNote(venue=venue), prefix='note')
    if form.is_valid():
        form.save()
        form = VenueNoteForm(prefix='note')
        messages.success(request, _('Note posted.'))
    return render(request, 'console/partials/venue_notes.html', {
        'venue': venue, 'notes': venue.active_notes(), 'note_form': form,
    })


@staff_required
@require_POST
def venue_note_delete(request, venue_id, note_id):
    venue = get_object_or_404(Venue, pk=venue_id)
    get_object_or_404(VenueNote, pk=note_id, venue=venue).delete()
    return render(request, 'console/partials/venue_notes.html', {
        'venue': venue, 'notes': venue.active_notes(), 'note_form': VenueNoteForm(prefix='note'),
    })


@staff_required
def venue_preview(request):
    """HTMX: summary card for the venue picked in the event editor."""
    venue_id = request.GET.get('venue', '')
    venue = Venue.objects.filter(pk=venue_id).prefetch_related('features').first() if venue_id.isdigit() else None
    return render(request, 'console/partials/venue_preview.html', {'venue': venue})
