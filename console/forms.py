import re
from datetime import timedelta

from django import forms
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from base import email_templates, triggers
from base.models import EmailTemplate, EmailTrigger, Level, OperatingHour, PointsRules, Profile, SiteSettings, Venue, VenueFeature, VenueNote
from education.models import ExternalCertificate, Quiz, QuizQuestion, TrainingLesson, TrainingModule
from events.models import Event, EventCategory
from jobs.models import Role

from .utils import LANGUAGE_CODES, translated_fields

DEFAULT_LANGUAGE = LANGUAGE_CODES[0]
DATETIME_FORMAT = '%Y-%m-%dT%H:%M'


class TranslatedFormMixin:
    """Makes the default-language copy of each translated field required and
    exposes ``translated_groups`` so templates can render language tabs."""

    translated = ()
    optional_translated = ()
    translation_key = ''

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.translated:
            if name in self.optional_translated:
                continue
            field = self.fields.get(f'{name}_{DEFAULT_LANGUAGE}')
            if field is not None:
                field.required = True

    def translated_groups(self):
        """[(lang_code, [bound fields for that language]), ...]"""
        return [
            (code, [self[f'{name}_{code}'] for name in self.translated if f'{name}_{code}' in self.fields])
            for code in LANGUAGE_CODES
        ]


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = 'datetime-local'

    def __init__(self, **kwargs):
        super().__init__(format=DATETIME_FORMAT, **kwargs)


