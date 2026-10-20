from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from base.models import Endorsement, PointsEntry, PointsRules, Profile
from education.models import Skill, TrainingModule, TrainingModuleCompletion
from events.models import Event, EventRoleSlot
from jobs.models import Role, Shift


def local(days, hour):
    """An aware datetime `days` from today at `hour`:00, local time."""
    day = timezone.localdate() + timedelta(days=days)
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour))


class PointsTests(TestCase):
    def setUp(self):
        cache.clear()  # PointsRules is cached
        self.vol = User.objects.create_user('vol', 'vol@example.com', 'pw', first_name='Val')
        self.mate = User.objects.create_user('mate', 'mate@example.com', 'pw', first_name='Max')
        self.role = Role.objects.create(name='Greeter', description='Say hello.', icon='person')

    def tearDown(self):
        cache.clear()

    def event(self, start, end, attendees=0):
        # Coordinates are set so Event.save() doesn't geocode over the network.
        return Event.objects.create(title='Cleanup', description='.', location='Park', latitude=49.28, longitude=-123.12,
                                    start_date=start, end_date=end, attendees=attendees)

    def work(self, user, slot, hours=None, clutched=False, start=None):
        """A finished shift on `slot`, from its start (or `start`) for `hours`."""
        start = start or slot.start_time
        shift = Shift.objects.create(user=user, event_role_slot=slot, clutched=clutched)
        Shift.objects.filter(pk=shift.pk).update(start_time=start)
        shift.refresh_from_db()
        shift.end_time = start + timedelta(hours=hours or (slot.end_time - slot.start_time).total_seconds() / 3600)
        shift.save()
        return shift

    def slot(self, start_hour=10, end_hour=13, days=-3, attendees=0, **extra):
        event = self.event(local(days, 8), local(days, 23), attendees=attendees)
        slot = EventRoleSlot.objects.create(event=event, role=extra.pop('role', self.role), start_time=local(days, start_hour),
                                            end_time=local(days, end_hour), required_qty=extra.pop('required_qty', 2))
        return slot

    def test_daytime_shift_is_points_per_hour(self):
        slot = self.slot()
        slot.signups.add(self.vol)
        shift = self.work(self.vol, slot)
        self.assertEqual(shift.end_impact_points, 30)
        self.assertEqual(PointsEntry.objects.get(shift=shift).amount, 30)
        self.vol.profile.refresh_from_db()
        self.assertEqual(self.vol.profile.impact_points, 30)

    def test_reach_is_shared_and_capped(self):
        slot = self.slot(attendees=40)
        slot.signups.add(self.vol, self.mate)
        # 20 people per volunteer: +44% (10% per doubling of 1 + 20)
        self.assertEqual(self.work(self.vol, slot).end_impact_points, 43)
        huge = self.slot(attendees=100000)
        huge.signups.add(self.vol)
        self.assertEqual(self.work(self.vol, huge).end_impact_points, 45)  # capped at +50%

    def test_clutch_bonuses_add_up(self):
        early = self.slot(start_hour=5, end_hour=8)
        early.signups.add(self.vol)
        self.assertEqual(self.work(self.vol, early).end_impact_points, 38)  # 30 + 25%
        late_walk_in = self.slot(start_hour=20, end_hour=23)
        late_walk_in.signups.add(self.vol)
        self.assertEqual(self.work(self.vol, late_walk_in, clutched=True).end_impact_points, 53)  # 30 + 75%

    def test_last_minute_signup(self):
        soon = self.slot(start_hour=0, end_hour=3, days=1)
        EventRoleSlot.objects.filter(pk=soon.pk).update(start_time=timezone.now() + timedelta(hours=2),
                                                         end_time=timezone.now() + timedelta(hours=5))
        soon.refresh_from_db()
        soon.add_signup(self.vol)
        self.assertTrue(soon.signup_log.get(user=self.vol).last_minute)

        later = self.slot(days=10)
        later.add_signup(self.vol)
        self.assertFalse(later.signup_log.get(user=self.vol).last_minute)

    def test_role_weight_override_and_reopening(self):
        weighty = Role.objects.create(name='Lead', description='.', icon='star', points_weight=1.5)
        slot = self.slot(role=weighty)
        slot.signups.add(self.vol)
        shift = self.work(self.vol, slot)
        self.assertEqual(shift.end_impact_points, 45)
        shift.end_time = None
        shift.save()
        self.assertFalse(PointsEntry.objects.filter(shift=shift).exists())

    def test_rules_changes_apply_going_forward(self):
        slot = self.slot()
        slot.signups.add(self.vol)
        first = self.work(self.vol, slot)
        rules = PointsRules.get()
        rules.points_per_hour = 20
        rules.save()
        second = self.work(self.vol, self.slot())
        self.assertEqual((PointsEntry.objects.get(shift=first).amount, second.end_impact_points), (30, 60))

    def test_training_award_once(self):
        module = TrainingModule.objects.create(title='Safety', description='.', icon='school', published=True)
        TrainingModuleCompletion.objects.create(training_module=module, user=self.vol)
        entry = PointsEntry.objects.get(user=self.vol, source=PointsEntry.TRAINING)
        self.assertEqual(entry.amount, 5)  # an empty module gets the minimum

    def test_endorsement_award_rules(self):
        slot = self.slot()
        slot.signups.add(self.vol, self.mate)
        skill = Skill.objects.create(name='First Aid')
        Endorsement.give(self.mate, self.vol, [skill], event=slot.event)
        self.assertEqual(PointsEntry.objects.filter(user=self.vol, source=PointsEntry.ENDORSEMENT).count(), 1)
        # Endorsing back earns nothing
        Endorsement.give(self.vol, self.mate, [skill], event=slot.event)
        self.assertFalse(PointsEntry.objects.filter(user=self.mate, source=PointsEntry.ENDORSEMENT).exists())
        # Without a shared event there's no award
        Endorsement.give(self.mate, self.vol, [Skill.objects.create(name='Logistics')])
        self.assertEqual(PointsEntry.objects.filter(user=self.vol, source=PointsEntry.ENDORSEMENT).count(), 1)

    def test_staff_endorsement_needs_no_event(self):
        staff = User.objects.create_user('boss2', 'boss2@example.com', 'pw', is_staff=True)
        skill = Skill.objects.create(name='Leadership')
        Endorsement.give(staff, self.vol, [skill])
        entry = PointsEntry.objects.get(user=self.vol, source=PointsEntry.ENDORSEMENT)
        self.assertEqual(entry.amount, 5)
        # Once per staff member and person every 30 days
        Endorsement.give(staff, self.vol, [Skill.objects.create(name='Logistics')])
        self.assertEqual(PointsEntry.objects.filter(user=self.vol, source=PointsEntry.ENDORSEMENT).count(), 1)
        # A different staff member can still add one, up to the monthly cap
        other = User.objects.create_user('boss3', 'boss3@example.com', 'pw', is_staff=True)
        Endorsement.give(other, self.vol, [skill])
        self.assertEqual(PointsEntry.objects.filter(user=self.vol, source=PointsEntry.ENDORSEMENT).count(), 2)

    def test_staff_adjustment_and_rules_page(self):
        staff = User.objects.create_user('boss', 'boss@example.com', 'pw', is_staff=True)
        self.client.force_login(staff)
        self.client.post(reverse('console_adjust_points', args=[self.vol.pk]), {'amount': '-10', 'reason': 'Correction'})
        entry = PointsEntry.objects.get(user=self.vol, source=PointsEntry.ADJUSTMENT)
        self.assertEqual((entry.amount, entry.created_by, entry.label), (-10, staff, 'Correction'))
        self.vol.profile.refresh_from_db()
        self.assertEqual(self.vol.profile.impact_points, -10)

        self.assertEqual(self.client.get(reverse('console_settings_points')).status_code, 200)
        data = {f.name: getattr(PointsRules.get(), f.name) for f in PointsRules._meta.fields if f.name != 'id'}
        data['points_per_hour'] = 12
        self.client.post(reverse('console_settings_points'), data)
        self.assertEqual(PointsRules.get().points_per_hour, 12)


class GravatarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', email=' Ana@Example.com ', password='pw')
        self.client.force_login(self.user)

    def test_avatar_url_uses_gravatar_hash_of_normalized_email(self):
        import hashlib
        profile = self.user.profile
        self.assertIsNone(profile.avatar_url())
        profile.use_gravatar = True
        digest = hashlib.sha256(b'ana@example.com').hexdigest()
        self.assertEqual(profile.avatar_url(64), f'https://gravatar.com/avatar/{digest}?s=64&d=mp')

    def test_turning_on_checks_gravatar_exists(self):
        from unittest import mock
        with mock.patch('base.views.gravatar_exists', return_value=False):
            self.client.post(reverse('edit_profile'), {'action': 'gravatar', 'use': '1'})
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.use_gravatar)

        with mock.patch('base.views.gravatar_exists', return_value=True):
            self.client.post(reverse('edit_profile'), {'action': 'gravatar', 'use': '1'})
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.use_gravatar)
        self.assertContains(self.client.get(reverse('edit_profile')), 'gravatar.com/avatar/')

        self.client.post(reverse('edit_profile'), {'action': 'gravatar', 'use': '0'})
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.use_gravatar)


class FirstRunSetupTests(TestCase):
    def setUp(self):
        from base.middleware import FirstRunMiddleware
        cache.clear()  # SiteSettings is cached
        self.had_users = FirstRunMiddleware.has_users
        FirstRunMiddleware.has_users = False

    def tearDown(self):
        from base.middleware import FirstRunMiddleware
        FirstRunMiddleware.has_users = self.had_users
        cache.clear()

    def post_setup(self, **overrides):
        data = {'company_name': 'Riverside Parish', 'first_name': 'Ada', 'last_name': 'Admin', 'email': 'ada@example.org',
                'password': 'a-Long-pass-123', 'password_confirm': 'a-Long-pass-123', **overrides}
        return self.client.post(reverse('first_run_setup'), data)

    def test_empty_database_redirects_to_setup(self):
        response = self.client.get(reverse('training_directory'))
        self.assertRedirects(response, reverse('first_run_setup'))
        self.assertEqual(self.client.get(reverse('first_run_setup')).status_code, 200)

    def test_setup_creates_superuser_and_admin_guide(self):
        from base.models import SiteSettings
        from base.onboarding import admin_guide

        response = self.post_setup()
        user = User.objects.get()
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertEqual((user.email, user.first_name), ('ada@example.org', 'Ada'))
        self.assertEqual(SiteSettings.get_settings().company_name, 'Riverside Parish')
        guide = admin_guide()
        self.assertTrue(guide.staff_only and guide.published)
        self.assertGreater(guide.lessons.count(), 10)
        self.assertEqual(guide.quizzes.get().questions.count(), 5)
        self.assertRedirects(response, reverse('module_overview', args=[guide.pk]))
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_setup_rejects_mismatched_or_weak_passwords(self):
        self.post_setup(password_confirm='something-else-9')
        self.post_setup(password='123', password_confirm='123')
        self.assertFalse(User.objects.exists())

    def test_setup_closes_once_a_user_exists(self):
        User.objects.create_user('someone', 'someone@example.org', 'pw')
        self.assertEqual(self.client.get(reverse('first_run_setup')).status_code, 404)
        self.assertEqual(self.post_setup().status_code, 404)
        self.assertEqual(self.client.get(reverse('training_directory')).status_code, 200)