class EventForm(TranslatedFormMixin, forms.ModelForm):
    translation_key = 'event'
    translated = ('title', 'description')

    class Meta:
        model = Event
        fields = translated_fields('title', 'description') + [
            'venue', 'location_en', 'report_to_location', 'start_date', 'end_date', 'image',
            'category', 'coordinators', 'attendees', 'featured', 'published',
        ]
        widgets = {
            'start_date': DateTimeLocalInput(),
            'end_date': DateTimeLocalInput(),
            'category': forms.CheckboxSelectMultiple,
            'coordinators': forms.CheckboxSelectMultiple,
        }
        labels = {
            'venue': _('Venue'),
            'location_en': _('Address'),
            'report_to_location': _('Check-in point'),
            'attendees': _('Expected attendees'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['venue'].queryset = Venue.objects.order_by('name')
        self.fields['venue'].empty_label = _('Somewhere else (type an address)')
        self.fields['venue'].help_text = ''
        self.fields['location_en'].help_text = ''
        self.fields['location_en'].widget.attrs['placeholder'] = _('Street address, city')
        self.fields['start_date'].input_formats = [DATETIME_FORMAT]
        self.fields['end_date'].input_formats = [DATETIME_FORMAT]
        self.fields['category'].queryset = EventCategory.objects.order_by('name')
        coordinator_ids = list(self.instance.coordinators.values_list('pk', flat=True)) if self.instance.pk else []
        self.fields['coordinators'].queryset = User.objects.filter(is_active=True).filter(
            Q(is_staff=True) | Q(pk__in=coordinator_ids)).order_by('first_name', 'username')
        self.fields['coordinators'].label_from_instance = lambda u: u.get_full_name() or u.username

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_date'), cleaned.get('end_date')
        if start and end and end <= start:
            self.add_error('end_date', _('The event must end after it starts.'))
        if not cleaned.get('venue') and not (cleaned.get('location_en') or '').strip():
            self.add_error('location_en', _('Choose a venue or type an address.'))
        return cleaned

    def save(self, commit=True):
        event = self.instance
        if event.venue_id:
            # The venue is the location now; don't keep a stale one-off address in any language.
            for code in LANGUAGE_CODES:
                setattr(event, f'location_{code}', '')
        if {'venue', 'location_en'} & set(self.changed_data):
            event.latitude = event.longitude = None  # re-derived in Event.save()
        return super().save(commit)


class RoleForm(TranslatedFormMixin, forms.ModelForm):
    translation_key = 'role'
    translated = ('name', 'description')

    class Meta:
        model = Role
        fields = translated_fields('name', 'description') + [
            'icon', 'permanent', 'regular_number_of_beneficiaries', 'points_weight', 'venue',
        ]
        labels = {
            'regular_number_of_beneficiaries': _('Beneficiaries served per hour'),
            'points_weight': _('Points weight'),
            'venue': _('Venues'),
        }
        help_texts = {
            'venue': _('Where staff in this position can check in. Leave all unticked to allow any venue.'),
            'points_weight': _("Multiplies the impact points for every hour in this role, e.g. 1.5. Leave empty to set it automatically from how long the role's training is."),
        }
        widgets = {'venue': forms.CheckboxSelectMultiple, 'points_weight': forms.NumberInput(attrs={'step': '0.1', 'min': '0.1', 'placeholder': _('Automatic')})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['venue'].queryset = Venue.objects.order_by('name')


class VenueForm(TranslatedFormMixin, forms.ModelForm):
    translation_key = 'venue'
    translated = ('name', 'description')
    optional_translated = ('description',)

    class Meta:
        model = Venue
        fields = translated_fields('name', 'description') + ['address', 'phone_number', 'capacity', 'photo', 'features']
        widgets = {
            'features': forms.CheckboxSelectMultiple,
            'capacity': forms.NumberInput(attrs={'min': 0, 'placeholder': _('People')}),
            'phone_number': forms.TextInput(attrs={'type': 'tel'}),
            'address': forms.TextInput(attrs={'placeholder': _('Street address, city')}),
        }
        widgets.update({f'description_{code}': forms.Textarea(attrs={'rows': 4}) for code in LANGUAGE_CODES})
        labels = {
            'phone_number': _('Phone'),
            'capacity': _('Capacity'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['address'].required = True


class VenueHoursForm(forms.Form):
    """One open/close pair per weekday. Leaving both times blank means closed that day."""

    def __init__(self, *args, venue=None, **kwargs):
        super().__init__(*args, **kwargs)
        existing = {h.day_of_week: h for h in venue.operating_hours.all()} if venue else {}
        for day, label in OperatingHour.DAY_CHOICES:
            hours = existing.get(day)
            self.fields[f'open_{day}'] = forms.TimeField(
                required=False, initial=hours and hours.open_time, widget=forms.TimeInput(format='%H:%M', attrs={
                    'type': 'time', 'class': 'c-input', 'aria-label': _('%(day)s opens') % {'day': label}}))
            self.fields[f'close_{day}'] = forms.TimeField(
                required=False, initial=hours and hours.close_time, widget=forms.TimeInput(format='%H:%M', attrs={
                    'type': 'time', 'class': 'c-input', 'aria-label': _('%(day)s closes') % {'day': label}}))
            self.fields[f'staff_{day}'] = forms.BooleanField(
                required=False, initial=bool(hours and hours.open_to_staff_outside_hours),
                widget=forms.CheckboxInput(attrs={'aria-label': _('%(day)s: open to staff outside hours') % {'day': label}}))

    def rows(self):
        return [
            {'day': day, 'label': label, 'open': self[f'open_{day}'], 'close': self[f'close_{day}'], 'staff': self[f'staff_{day}']}
            for day, label in OperatingHour.DAY_CHOICES
        ]

    def clean(self):
        cleaned = super().clean()
        for day, _label in OperatingHour.DAY_CHOICES:
            opens, closes = cleaned.get(f'open_{day}'), cleaned.get(f'close_{day}')
            if bool(opens) != bool(closes):
                self.add_error(f'close_{day}' if opens else f'open_{day}', _('Set both times, or neither for closed.'))
            elif opens and closes <= opens:
                self.add_error(f'close_{day}', _('Closing time must be after opening time.'))
        return cleaned

    def save(self, venue):
        for day, _label in OperatingHour.DAY_CHOICES:
            opens, closes = self.cleaned_data.get(f'open_{day}'), self.cleaned_data.get(f'close_{day}')
            if opens and closes:
                OperatingHour.objects.update_or_create(venue=venue, day_of_week=day, defaults={
                    'open_time': opens, 'close_time': closes,
                    'open_to_staff_outside_hours': self.cleaned_data[f'staff_{day}'],
                })
            else:
                OperatingHour.objects.filter(venue=venue, day_of_week=day).delete()


class VenueNoteForm(forms.ModelForm):
    EXPIRY_CHOICES = [
        (1, _('1 day')), (3, _('3 days')), (7, _('1 week')), (14, _('2 weeks')), (30, _('30 days')), (365, _('1 year')),
    ]
    expires_in_days = forms.TypedChoiceField(choices=EXPIRY_CHOICES, coerce=int, initial=7, label=_('Show for'),
                                             widget=forms.Select(attrs={'class': 'c-select'}))

    class Meta:
        model = VenueNote
        fields = ['text']
        widgets = {'text': forms.Textarea(attrs={'rows': 2, 'class': 'c-textarea',
                                                 'placeholder': _('e.g. Side entrance closed for repairs this week')})}
        labels = {'text': _('Note')}

    def save(self, commit=True):
        self.instance.expires_after = timedelta(days=self.cleaned_data['expires_in_days'])
        return super().save(commit)


class VenueFeatureForm(forms.ModelForm):
    # Rendered inside the venue form, so an empty box must not block saving the venue.
    # The add-feature endpoint still rejects a blank name server-side.
    use_required_attribute = False

    class Meta:
        model = VenueFeature
        fields = ['name', 'category']
        widgets = {
            # Enter adds the feature instead of submitting the surrounding venue form.
            'name': forms.TextInput(attrs={
                'class': 'c-input', 'placeholder': _('e.g. Piano'),
                'onkeydown': "if (event.key === 'Enter') { event.preventDefault(); this.closest('.venue-feature-add').querySelector('button').click(); }",
            }),
            'category': forms.Select(attrs={'class': 'c-select'}),
        }


class TrainingModuleForm(TranslatedFormMixin, forms.ModelForm):
    translation_key = 'module'
    translated = ('title', 'description')

    class Meta:
        model = TrainingModule
        fields = translated_fields('title', 'description') + [
            'icon', 'topic', 'photo', 'expires_after_days', 'complexity_level', 'published',
        ]
        widgets = {
            'complexity_level': forms.NumberInput(attrs={'min': 0, 'max': 4}),
        }
        labels = {
            'expires_after_days': _('Certificate valid for (days)'),
            'complexity_level': _('Complexity (0-4)'),
        }

    def clean_complexity_level(self):
        level = self.cleaned_data['complexity_level']
        if not 0 <= level <= 4:
            raise forms.ValidationError(_('Complexity must be between 0 and 4.'))
        return level


class LessonForm(TranslatedFormMixin, forms.ModelForm):
    translation_key = 'lesson'
    translated = ('title', 'content')

    class Meta:
        model = TrainingLesson
        fields = translated_fields('title', 'content') + ['video_url', 'video_length_minutes']
        labels = {'video_length_minutes': _('Video length (minutes)')}
        help_texts = {'video_length_minutes': _('Video length in minutes. Leave blank to detect it automatically from YouTube.')}
        widgets = {
            'video_url': forms.URLInput(attrs={'placeholder': 'https://www.youtube.com/watch?v=...'}),
            'video_length_minutes': forms.NumberInput(attrs={'placeholder': _('Minutes'), 'min': 0}),
        }


class QuizForm(TranslatedFormMixin, forms.ModelForm):
    translation_key = 'quiz'
    translated = ('title',)

    class Meta:
        model = Quiz
        fields = translated_fields('title') + ['percentage_to_pass']
        widgets = {'percentage_to_pass': forms.NumberInput(attrs={'min': 0, 'max': 100})}
        labels = {'percentage_to_pass': _('Passing score (%)')}


class QuizQuestionForm(TranslatedFormMixin, forms.ModelForm):
    translation_key = 'question'
    translated = ('question_text', 'option_a', 'option_b', 'option_c', 'option_d')

    class Meta:
        model = QuizQuestion
        fields = translated_fields('question_text', 'option_a', 'option_b', 'option_c', 'option_d') + [
            'is_true_false', 'correct_option',
        ]
        widgets = {
            'correct_option': forms.RadioSelect,
        }
        for code in LANGUAGE_CODES:
            widgets[f'question_text_{code}'] = forms.Textarea(attrs={'rows': 3})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Options A/B are only required for multiple choice; true/false fills them in.
        for letter in 'ab':
            self.fields[f'option_{letter}_{DEFAULT_LANGUAGE}'].required = False
        for letter in 'cd':
            self.fields[f'option_{letter}_{DEFAULT_LANGUAGE}'].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('is_true_false'):
            if cleaned.get('correct_option') not in ('A', 'B'):
                self.add_error('correct_option', _('Choose True or False as the correct answer.'))
            return cleaned
        options = {letter: cleaned.get(f'option_{letter.lower()}_{DEFAULT_LANGUAGE}') for letter in 'ABCD'}
        if not options['A'] or not options['B']:
            self.add_error(f'option_a_{DEFAULT_LANGUAGE}', _('Multiple choice questions need at least two options.'))
        correct = cleaned.get('correct_option')
        if correct and not options.get(correct):
            self.add_error('correct_option', _('The correct answer must be one of the filled-in options.'))
        return cleaned


class CertificateForm(forms.ModelForm):
    class Meta:
        model = ExternalCertificate
        fields = ['name', 'issuer', 'description', 'icon', 'expires_after_days']
        labels = {
            'name': _('Certificate title'),
            'issuer': _('Issuing authority'),
            'description': _('Public description'),
            'expires_after_days': _('Valid for (days)'),
        }
        help_texts = {'expires_after_days': _('Leave blank if the certificate never expires.')}


class VolunteerUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'is_active']
        labels = {'is_active': _('Account active')}

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_('That email address is already in use.'))
        return email


class VolunteerProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['phone', 'location', 'bio', 'avatar', 'admin_notes', 'permanent_roles']
        widgets = {
            'permanent_roles': forms.CheckboxSelectMultiple,
            'bio': forms.Textarea(attrs={'rows': 3}),
            'admin_notes': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['permanent_roles'].queryset = Role.objects.filter(permanent=True).order_by('name')


class NewVolunteerForm(forms.Form):
    first_name = forms.CharField(label=_('First name'), max_length=150)
    last_name = forms.CharField(label=_('Last name'), max_length=150, required=False)
    email = forms.EmailField(label=_('Email address'))
    password = forms.CharField(label=_('Temporary password'), min_length=8, widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_('An account with that email already exists.'))
        return email


class OrganizationForm(forms.ModelForm):
    COLOR_FIELDS = [
        'color_primary', 'color_primary_contrast', 'color_accent', 'color_accent_contrast',
        'color_bg_primary', 'color_bg_secondary', 'color_bg_tertiary',
        'color_text_primary', 'color_text_secondary', 'color_text_tertiary',
        'color_border', 'color_divider', 'color_success', 'color_warning', 'color_error',
        'dark_bg_primary', 'dark_bg_secondary', 'dark_bg_tertiary',
        'dark_text_primary', 'dark_text_secondary', 'dark_text_tertiary',
        'dark_border', 'dark_divider',
    ]
    OFFSET_FIELDS = [
        'color_primary_light_offset', 'color_primary_dark_offset',
        'color_accent_light_offset', 'color_accent_dark_offset',
    ]

    class Meta:
        model = SiteSettings
        fields = [
            'company_name', 'logo', 'contact_email', 'careers_page_url', 'terms_of_service',
            'max_skills_per_user', 'kiosk_idle_timeout_seconds',
        ] + [
            'color_primary', 'color_primary_contrast', 'color_primary_light_offset', 'color_primary_dark_offset',
            'color_accent', 'color_accent_contrast', 'color_accent_light_offset', 'color_accent_dark_offset',
            'color_bg_primary', 'color_bg_secondary', 'color_bg_tertiary',
            'color_text_primary', 'color_text_secondary', 'color_text_tertiary',
            'color_border', 'color_divider', 'color_success', 'color_warning', 'color_error',
            'dark_bg_primary', 'dark_bg_secondary', 'dark_bg_tertiary',
            'dark_text_primary', 'dark_text_secondary', 'dark_text_tertiary',
            'dark_border', 'dark_divider',
        ]
        widgets = {
            'terms_of_service': forms.Textarea(attrs={'rows': 8}),
            'kiosk_idle_timeout_seconds': forms.NumberInput(attrs={'min': 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.COLOR_FIELDS:
            self.fields[name].widget = forms.TextInput(attrs={'type': 'color'})
        for name in self.OFFSET_FIELDS:
            self.fields[name].widget = forms.NumberInput(attrs={'type': 'range', 'min': 0, 'max': 60})

    def clean(self):
        cleaned = super().clean()
        for name in self.COLOR_FIELDS:
            value = cleaned.get(name)
            if value and not re.fullmatch(r'#[0-9A-Fa-f]{6}', value):
                self.add_error(name, _('Enter a hex color like #00434d.'))
        return cleaned


class LevelForm(forms.ModelForm):
    class Meta:
        model = Level
        fields = ['name', 'min_points', 'benefits']
        labels = {
            'name': _('Level name'),
            'min_points': _('Minimum points'),
            'benefits': _('Benefits'),
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'c-input', 'placeholder': _('e.g. Community Champion')}),
            'min_points': forms.NumberInput(attrs={'class': 'c-input', 'min': 0, 'placeholder': '0'}),
            'benefits': forms.TextInput(attrs={'class': 'c-input', 'placeholder': _('Comma-separated, e.g. Priority sign-up, Free T-shirt')}),
        }


class BaseLevelFormSet(forms.BaseModelFormSet):
    def clean(self):
        super().clean()
        seen = set()
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE') or not form.has_changed() and form.instance.pk is None:
                continue
            points = form.cleaned_data.get('min_points')
            if points is None:
                continue
            if points < 0:
                form.add_error('min_points', _("Points can't be negative."))
            elif points in seen:
                form.add_error('min_points', _("Two levels can't start at the same number of points."))
            seen.add(points)


class PointsRulesForm(forms.ModelForm):
    class Meta:
        model = PointsRules
        fields = '__all__'
        labels = {
            'points_per_hour': _('Points per hour'),
            'max_role_weight': _('Top role weight'),
            'reach_cap_percent': _('Reach bonus cap (%)'),
            'walk_in_bonus_percent': _('Walk-in cover (%)'),
            'last_minute_bonus_percent': _('Last-minute sign-up (%)'),
            'last_minute_window_hours': _('Last-minute window (hours)'),
            'off_hours_bonus_percent': _('Early or late shift (%)'),
            'early_start_hour': _('Early: starts before (hour)'),
            'late_end_hour': _('Late: ends after (hour)'),
            'training_points_per_minute': _('Points per training minute'),
            'training_points_min': _('Minimum per module'),
            'training_points_cap': _('Maximum per module'),
            'endorsement_points': _('Points per endorsement'),
            'endorsement_monthly_cap': _('Endorsement awards per 30 days'),
        }
        help_texts = {
            'max_role_weight': _('Roles with the longest training get this multiplier; untrained roles get 1.0. A role can set its own weight instead.'),
            'reach_cap_percent': _('Serving more people per volunteer adds 10% per doubling, up to this cap.'),
            'walk_in_bonus_percent': _('Checking in at the kiosk to cover a slot they had not signed up for.'),
            'last_minute_bonus_percent': _('Signing up for an understaffed slot within the window before it starts.'),
            'early_start_hour': _('0-23, local time.'),
            'late_end_hour': _('0-23, local time. Shifts that run past midnight also count.'),
            'endorsement_points': _('Given to someone endorsed by a teammate from the same event, once per event.'),
        }

    def clean(self):
        cleaned = super().clean()
        for name in ('early_start_hour', 'late_end_hour'):
            value = cleaned.get(name)
            if value is not None and value > 23:
                self.add_error(name, _('Use an hour from 0 to 23.'))
        if cleaned.get('max_role_weight') is not None and cleaned['max_role_weight'] < 1:
            self.add_error('max_role_weight', _('Use 1.0 or more.'))
        return cleaned


LevelFormSet = forms.modelformset_factory(Level, form=LevelForm, formset=BaseLevelFormSet, extra=1, can_delete=True)


class BackendSettingsForm(forms.ModelForm):
    """Integration keys and URLs. Secrets are write-only: blank keeps the saved value."""
    SECRET_FIELDS = ('libretranslate_api_key', 'google_translate_api_key', 'deepl_api_key', 'listmonk_api_token')

    class Meta:
        model = SiteSettings
        fields = [
            'libretranslate_url', 'libretranslate_api_key', 'google_translate_api_key', 'deepl_api_key',
            'mymemory_email', 'listmonk_url', 'listmonk_api_user', 'listmonk_api_token', 'default_from_email',
        ]
        labels = {
            'libretranslate_url': _('LibreTranslate URL'),
            'libretranslate_api_key': _('LibreTranslate API key'),
            'google_translate_api_key': _('Google Cloud Translation key'),
            'deepl_api_key': _('DeepL API key'),
            'mymemory_email': _('MyMemory contact email'),
            'listmonk_url': _('listmonk URL'),
            'listmonk_api_user': _('API user'),
            'listmonk_api_token': _('API token'),
            'default_from_email': _('From address'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['listmonk_url'].widget.attrs['placeholder'] = 'https://lists.example.org'
        self.fields['default_from_email'].widget.attrs['placeholder'] = 'Town Hall <hello@example.org>'
        for name in self.SECRET_FIELDS:
            field = self.fields[name]
            field.widget = forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'})
            saved = getattr(self.instance, name)
            field.widget.attrs['placeholder'] = (
                _('Saved (ends in %(tail)s). Leave blank to keep.') % {'tail': saved[-4:]} if saved else _('Not set')
            )
        self.fields['libretranslate_url'].widget.attrs['placeholder'] = 'http://translate.internal:5000'

    def clean(self):
        cleaned = super().clean()
        for name in self.SECRET_FIELDS:
            if self.data.get(f'clear_{name}'):
                cleaned[name] = ''
            elif not cleaned.get(name):
                cleaned[name] = getattr(self.instance, name)  # blank keeps the saved secret
        return cleaned

    def save(self, commit=True):
        # construct_instance() skips fields that were absent from the POST and have a
        # default, which would ignore "Remove saved value"; apply secrets explicitly.
        for name in self.SECRET_FIELDS:
            setattr(self.instance, name, self.cleaned_data[name])
        return super().save(commit)

    def saved_secrets(self):
        return {name: bool(getattr(self.instance, name)) for name in self.SECRET_FIELDS}


class EmailSettingsForm(forms.Form):
    """Communication > Email: for each app event (base/triggers.py), whether it emails,
    which listmonk template it uses in each enabled language and, for the campaign
    event, which extra lists it goes to."""

    def __init__(self, *args, options=None, **kwargs):
        """``options``: {'lists', 'tx', 'campaign_visual': [(id, name)]} fetched from listmonk.
        Without it (listmonk unreachable) template IDs are plain number inputs and the
        extra-list picker is hidden."""
        super().__init__(*args, **kwargs)
        self.options = options
        self.languages = email_templates.languages()
        self.users_list_id = SiteSettings.get_settings().listmonk_list_id
        self.saved = dict(((r.trigger, r.language), r.template_id) for r in EmailTemplate.objects.all())
        self.template_names = {tid: name for kind in ('tx', 'campaign_visual') for tid, name in (options or {}).get(kind, [])}
        for trigger in triggers.TRIGGERS:
            enabled, auto_send, extra_lists = triggers.setting(trigger.key)
            self.fields[f'{trigger.key}_enabled'] = forms.BooleanField(
                required=False, initial=enabled, disabled=trigger.required,
                widget=forms.CheckboxInput(attrs={'aria-label': _('Send "%(label)s" emails') % {'label': trigger.label}}))
            for lang in self.languages:
                self.fields[f'{trigger.key}_{lang}'] = self._template_field(
                    'campaign_visual' if trigger.campaign else 'tx', self.saved.get((trigger.key, lang)), lang)
            if trigger.campaign:
                self.fields[f'{trigger.key}_auto_send'] = forms.BooleanField(required=False, initial=auto_send)
                if options is not None:
                    self.fields[f'{trigger.key}_lists'] = forms.TypedMultipleChoiceField(
                        choices=[(i, name) for i, name in options['lists'] if i != self.users_list_id],
                        coerce=int, required=False, initial=extra_lists, widget=forms.CheckboxSelectMultiple)

    def _template_field(self, kind, saved, lang):
        label = email_templates.language_name(lang)
        if self.options is None:
            return forms.IntegerField(
                required=False, min_value=1, initial=saved, label=label,
                widget=forms.NumberInput(attrs={'placeholder': _('listmonk template ID')}))
        choices = [('', _('Not set'))] + list(self.options[kind])
        if saved and saved not in dict(self.options[kind]):
            choices.append((saved, _('#%(id)s (missing in listmonk)') % {'id': saved}))
        return forms.TypedChoiceField(
            choices=choices, coerce=int, empty_value=None, required=False, initial=saved, label=label,
            widget=forms.Select(attrs={'class': 'c-select'}))

    def missing_count(self):
        """Event/language pairs with no template, or one that no longer exists in listmonk."""
        if self.options is None:
            return 0
        return sum(
            1 for trigger in triggers.TRIGGERS for lang in self.languages
            if self.saved.get((trigger.key, lang)) not in self.template_names
        )

    def groups(self):
        """[(group label, [row])] in display order, for the template."""
        grouped = {}
        for trigger in triggers.TRIGGERS:
            templates = []
            for lang in self.languages:
                saved = self.saved.get((trigger.key, lang))
                templates.append({
                    'field': self[f'{trigger.key}_{lang}'],
                    'name': self.template_names.get(saved, ''),
                    # Set, but deleted in listmonk since; unset rows are covered by the page banner.
                    'missing': self.options is not None and bool(saved) and saved not in self.template_names,
                })
            grouped.setdefault(trigger.group, []).append({
                'trigger': trigger,
                'enabled': self[f'{trigger.key}_enabled'],
                'templates': templates,
                'auto_send': self[f'{trigger.key}_auto_send'] if trigger.campaign else None,
                'lists': self[f'{trigger.key}_lists'] if f'{trigger.key}_lists' in self.fields else None,
            })
        return list(grouped.items())

    def save(self):
        for trigger in triggers.TRIGGERS:
            defaults = {
                'enabled': trigger.required or self.cleaned_data[f'{trigger.key}_enabled'],
                'auto_send': bool(self.cleaned_data.get(f'{trigger.key}_auto_send')),
            }
            if f'{trigger.key}_lists' in self.fields:
                defaults['extra_list_ids'] = self.cleaned_data[f'{trigger.key}_lists']
            EmailTrigger.objects.update_or_create(key=trigger.key, defaults=defaults)
            for lang in self.languages:
                template_id = self.cleaned_data[f'{trigger.key}_{lang}']
                if template_id:
                    EmailTemplate.objects.update_or_create(
                        trigger=trigger.key, language=lang, defaults={'template_id': template_id})
                else:
                    EmailTemplate.objects.filter(trigger=trigger.key, language=lang).delete()