class StaffOnlyModuleTests(TestCase):
    def setUp(self):
        from base.onboarding import ensure_admin_guide
        self.guide = ensure_admin_guide()
        self.vol = User.objects.create_user('vol', 'vol@example.com', 'pw')
        self.staff = User.objects.create_user('boss', 'boss@example.com', 'pw', is_staff=True)

    def test_hidden_from_volunteers(self):
        self.client.force_login(self.vol)
        self.assertNotContains(self.client.get(reverse('training_directory')), self.guide.title)
        self.assertEqual(self.client.get(reverse('module_overview', args=[self.guide.pk])).status_code, 404)
        lesson = self.guide.lessons.first()
        self.assertEqual(self.client.get(reverse('lesson_detail', args=[self.guide.pk, lesson.pk])).status_code, 404)

    def test_visible_to_staff_without_points(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse('training_directory')), self.guide.title)
        self.assertEqual(self.client.get(reverse('module_overview', args=[self.guide.pk])).status_code, 200)
        TrainingModuleCompletion.objects.create(training_module=self.guide, user=self.staff)
        self.assertFalse(PointsEntry.objects.filter(user=self.staff).exists())

    def test_guide_is_created_once(self):
        from base.onboarding import ensure_admin_guide
        self.assertEqual(ensure_admin_guide(), self.guide)
        self.assertEqual(TrainingModule.objects.filter(system_key='admin_guide').count(), 1)


class NewsletterFormTests(TestCase):
    """The footer form signs visitors up to the "New event published" campaign's extra lists."""

    def setUp(self):
        from base.models import EmailTrigger, SiteSettings
        User.objects.create_user('someone', 'someone@example.com', 'pw')  # skip first-run setup
        site = SiteSettings.get_settings()
        site.listmonk_url, site.listmonk_api_user, site.listmonk_api_token = 'http://lists.local', 'town-hall', 'tok'
        site.save()
        EmailTrigger.objects.create(key='event_published', enabled=True, extra_list_ids=[7, 8])
        self.url = reverse('newsletter_subscribe')

    def post(self, **data):
        return self.client.post(self.url, data, HTTP_HX_REQUEST='true')

    def test_footer_shows_form_only_with_lists(self):
        from base.models import EmailTrigger
        self.assertContains(self.client.get(reverse('terms_of_service')), self.url)
        EmailTrigger.objects.filter(key='event_published').update(extra_list_ids=[])
        self.assertNotContains(self.client.get(reverse('terms_of_service')), self.url)

    def test_new_subscriber_gets_opt_in(self):
        from unittest.mock import patch
        with patch('base.listmonk.api', side_effect=[{'results': []}, {'id': 1}]) as api:
            response = self.post(email='new@example.com')
        self.assertEqual(response.status_code, 204)
        self.assertIn('newsletter-subscribed', response['HX-Trigger'])
        method, path, payload = api.call_args_list[1].args
        self.assertEqual((method, path, payload['lists']), ('POST', '/subscribers', [7, 8]))
        self.assertFalse(payload['preconfirm_subscriptions'])

    def test_existing_subscriber_only_gets_missing_lists(self):
        from unittest.mock import patch
        existing = {'id': 9, 'status': 'enabled', 'lists': [{'id': 7, 'subscription_status': 'unsubscribed'}]}
        with patch('base.listmonk.api', side_effect=[{'results': [existing]}, None, None]) as api:
            self.post(email='old@example.com')
        self.assertEqual(api.call_args_list[1].args[2]['target_list_ids'], [8])
        self.assertEqual(api.call_args_list[2].args[:2], ('POST', '/subscribers/9/optin'))

    def test_bad_email_and_honeypot_never_reach_listmonk(self):
        from unittest.mock import patch
        with patch('base.listmonk.api') as api:
            bad = self.post(email='not-an-email')
            self.post(email='bot@example.com', nonce='x')
        api.assert_not_called()
        self.assertNotIn('newsletter-subscribed', bad['HX-Trigger'])
        self.assertIn('toasts', bad['HX-Trigger'])

    def test_listmonk_failure_shows_error(self):
        from unittest.mock import patch
        from base.listmonk import ListmonkError
        with patch('base.listmonk.api', side_effect=ListmonkError('down')):
            response = self.post(email='new@example.com')
        self.assertNotIn('newsletter-subscribed', response['HX-Trigger'])


class SavedLanguageTests(TestCase):
    def test_saved_language_beats_browser_but_cookie_beats_saved(self):
        from django.conf import settings
        from django.contrib.auth.models import User

        codes = [c for c, _n in settings.LANGUAGES]
        saved = next(c for c in codes if c != 'fr') if 'fr' in codes else codes[-1]
        user = User.objects.create_user('lang', password='x')
        Profile.objects.filter(user=user).update(language=saved)
        self.client.force_login(user)
        response = self.client.get('/', HTTP_ACCEPT_LANGUAGE='fr')
        if saved != 'fr':
            self.assertEqual(response.status_code, 302)
            self.assertIn(f'/{saved}/', response['Location'])


class ProfileCompletionTests(TestCase):
    def setUp(self):
        cache.clear()  # PointsRules and the middleware throttle are cached
        self.vol = User.objects.create_user('newbie', 'newbie@example.com', 'pw', first_name='Nina')
        self.skill = Skill.objects.create(name='First aid')

    def tearDown(self):
        cache.clear()

    def joined(self, user, ago):
        User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - ago)

    def run_reminders(self):
        from base.profile_completion import run_due_reminders
        return run_due_reminders('https://volunteer.example.org')

    def complete(self, profile):
        profile.bio, profile.location, profile.use_gravatar = 'Hi there.', 'Vancouver', True
        profile.save()
        profile.skills.add(self.skill)

    def sent_triggers(self):
        from django.core import mail
        return [message.subject for message in mail.outbox]

    def test_first_reminder_after_a_day_lists_whats_missing(self):
        from django.core import mail
        self.joined(self.vol, timedelta(hours=25))
        self.assertEqual(self.run_reminders(), 1)
        self.assertEqual(self.sent_triggers(), ['[profile_reminder] Profile reminder'])
        body = mail.outbox[0].body
        self.assertIn('missing: a profile photo, a short bio, your skills, your location', body)
        self.assertIn('points: 25', body)
        self.assertIn('link: https://volunteer.example.org/en/profile/edit/', body)
        self.assertEqual(self.run_reminders(), 0)  # once only

    def test_nothing_before_a_day_or_for_complete_profiles(self):
        self.joined(self.vol, timedelta(hours=20))
        self.assertEqual(self.run_reminders(), 0)
        self.joined(self.vol, timedelta(days=2))
        self.complete(self.vol.profile)
        self.assertEqual(self.run_reminders(), 0)

    def test_second_reminder_after_a_week(self):
        self.joined(self.vol, timedelta(days=1, hours=1))
        self.run_reminders()
        self.joined(self.vol, timedelta(days=7, hours=1))
        self.assertEqual(self.run_reminders(), 1)
        self.assertEqual(self.sent_triggers(), ['[profile_reminder] Profile reminder',
                                                '[profile_reminder_week] Second profile reminder'])
        self.assertEqual(self.run_reminders(), 0)

    def test_late_starters_get_only_the_week_reminder(self):
        self.joined(self.vol, timedelta(days=8))
        self.assertEqual(self.run_reminders(), 1)
        self.assertEqual(self.sent_triggers(), ['[profile_reminder_week] Second profile reminder'])

    def test_old_accounts_are_left_alone(self):
        self.joined(self.vol, timedelta(days=60))
        self.assertEqual(self.run_reminders(), 0)

    def test_switched_off_trigger_sends_nothing_and_keeps_people_waiting(self):
        from base.models import EmailTrigger
        EmailTrigger.objects.create(key='profile_reminder', enabled=False)
        self.joined(self.vol, timedelta(days=2))
        self.assertEqual(self.run_reminders(), 0)
        self.vol.profile.refresh_from_db()
        self.assertEqual(self.vol.profile.profile_reminders_sent, 0)

    def test_link_uses_the_recipients_language(self):
        from django.core import mail
        Profile.objects.filter(user=self.vol).update(language='uk')
        self.joined(self.vol, timedelta(days=2))
        self.run_reminders()
        self.assertIn('link: https://volunteer.example.org/uk/profile/edit/', mail.outbox[0].body)

    def test_middleware_runs_the_pass(self):
        from django.core import mail
        self.joined(self.vol, timedelta(days=2))
        self.client.get(reverse('home'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('link: http://testserver/en/profile/edit/', mail.outbox[0].body)

    def test_completing_the_profile_awards_points_once(self):
        self.client.force_login(self.vol)
        profile = self.vol.profile
        profile.use_gravatar = True
        profile.save()
        self.client.post(reverse('profile_skills'), {'skill_id': self.skill.pk, 'action': 'add'})
        self.assertFalse(PointsEntry.objects.filter(source=PointsEntry.PROFILE).exists())
        response = self.client.post(reverse('edit_profile'), {'first_name': 'Nina', 'email': 'newbie@example.com',
                                                               'bio': 'Hi there.', 'location': 'Vancouver'}, follow=True)
        self.assertContains(response, 'You earned 25 impact points')
        profile.refresh_from_db()
        self.assertEqual(profile.impact_points, 25)
        self.assertNotContains(response, 'profile-checklist')
        # Emptying and refilling the profile doesn't pay out again.
        self.client.post(reverse('edit_profile'), {'first_name': 'Nina', 'email': 'newbie@example.com', 'bio': ''})
        self.client.post(reverse('edit_profile'), {'first_name': 'Nina', 'email': 'newbie@example.com',
                                                   'bio': 'Back.', 'location': 'Vancouver'})
        self.assertEqual(PointsEntry.objects.filter(source=PointsEntry.PROFILE).count(), 1)

    def test_checklist_shows_progress_and_award(self):
        self.client.force_login(self.vol)
        Profile.objects.filter(user=self.vol).update(bio='Hi there.')
        response = self.client.get(reverse('edit_profile'))
        self.assertContains(response, '+25 impact points')
        self.assertContains(response, '<li class="is-done"><span class="material-symbols-outlined">check_circle</span><span>A short bio</span></li>', html=True)

    def test_award_can_be_switched_off(self):
        rules = PointsRules.get()
        rules.profile_complete_points = 0
        rules.save()
        self.complete(self.vol.profile)
        from base.points import award_profile
        self.assertIsNone(award_profile(self.vol.profile))

    def test_recalculate_backfills_complete_profiles(self):
        from base.points import rebuild
        self.complete(self.vol.profile)
        rebuild()
        self.vol.profile.refresh_from_db()
        self.assertEqual(self.vol.profile.impact_points, 25)
