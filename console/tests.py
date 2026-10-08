import os
import re
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from base.models import (
    AdminFeedback, EmailTemplate, EmailTrigger, Endorsement, HeroSection, Level, Notification, OperatingHour, Profile, SiteSettings, Venue,
    VenueFeature, VenueNote,
)
from education.models import (
    ExternalCertificate, Quiz, QuizQuestion, Skill, TrainingLesson, TrainingModule,
    TrainingModuleCompletion, UserCertification, UserCertificationFile,
)
from events.models import Event, EventFeedback, EventRoleSlot, EventSlotInvite, EventTaskList, SlotSignup
from jobs.models import Role, RoleTrainingRequirement, Shift

MEDIA_ROOT = tempfile.mkdtemp()
HTMX = {'HTTP_HX_REQUEST': 'true'}


def make_event(title, start, end, **extra):
    # Coordinates are set so Event.save() doesn't geocode over the network.
    return Event.objects.create(
        title=title, description='Bring your energy.', location='Town Hall',
        latitude=49.28, longitude=-123.12, start_date=start, end_date=end, **extra,
    )


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ConsoleFixture(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.staff = User.objects.create_user('admin', 'admin@example.com', 'pw-admin-123', first_name='Ada', is_staff=True)
        cls.vol = User.objects.create_user('vol', 'vol@example.com', 'pw-vol-123', first_name='Val', last_name='Volunteer')
        cls.mate = User.objects.create_user('mate', 'mate@example.com', 'pw-mate-123', first_name='Max')

        cls.skill = Skill.objects.create(name='First Aid')
        cls.vol.profile.skills.add(cls.skill)
        cls.mate.profile.skills.add(cls.skill)

        cls.module = TrainingModule.objects.create(title='Safety 101', description='Stay safe.', icon='health_and_safety', published=True)
        cls.lesson = TrainingLesson.objects.create(training_module=cls.module, title='Exits', content='Know your **exits**.', order=1)
        cls.quiz = Quiz.objects.create(training_module=cls.module, title='Check', order=1)
        cls.question = QuizQuestion.objects.create(question_text='Where is the exit?', option_a='Door', option_b='Wall', correct_option='A')
        cls.quiz.questions.add(cls.question)

        cls.role = Role.objects.create(name='Greeter', description='Say hello.', icon='person')
        cls.role.preferred_skills.add(cls.skill)
        RoleTrainingRequirement.objects.create(role=cls.role, training_module=cls.module, mandatory=True)
        TrainingModuleCompletion.objects.create(training_module=cls.module, user=cls.vol)
        TrainingModuleCompletion.objects.create(training_module=cls.module, user=cls.mate)

        cls.live = make_event('Fall Festival', now - timedelta(hours=1), now + timedelta(hours=5), attendees=50)
        cls.live.coordinators.add(cls.staff)
        cls.slot = EventRoleSlot.objects.create(event=cls.live, role=cls.role, start_time=now - timedelta(minutes=30),
                                                end_time=now + timedelta(hours=3), required_qty=3)
        cls.slot.signups.add(cls.vol, cls.mate)

        cls.past = make_event('Spring Cleanup', now - timedelta(days=10), now - timedelta(days=10) + timedelta(hours=6),
                              post_event_statement='We cleaned the river.')
        cls.past_slot = EventRoleSlot.objects.create(event=cls.past, role=cls.role, start_time=cls.past.start_date,
                                                     end_time=cls.past.start_date + timedelta(hours=4), required_qty=2)
        cls.past_slot.signups.add(cls.vol, cls.mate)
        shift = Shift.objects.create(user=cls.vol, event_role_slot=cls.past_slot)
        Shift.objects.filter(pk=shift.pk).update(start_time=cls.past.start_date)
        shift.refresh_from_db()
        shift.end_time = cls.past.start_date + timedelta(hours=3)
        shift.save()
        EventFeedback.objects.create(event=cls.past, user=cls.vol, rating=5, enjoyed='The people')
        Endorsement.give(cls.mate, cls.vol, [cls.skill], event=cls.past, text='Great work!')

        cls.venue = Venue.objects.create(name='Library', address='1 Main St', latitude=49.27, longitude=-123.1)
        OperatingHour.objects.create(venue=cls.venue, day_of_week=0, open_time='09:00', close_time='17:00')
        cls.perm_role = Role.objects.create(name='Librarian Assistant', icon='menu_book', permanent=True, regular_number_of_beneficiaries=10)
        cls.perm_role.venue.add(cls.venue)
        cls.vol.profile.permanent_roles.add(cls.perm_role)

        cls.cert = ExternalCertificate.objects.create(name='CPR', issuer='Red Cross', description='CPR training.', docs_list='ID, Card', expires_after_days=730)
        cls.user_cert = UserCertification.objects.create(user=cls.vol, certificate=cls.cert)
        UserCertificationFile.objects.create(certification=cls.user_cert, file=SimpleUploadedFile('card.pdf', b'%PDF-1.4'))

        cls.invite = EventSlotInvite.objects.create(event_role_slot=cls.slot, user=cls.staff)


class ConsolePageTests(ConsoleFixture):
    def setUp(self):
        self.client.force_login(self.staff)

    def test_console_pages_render(self):
        pages = [
            ('console_dashboard', []),
            ('console_settings_home', []),
            ('console_events', []),
            ('console_event_history', []),
            ('console_event_create', []),
            ('console_event_edit', [self.live.pk]),
            ('console_event_roles', [self.live.pk]),
            ('console_add_role_slots', [self.live.pk]),
            ('console_slot_row', [self.live.pk]),
            ('console_event_invite', [self.live.pk]),
            ('console_event_monitor', [self.live.pk]),
            ('console_monitor_activity', [self.live.pk]),
            ('console_export_shift_log', [self.live.pk]),
            ('console_event_report', [self.past.pk]),
            ('console_event_report', [self.live.pk]),
            ('console_roles', []),
            ('console_role_create', []),
            ('console_role_edit', [self.role.pk]),
            ('console_role_module_search', []),
            ('console_venues', []),
            ('console_venue_create', []),
            ('console_venue_edit', [self.venue.pk]),
            ('console_venue_preview', []),
            ('console_volunteers', []),
            ('console_export_volunteers', []),
            ('console_volunteer_detail', [self.vol.pk]),
            ('console_volunteer_edit', [self.vol.pk]),
            ('console_skill_search', []),
            ('console_modules', []),
            ('console_module_create', []),
            ('console_module_edit', [self.module.pk]),
            ('console_lesson_create', [self.module.pk]),
            ('console_lesson_edit', [self.module.pk, self.lesson.pk]),
            ('console_quiz_edit', [self.module.pk, self.quiz.pk]),
            ('console_question_edit', [self.module.pk, self.quiz.pk, self.question.pk]),
            ('console_certificates', []),
            ('console_certificate_create', []),
            ('console_certificate_edit', [self.cert.pk]),
            ('console_doc_row', []),
            ('console_verifications', []),
            ('console_verification_review', [self.user_cert.pk]),
            ('console_settings', []),
            ('console_settings_levels', []),
            ('kiosk_home', []),
        ]
        for name, args in pages:
            with self.subTest(page=name, args=args):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 200)

    def test_htmx_partials_render(self):
        partials = [
            (reverse('console_volunteers') + '?q=val', 'volunteer-table'),
            (reverse('console_roles') + '?difficulty=0', 'role-grid'),
            (reverse('console_venues') + '?q=lib', 'venue-grid'),
            (reverse('console_venue_preview') + f'?venue={self.venue.pk}', 'venue-preview'),
            (reverse('console_modules') + '?status=draft', 'module-grid'),
            (reverse('console_certificates') + '?validity=expiring', 'certificate-grid'),
            (reverse('console_add_role_slots', args=[self.live.pk]) + '?partial=options&q=greet', 'role-options'),
            (reverse('console_invite_recipients', args=[self.live.pk]) + f'?slot={self.slot.pk}', 'recipients'),
        ]
        for url, marker in partials:
            with self.subTest(url=url):
                response = self.client.get(url, **HTMX)
                self.assertContains(response, f'id="{marker}"')
                self.assertNotContains(response, '<html')


class ConsoleAccessTests(ConsoleFixture):
    def test_volunteers_are_forbidden(self):
        self.client.force_login(self.vol)
        self.assertEqual(self.client.get(reverse('console_dashboard')).status_code, 403)

    def test_anonymous_users_are_sent_to_login(self):
        response = self.client.get(reverse('console_events'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_old_theme_url_redirects_to_console(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('theme_settings_edit'))
        self.assertRedirects(response, reverse('console_settings'))


class ConsoleFlowTests(ConsoleFixture):
    def setUp(self):
        self.client.force_login(self.staff)

    def test_home_page_banner_edit(self):
        from io import BytesIO
        from PIL import Image
        buffer = BytesIO()
        Image.new('RGB', (4, 3)).save(buffer, 'PNG')
        url = reverse('console_settings_home')
        self.assertEqual(self.client.get(url).status_code, 200)
        data = {
            'title_en': 'Join us', 'subtitle_en': 'Help out.', 'button_1_text_en': 'Roles',
            'button_2_text_en': 'Train', 'button_1_url': '/en/opportunities/', 'button_2_url': 'https://example.com',
            'image': SimpleUploadedFile('hero.png', buffer.getvalue(), content_type='image/png'),
        }
        self.assertRedirects(self.client.post(url, data), url)
        hero = HeroSection.objects.get(pk=1)
        self.assertEqual((hero.title, hero.button_1_url), ('Join us', '/en/opportunities/'))
        self.assertTrue(hero.image)
        self.assertContains(self.client.get(reverse('home')), hero.image.url)

        data.pop('image')
        self.client.post(url, dict(data, **{'image-clear': 'on'}))
        hero.refresh_from_db()
        self.assertFalse(hero.image)
        self.assertContains(self.client.get(reverse('home')), 'hero-inner--text-only')

    def test_level_settings_add_edit_and_renumber(self):
        low = Level.objects.create(name='Starter', numeric_name=1, min_points=0)
        high = Level.objects.create(name='Hero', numeric_name=2, min_points=500)
        data = {
            'form-TOTAL_FORMS': '3', 'form-INITIAL_FORMS': '2', 'form-MIN_NUM_FORMS': '0', 'form-MAX_NUM_FORMS': '1000',
            'form-0-id': low.pk, 'form-0-name': 'Starter', 'form-0-min_points': '0', 'form-0-benefits': '',
            'form-1-id': high.pk, 'form-1-name': 'Hero', 'form-1-min_points': '500', 'form-1-benefits': 'Badge',
            'form-2-name': 'Helper', 'form-2-min_points': '100', 'form-2-benefits': '',
        }
        self.client.post(reverse('console_settings_levels'), data)
        self.assertEqual(list(Level.objects.order_by('numeric_name').values_list('name', flat=True)), ['Starter', 'Helper', 'Hero'])

        # Two levels can't share a threshold
        data['form-2-min_points'] = '500'
        data['form-2-id'] = Level.objects.get(name='Helper').pk
        data['form-INITIAL_FORMS'] = '3'
        response = self.client.post(reverse('console_settings_levels'), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Level.objects.get(name='Helper').min_points, 100)

    def test_assign_permanent_roles(self):
        detail = self.client.get(reverse('console_volunteer_detail', args=[self.mate.pk]))
        self.assertContains(detail, 'Permanent Roles')
        self.assertContains(detail, 'Librarian Assistant')

        url = reverse('console_volunteer_roles', args=[self.mate.pk])
        # Event roles can't be assigned as permanent positions
        response = self.client.post(url, {'role_ids': [self.perm_role.pk, self.role.pk]}, **HTMX)
        self.assertContains(response, 'id="vd-roles"')
        self.assertEqual(list(self.mate.profile.permanent_roles.all()), [self.perm_role])

        self.client.post(url, {}, **HTMX)
        self.assertFalse(self.mate.profile.permanent_roles.exists())

        self.client.force_login(self.vol)
        self.client.post(url, {'role_ids': [self.perm_role.pk]})
        self.assertFalse(self.mate.profile.permanent_roles.exists())

    def test_public_profile_lists_permanent_roles(self):
        self.assertContains(self.client.get(reverse('profile_view', args=[self.vol.username])), 'Librarian Assistant')
        self.assertNotContains(self.client.get(reverse('profile_view', args=[self.mate.username])), 'Permanent Positions')

    def test_volunteer_access_needs_permission(self):
        # Plain staff can't hand out staff status
        self.client.post(reverse('console_volunteer_access', args=[self.vol.pk]), {'is_staff': 'on'})
        self.vol.refresh_from_db()
        self.assertFalse(self.vol.is_staff)

        boss = User.objects.create_superuser('boss', 'boss@example.com', 'pw-boss-123')
        group = Group.objects.create(name='Coordinators')
        self.client.force_login(boss)
        self.client.post(reverse('console_volunteer_access', args=[self.vol.pk]), {'is_staff': 'on', 'group_ids': [group.pk]})
        self.vol.refresh_from_db()
        self.assertTrue(self.vol.is_staff)
        self.assertFalse(self.vol.is_superuser)
        self.assertEqual(list(self.vol.groups.all()), [group])

    def test_create_event_then_add_role_slots(self):
        start = timezone.localtime() + timedelta(days=3)
        response = self.client.post(reverse('console_event_create'), {
            'title_en': 'Food Drive', 'description_en': 'Collect food.', 'location_en': 'Park',
            'start_date': start.strftime('%Y-%m-%dT09:00'), 'end_date': start.strftime('%Y-%m-%dT17:00'),
            'attendees': 0,
        })
        event = Event.objects.get(title_en='Food Drive')
        self.assertRedirects(response, reverse('console_event_roles', args=[event.pk]))
        self.assertIn(self.staff, event.coordinators.all())

        day = start.date().isoformat()
        self.client.post(reverse('console_add_role_slots', args=[event.pk]), {
            'role': self.role.pk, 'is_public': 'on',
            'slot_date': [day, day], 'slot_start': ['09:00', '13:00'], 'slot_end': ['12:00', '17:00'],
            'slot_qty': ['4', '2'], 'slot_over': ['0', '1'],
        })
        self.assertEqual(event.role_slots.count(), 2)
        self.assertEqual(event.total_required(), 6)

    def test_invalid_slot_times_are_rejected(self):
        day = timezone.localdate().isoformat()
        self.client.post(reverse('console_add_role_slots', args=[self.live.pk]), {
            'role': self.role.pk, 'slot_date': [day], 'slot_start': ['15:00'], 'slot_end': ['09:00'],
            'slot_qty': ['1'], 'slot_over': ['0'],
        })
        self.assertEqual(self.live.role_slots.count(), 1)

    def test_invitations_create_invites_and_notifications(self):
        outsider = User.objects.create_user('new', 'new@example.com', 'pw')
        self.client.post(reverse('console_event_invite', args=[self.live.pk]), {
            'slot': self.slot.pk, 'user_ids': [outsider.pk], 'subject': 'Join us', 'body': 'Hi [Name]',
        })
        invite = EventSlotInvite.objects.get(user=outsider)
        self.assertEqual(invite.event_role_slot, self.slot)
        self.assertTrue(Notification.objects.filter(user=outsider, message='Join us').exists())
        # Tokens are unique per invite (the old default reused one value).
        self.assertNotEqual(invite.token, self.invite.token)

    def test_manual_check_in_and_out(self):
        self.client.post(reverse('console_manual_check_in', args=[self.live.pk]), {'user_id': self.mate.pk})
        shift = Shift.objects.get(user=self.mate, event_role_slot=self.slot)
        self.assertIsNone(shift.end_time)
        self.assertEqual(shift.role, self.role)
        self.client.post(reverse('console_manual_check_in', args=[self.live.pk]), {'shift_id': shift.pk})
        shift.refresh_from_db()
        self.assertIsNotNone(shift.end_time)

    def test_role_template_saves_skills_and_training(self):
        response = self.client.post(reverse('console_role_create'), {
            'name_en': 'Medic', 'description_en': 'Help people.', 'icon': 'person', 'icon_custom': 'medical_services',
            'skill_ids': [self.skill.pk], 'module_ids': [self.module.pk], f'mandatory_{self.module.pk}': 'on',
        })
        self.assertRedirects(response, reverse('console_roles'))
        role = Role.objects.get(name_en='Medic')
        self.assertEqual(role.icon, 'medical_services')
        self.assertEqual(list(role.preferred_skills.all()), [self.skill])
        self.assertTrue(RoleTrainingRequirement.objects.get(role=role).mandatory)

    def test_skill_chip_creates_new_skill(self):
        response = self.client.get(reverse('console_skill_chip') + '?name=Bilingual', **HTMX)
        self.assertContains(response, 'Bilingual')
        self.assertTrue(Skill.objects.filter(name='Bilingual').exists())

    def test_quiz_builder_adds_true_false_question(self):
        response = self.client.post(reverse('console_question_create', args=[self.module.pk, self.quiz.pk]), {
            'question_text_en': 'Fire doors stay closed?', 'is_true_false': 'on', 'correct_option': 'A',
        }, **HTMX)
        self.assertEqual(response.status_code, 200)
        question = self.quiz.questions.get(question_text_en='Fire doors stay closed?')
        self.assertEqual((question.option_a, question.option_b), ('True', 'False'))

    def test_question_needs_two_options(self):
        response = self.client.post(reverse('console_question_create', args=[self.module.pk, self.quiz.pk]), {
            'question_text_en': 'Pick one', 'option_a_en': 'Only', 'correct_option': 'A',
        }, **HTMX)
        self.assertContains(response, 'at least two options')
        self.assertEqual(self.quiz.questions.count(), 1)

    def test_lesson_reordering(self):
        second = TrainingLesson.objects.create(training_module=self.module, title='Alarms', content='Beep.', order=2)
        self.client.post(reverse('console_move_item', args=[self.module.pk, 'lesson', second.pk, 'up']), **HTMX)
        ordered = list(self.module.lessons.order_by('order').values_list('pk', flat=True))
        self.assertEqual(ordered, [second.pk, self.lesson.pk])

    def test_certificate_approval_sets_expiry_and_notifies(self):
        self.client.post(reverse('console_verification_review', args=[self.user_cert.pk]), {
            'action': 'approve', 'issue_date': '2026-01-15', 'expiration_date': '', 'admin_notes': 'Looks good',
        })
        self.user_cert.refresh_from_db()
        self.assertTrue(self.user_cert.verified)
        self.assertEqual(timezone.localtime(self.user_cert.expiration_date).date().isoformat(), '2028-01-15')
        self.assertTrue(Notification.objects.filter(user=self.vol).exists())

    def test_rejected_certificate_resubmission_returns_to_queue(self):
        self.client.post(reverse('console_verification_review', args=[self.user_cert.pk]), {'action': 'reject'})
        self.user_cert.refresh_from_db()
        self.assertTrue(self.user_cert.rejected)
        self.client.force_login(self.vol)
        self.client.post(reverse('submit_certificate', args=[self.cert.pk]), {
            'documents': [SimpleUploadedFile('new.pdf', b'%PDF-1.4')],
        })
        self.user_cert.refresh_from_db()
        self.assertFalse(self.user_cert.rejected)

    def test_private_feedback(self):
        self.client.post(reverse('console_volunteer_detail', args=[self.vol.pk]), {'rating': '4', 'note': 'Reliable'})
        self.assertEqual(AdminFeedback.objects.get(volunteer=self.vol).rating, 4)

    def test_cannot_deactivate_self(self):
        self.client.post(reverse('console_volunteer_status', args=[self.staff.pk]))
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)

    def test_publish_toggle_hides_event_from_volunteers(self):
        self.client.post(reverse('console_event_publish', args=[self.live.pk]))
        self.live.refresh_from_db()
        self.assertFalse(self.live.published)
        self.client.force_login(self.vol)
        self.assertEqual(self.client.get(reverse('opportunity_detail', args=[self.live.pk])).status_code, 404)

    def test_htmx_messages_become_toast_trigger(self):
        response = self.client.post(reverse('console_delete_slot', args=[self.live.pk, self.slot.pk]), **HTMX)
        self.assertIn('toasts', response['HX-Trigger'])


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class VolunteerPageTests(ConsoleFixture):
    def setUp(self):
        self.client.force_login(self.vol)

    def test_volunteer_pages_render(self):
        pages = [
            reverse('home'),
            reverse('my_events'),
            reverse('opportunity_detail', args=[self.live.pk]),
            reverse('event_thank_you', args=[self.past.pk]),
            reverse('event_certificate', args=[self.past.pk, self.vol.username]),
            reverse('event_impact_card', args=[self.past.pk]),
            reverse('endorsements_feed'),
            reverse('give_endorsement'),
            reverse('give_endorsement') + f'?user={self.mate.username}&event={self.past.pk}',
            reverse('endorse_people_search') + '?person_q=max',
            reverse('impact_record', args=[self.vol.username]),
            reverse('volunteer_resume', args=[self.vol.username]),
            reverse('terms_of_service'),
            reverse('edit_profile'),
            reverse('profile_view', args=[self.vol.username]),
            reverse('training_directory'),
            reverse('training_dashboard'),
            reverse('module_overview', args=[self.module.pk]),
            reverse('lesson_detail', args=[self.module.pk, self.lesson.pk]),
            reverse('verify_certificates'),
            reverse('submit_certificate', args=[self.cert.pk]),
        ]
        for url in pages:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_anonymous_public_pages(self):
        self.client.logout()
        for url in [reverse('home'), reverse('endorsements_feed'), reverse('impact_record', args=[self.vol.username]),
                    reverse('explore_opportunities'), reverse('training_directory'), reverse('login')]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_give_endorsement(self):
        self.client.post(reverse('give_endorsement'), {
            'user': self.mate.username, 'skills': [self.skill.pk], 'text': 'Always on time', 'event': self.past.pk,
        })
        endorsement = Endorsement.objects.get(endorser=self.vol, endorsed=self.mate)
        self.assertEqual(endorsement.event, self.past)
        # Volunteers can't leave private admin feedback.
        self.assertFalse(AdminFeedback.objects.exists())

    def test_endorsing_several_skills_makes_one_endorsement(self):
        other = Skill.objects.create(name='Logistics')
        self.client.post(reverse('give_endorsement'), {
            'user': self.mate.username, 'skills': [self.skill.pk, other.pk], 'text': 'Great lead',
        })
        endorsement = Endorsement.objects.get(endorser=self.vol, endorsed=self.mate)
        self.assertEqual(set(endorsement.skills.all()), {self.skill, other})
        # Endorsing a skill again moves it to the new endorsement instead of counting it twice
        self.client.post(reverse('give_endorsement'), {'user': self.mate.username, 'skills': [other.pk], 'text': 'Again'})
        self.assertEqual(Endorsement.objects.filter(endorser=self.vol, endorsed=self.mate, skills=other).count(), 1)
        self.assertEqual(list(endorsement.skills.all()), [self.skill])

    def test_endorsement_feed_matches_full_name(self):
        response = self.client.get(reverse('endorsements_feed'), {'q': self.vol.get_full_name()})
        self.assertEqual(len(response.context['page']), 1)

    def test_quick_endorse_is_idempotent(self):
        for _ in range(2):
            response = self.client.post(reverse('quick_endorse'), {'user_id': self.mate.pk, 'skill_id': self.skill.pk}, **HTMX)
            self.assertContains(response, 'endorsed')
        self.assertEqual(Endorsement.objects.filter(endorser=self.vol, endorsed=self.mate).count(), 1)
        other = Skill.objects.create(name='Logistics')
        self.client.post(reverse('quick_endorse'), {'user_id': self.mate.pk, 'skill_id': other.pk}, **HTMX)
        endorsement = Endorsement.objects.get(endorser=self.vol, endorsed=self.mate)
        self.assertEqual(endorsement.skills.count(), 2)

    def test_event_feedback(self):
        self.client.post(reverse('event_thank_you', args=[self.past.pk]), {'rating': '3', 'suggestions': 'More shade'})
        feedback = EventFeedback.objects.get(event=self.past, user=self.vol)
        self.assertEqual((feedback.rating, feedback.suggestions), (3, 'More shade'))

    def test_profile_skills_htmx(self):
        extra = Skill.objects.create(name='Cooking')
        response = self.client.post(reverse('profile_skills'), {'action': 'add', 'skill_id': extra.pk}, **HTMX)
        self.assertContains(response, 'Cooking')
        self.assertIn(extra, self.vol.profile.skills.all())
        self.client.post(reverse('profile_skills'), {'action': 'remove', 'skill_id': extra.pk}, **HTMX)
        self.assertNotIn(extra, self.vol.profile.skills.all())

    def test_lesson_completion_htmx(self):
        lesson = TrainingLesson.objects.create(training_module=self.module, title='More', content='Text', order=3)
        response = self.client.post(reverse('mark_lesson_complete', args=[self.module.pk, lesson.pk]), **HTMX)
        self.assertContains(response, 'hx-swap-oob')
        self.assertIn(self.vol, lesson.completed_by.all())

    def test_invite_only_slot_requires_invite(self):
        # Starts when the volunteer's current slot ends, so there's no scheduling conflict.
        private = EventRoleSlot.objects.create(event=self.live, role=self.role, is_public=False,
                                               start_time=self.slot.end_time,
                                               end_time=self.slot.end_time + timedelta(hours=1))
        self.client.post(reverse('role_slot_signup', args=[private.pk]))
        self.assertNotIn(self.vol, private.signups.all())

        invite = EventSlotInvite.objects.create(event_role_slot=private, user=self.vol)
        self.assertEqual(self.client.get(reverse('respond_to_invite', args=[invite.token])).status_code, 200)
        self.client.post(reverse('respond_to_invite', args=[invite.token]), {'response': 'accept'})
        self.assertIn(self.vol, private.signups.all())

    def test_signup_uses_selected_time_slot(self):
        later = EventRoleSlot.objects.create(event=self.live, role=self.role,
                                             start_time=self.live.start_date + timedelta(hours=4),
                                             end_time=self.live.start_date + timedelta(hours=5))
        self.client.force_login(self.staff)
        TrainingModuleCompletion.objects.create(training_module=self.module, user=self.staff)
        self.client.post(reverse('role_slot_signup', args=[self.slot.pk]), {'slot_id': later.pk})
        self.assertIn(self.staff, later.signups.all())
        self.assertNotIn(self.staff, self.slot.signups.all())


class AutoTranslateTests(ConsoleFixture):
    def setUp(self):
        self.client.force_login(self.staff)

    def post(self, data):
        return self.client.post(reverse('console_auto_translate'), data, **HTMX)

    def test_fills_only_empty_languages(self):
        fake = lambda texts, source, target: [f'[{target}] {t}' for t in texts]
        with patch('console.views.translate.translate_texts', side_effect=fake):
            response = self.post({
                'form_key': 'role', 'name_en': 'Greeter', 'description_en': 'Say hello.',
                'name_es': 'Recepcionista',  # already translated: must be kept
            })
        self.assertContains(response, 'hx-swap-oob="true"')
        self.assertContains(response, '[fr] Greeter')
        self.assertContains(response, '[uk] Say hello.')
        self.assertNotContains(response, 'id="field-name_es"')
        self.assertIn('toasts', response['HX-Trigger'])

    def test_markdown_fields_keep_their_editor(self):
        with patch('console.views.translate.translate_texts', side_effect=lambda t, s, g: t):
            response = self.post({'form_key': 'lesson', 'title_en': 'Exits', 'content_en': '**Go**', 'md_fields': 'content'})
        self.assertContains(response, 'md-toolbar')

    def test_translation_errors_become_a_toast(self):
        from console.auto_translate import TranslationError
        with patch('console.views.translate.translate_texts', side_effect=TranslationError('rate limited')):
            response = self.post({'form_key': 'event', 'title_en': 'Fest'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('rate limited', response['HX-Trigger'])

    def test_unknown_form_is_rejected(self):
        self.assertEqual(self.post({'form_key': 'nope'}).status_code, 400)

    def test_button_is_rendered_on_editors(self):
        self.assertContains(self.client.get(reverse('console_role_edit', args=[self.role.pk])), 'console/translate/')
        self.assertContains(self.client.get(reverse('console_quiz_edit', args=[self.module.pk, self.quiz.pk])), '"form_key": "question"')


class TranslationProviderTests(TestCase):
    def setUp(self):
        import console.auto_translate as at
        self.at = at
        at._google_blocked_until = 0.0

    def test_falls_back_to_mymemory_and_remembers_google_block(self):
        at = self.at
        with patch.object(at, '_translate_google_free', side_effect=at.GoogleBlocked('captcha')) as google, \
                patch.object(at, '_translate_mymemory', return_value=['Hola']) as mymemory, \
                patch.dict('os.environ', {}, clear=False):
            for key in ('GOOGLE_TRANSLATE_API_KEY', 'DEEPL_API_KEY'):
                os.environ.pop(key, None)
            self.assertEqual(at.translate_texts(['Hello'], 'en', 'es'), ['Hola'])
            self.assertEqual(at.translate_texts(['Hello'], 'en', 'es'), ['Hola'])
        self.assertEqual(google.call_count, 1)  # skipped while the block is remembered
        self.assertEqual(mymemory.call_count, 2)

    def test_libretranslate_batches_lines_and_keeps_markdown(self):
        at = self.at
        sent = {}

        class FakeResponse:
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout):
            import json as _json
            sent['url'] = request.full_url
            sent['payload'] = _json.loads(request.data)
            return FakeResponse(_json.dumps({'translatedText': [f'<{q}>' for q in sent['payload']['q']]}).encode())

        with patch.dict('os.environ', {'LIBRETRANSLATE_URL': 'http://translate.local:5000/'}), \
                patch.object(at.urllib.request, 'urlopen', side_effect=fake_urlopen):
            result = at.translate_texts(['Greeter', '## Title\n\n- item'], 'en', 'uk')
        self.assertEqual(sent['url'], 'http://translate.local:5000/translate')
        self.assertEqual(sent['payload']['q'], ['Greeter', 'Title', 'item'])  # one request, markers held back
        self.assertEqual(result, ['<Greeter>', '## <Title>\n\n- <item>'])

    def test_libretranslate_outage_falls_back(self):
        at = self.at
        with patch.dict('os.environ', {'LIBRETRANSLATE_URL': 'http://translate.local:5000'}), \
                patch.object(at.urllib.request, 'urlopen', side_effect=at.urllib.error.URLError('refused')), \
                patch.object(at, '_translate_google_free', return_value=['Hola']):
            self.assertEqual(at.translate_texts(['Hello'], 'en', 'es'), ['Hola'])

    def test_chunking_keeps_markdown_structure_and_limit(self):
        at = self.at
        seen = []

        def fake(piece):
            seen.append(piece)
            return piece.upper()

        long_line = ' '.join(['This sentence is part of a very long paragraph.'] * 20)
        text = f'## Title\n\n- item one\n1. step\n\n{long_line}'
        result = at._translate_in_chunks(text, fake)
        self.assertTrue(result.startswith('## TITLE\n\n- ITEM ONE\n1. STEP\n\n'))
        self.assertTrue(all(len(piece) <= at.MYMEMORY_MAX_CHARS for piece in seen))
        self.assertNotIn('##', ''.join(seen))


class BackendSettingsTests(ConsoleFixture):
    def setUp(self):
        cache.clear()  # SiteSettings is cached in memory between tests
        self.client.force_login(self.staff)
        self.url = reverse('console_settings_backend')

    def tearDown(self):
        cache.clear()

    def save(self, **data):
        return self.client.post(self.url, data)

    def test_page_renders_with_tabs(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'LibreTranslate')
        self.assertContains(response, reverse('console_settings'))
        self.assertContains(self.client.get(reverse('console_settings')), self.url)

    def test_secrets_are_write_only(self):
        self.save(libretranslate_url='http://translate.local:5000', deepl_api_key='abc123:fx')
        site = SiteSettings.objects.get()
        self.assertEqual(site.deepl_api_key, 'abc123:fx')
        self.assertNotContains(self.client.get(self.url), 'abc123:fx')

        self.save(libretranslate_url='http://translate.local:5000')  # blank secret keeps the saved one
        self.assertEqual(SiteSettings.objects.get().deepl_api_key, 'abc123:fx')

        self.save(libretranslate_url='', clear_deepl_api_key='on')
        self.assertEqual(SiteSettings.objects.get().deepl_api_key, '')

    def test_saved_url_beats_environment(self):
        import console.auto_translate as at
        self.save(libretranslate_url='http://saved.local:5000')
        with patch.dict('os.environ', {'LIBRETRANSLATE_URL': 'http://env.local:5000'}):
            self.assertEqual(at.config()['libretranslate_url'], 'http://saved.local:5000')

    def test_translation_test_button_reports_provider(self):
        with patch('console.auto_translate.translate_with_provider', return_value=(['¡Gracias!'], 'LibreTranslate')):
            response = self.client.post(reverse('console_settings_test_translation'), {'target': 'es'}, **HTMX)
        self.assertContains(response, 'Translated by LibreTranslate')
        self.assertContains(response, '¡Gracias!')

    def test_email_test_button_sends_to_admin(self):
        response = self.client.post(reverse('console_settings_test_email'), **HTMX)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(mail.outbox[0].to, ['admin@example.com'])

    def connect_listmonk(self, **extra):
        site = SiteSettings.get_settings()
        site.listmonk_url, site.listmonk_api_user, site.listmonk_api_token = 'http://lists.local', 'town-hall', 'tok'
        for name, value in extra.items():
            setattr(site, name, value)
        site.save()

    @override_settings(EMAIL_BACKEND='base.email.ListmonkEmailBackend')
    def test_send_mail_goes_through_the_system_template(self):
        self.connect_listmonk(default_from_email='Town Hall <hello@example.org>')
        EmailTemplate.objects.create(trigger='system', language='en', template_id=7)
        with patch('base.listmonk.api') as api:
            mail.send_mail('Hi', 'Body https://example.org', None, ['x@example.com'])
        method, path, payload = api.call_args.args
        self.assertEqual((method, path), ('POST', '/tx'))
        self.assertEqual((payload['template_id'], payload['subscriber_emails']), (7, ['x@example.com']))
        self.assertEqual(payload['subscriber_mode'], 'fallback')
        self.assertNotIn('subject', payload)  # the template's own subject is used: {{ .Tx.Data.subject }}
        self.assertEqual(payload['data']['subject'], 'Hi')
        self.assertIn('<a href="https://example.org"', payload['data']['html'])
        self.assertEqual(payload['from_email'], 'Town Hall <hello@example.org>')

    @override_settings(EMAIL_BACKEND='base.email.ListmonkEmailBackend')
    def test_email_backend_without_listmonk_prints_to_console(self):
        with patch('base.email.ConsoleBackend') as console_backend:
            mail.send_mail('Hi', 'Body', None, ['x@example.com'])
        console_backend.return_value.send_messages.assert_called_once()

    def connect_form(self):
        return {'listmonk_url': 'http://lists.local', 'listmonk_api_user': 'town-hall', 'listmonk_api_token': 'tok'}

    def test_saving_a_working_connection_sets_listmonk_up_in_the_background(self):
        from base import listmonk
        with patch('base.listmonk.get_lists', return_value=[]), patch('base.listmonk.run_in_background') as job:
            response = self.client.post(self.url, self.connect_form(), follow=True)
        self.assertIs(job.call_args.args[1], listmonk.connect)
        self.assertContains(response, 'Setting up the')

    def test_connecting_to_an_unreachable_server_still_saves(self):
        from base.listmonk import ListmonkError
        with patch('base.listmonk.get_lists', side_effect=ListmonkError('listmonk unreachable')), \
                patch('base.listmonk.run_in_background') as job:
            response = self.client.post(self.url, self.connect_form(), follow=True)
        job.assert_not_called()
        self.assertEqual(SiteSettings.objects.get().listmonk_url, 'http://lists.local')
        self.assertContains(response, "listmonk couldn&#x27;t be reached")

    def test_backend_page_reports_listmonk_errors(self):
        from base.listmonk import ListmonkError
        self.connect_listmonk()
        with patch('base.listmonk.get_lists', side_effect=ListmonkError('listmonk returned 403: forbidden', 403)):
            response = self.client.get(self.url)
        self.assertContains(response, 'listmonk returned 403: forbidden')

    def test_backend_page_links_to_communication_when_connected(self):
        self.connect_listmonk()
        with patch('base.listmonk.get_lists', return_value=[]):
            response = self.client.get(self.url)
        self.assertContains(response, 'Connected to listmonk')
        self.assertContains(response, reverse('console_communication_email'))

    def test_identity_change_refreshes_email_branding(self):
        from base import listmonk
        from console.forms import OrganizationForm
        self.connect_listmonk()
        form = OrganizationForm(instance=SiteSettings.get_settings())
        data = {name: form[name].value() for name in form.fields if name != 'logo'}
        data = {k: v for k, v in data.items() if v not in (None, False)}
        data['color_primary'] = '#123456'
        with patch('base.listmonk.run_in_background') as job:
            self.client.post(reverse('console_settings'), data)
        self.assertIs(job.call_args.args[1], listmonk.refresh_brand)
        with patch('base.listmonk.run_in_background') as job:
            self.client.post(reverse('console_settings'), data)  # nothing brand-related changed
        job.assert_not_called()


class FakeListmonk:
    """In-memory stand-in for the listmonk API (``base.listmonk.api``)."""

    def __init__(self):
        self.lists, self.templates, self.campaigns, self.tx, self.media = {}, {}, [], [], []
        self.subscribers, self.calls, self._next = [], [], 100

    def _id(self):
        self._next += 1
        return self._next

    def __call__(self, method, path, payload=None, cfg=None, raw=None, content_type=None):
        from base.listmonk import ListmonkError
        self.calls.append((method, path))
        route = path.split('?')[0]
        if route == '/lists':
            if method == 'POST':
                self.lists[self._id()] = payload['name']
                return {'id': self._next}
            return {'results': [{'id': i, 'name': n} for i, n in self.lists.items()]}
        if route == '/templates':
            if method == 'POST':
                self.templates[self._id()] = dict(payload)
                return {'id': self._next}
            return [{'id': i, 'name': t['name'], 'type': t['type']} for i, t in self.templates.items()]
        match = re.fullmatch(r'/templates/(\d+)', route)
        if match:
            template_id = int(match[1])
            if template_id not in self.templates:
                raise ListmonkError('listmonk returned 404: not found', 404)
            if method == 'PUT':
                self.templates[template_id] = dict(payload)
            return {'id': template_id, **self.templates[template_id]}
        if route == '/media':
            self.media.append(raw)
            return {'url': f'https://lists.local/uploads/{len(self.media)}.png'}
        if route == '/tx':
            self.tx.append(payload)
            return True
        if route == '/campaigns':
            self.campaigns.append(payload)
            return {'id': self._id()}
        if route.startswith('/campaigns/'):
            return True
        if route == '/subscribers':
            if method == 'POST':
                self.subscribers.append(payload)
                return {'id': self._id()}
            return {'results': []}
        raise AssertionError(f'Unexpected listmonk call: {method} {path}')


class ListmonkTemplateTests(ConsoleFixture):
    def setUp(self):
        cache.clear()
        site = SiteSettings.get_settings()
        site.listmonk_url, site.listmonk_api_user, site.listmonk_api_token = 'http://lists.local', 'town-hall', 'tok'
        site.save()
        self.fake = FakeListmonk()
        patcher = patch('base.listmonk.api', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        cache.clear()

    def test_connect_creates_the_list_seeds_every_event_and_language_and_adds_users(self):
        from base import email_templates, listmonk, triggers
        listmonk.connect()
        self.assertEqual(list(self.fake.lists.values()), ['Town Hall users'])
        expected = len(triggers.TRIGGERS) * len(email_templates.languages())
        self.assertEqual(len(self.fake.templates), expected)
        self.assertEqual(EmailTemplate.objects.count(), expected)
        visual = EmailTemplate.objects.get(trigger='event_published', language='en').template_id
        self.assertEqual(self.fake.templates[visual]['type'], 'campaign_visual')
        invite = self.fake.templates[EmailTemplate.objects.get(trigger='invitation', language='uk').template_id]
        self.assertEqual(invite['type'], 'tx')
        self.assertIn('Українська', invite['name'])
        self.assertIn('Town Hall brand settings', invite['body'])
        self.assertIn('.Tx.Data.message_html', invite['body'])   # documented in the template's comment
        self.assertEqual(len(self.fake.subscribers), User.objects.filter(is_active=True).exclude(email='').count())

        posted = sum(1 for call in self.fake.calls if call == ('POST', '/templates'))
        listmonk.connect()  # running again creates nothing new
        self.assertEqual(sum(1 for call in self.fake.calls if call == ('POST', '/templates')), posted)

    def test_seeds_are_translated(self):
        from base import listmonk
        listmonk.seed_templates(keys=['signup_confirmed'])
        bodies = {row.language: self.fake.templates[row.template_id]['body']
                  for row in EmailTemplate.objects.filter(trigger='signup_confirmed')}
        self.assertIn("You're on the team!", bodies['en'])
        self.assertIn('¡Ya formas parte del equipo!', bodies['es'])
        self.assertIn("Vous faites partie de l'équipe !", bodies['fr'].replace('&#x27;', "'"))
        self.assertIn('Ви в команді!', bodies['uk'])

    def test_html_fields_render_without_data_and_old_seeds_are_repaired(self):
        from base import email_templates, listmonk
        listmonk.seed_templates(keys=['system'])
        tpl_id = EmailTemplate.objects.get(trigger='system', language='en').template_id
        body = self.fake.templates[tpl_id]['body']
        self.assertIn('{{ with .Tx.Data.html }}{{ . | Safe }}{{ end }}', body)
        self.assertNotIn('{{ .Tx.Data.html | Safe }}', body)

        # Templates seeded before the fix are repaired by the next brand refresh; edits survive.
        old = body.replace('{{ with .Tx.Data.html }}{{ . | Safe }}{{ end }}', '{{ .Tx.Data.html | Safe }}<p>mine</p>')
        self.fake.templates[tpl_id]['body'] = old
        listmonk.refresh_brand()
        self.assertIn('{{ with .Tx.Data.html }}{{ . | Safe }}{{ end }}<p>mine</p>', self.fake.templates[tpl_id]['body'])
        self.assertEqual(email_templates.upgrade_body('{{ .Tx.Data.notes }}'), '{{ .Tx.Data.notes }}')

    def test_deleted_templates_are_recreated(self):
        from base import listmonk
        listmonk.seed_templates(keys=['welcome'])
        row = EmailTemplate.objects.get(trigger='welcome', language='en')
        del self.fake.templates[row.template_id]
        self.assertEqual(listmonk.seed_templates(keys=['welcome']), 1)
        row.refresh_from_db()
        self.assertIn(row.template_id, self.fake.templates)

    def test_reset_rewrites_town_hall_templates_but_never_a_custom_one(self):
        from base import listmonk
        listmonk.seed_templates(keys=['invitation'])
        seeded = EmailTemplate.objects.get(trigger='invitation', language='en')
        self.fake.templates[seeded.template_id]['body'] = 'edited in listmonk'
        custom = self.fake._id()
        self.fake.templates[custom] = {'name': 'My own invite', 'type': 'tx', 'subject': 's', 'body': 'mine'}
        EmailTemplate.objects.filter(trigger='invitation', language='es').update(template_id=custom)

        listmonk.seed_templates(keys=['invitation'], overwrite=True)
        self.assertIn('Town Hall brand settings', self.fake.templates[seeded.template_id]['body'])
        self.assertEqual(self.fake.templates[custom]['body'], 'mine')
        self.assertNotEqual(EmailTemplate.objects.get(trigger='invitation', language='es').template_id, custom)

    def test_logo_is_uploaded_to_listmonk_once(self):
        from base import listmonk
        site = SiteSettings.get_settings()
        site.logo = SimpleUploadedFile('logo.png', b'\x89PNG fake', content_type='image/png')
        site.save()
        listmonk.seed_templates(keys=['welcome'])
        listmonk.seed_templates(keys=['welcome'], overwrite=True)
        self.assertEqual(len(self.fake.media), 1)
        body = self.fake.templates[EmailTemplate.objects.get(trigger='welcome', language='en').template_id]['body']
        self.assertIn('$logo_url := "https://lists.local/uploads/1.png"', body)

    def test_brand_refresh_rewrites_values_and_keeps_staff_edits(self):
        from base import listmonk
        listmonk.seed_templates(keys=['welcome', 'event_published'])
        tx_id = EmailTemplate.objects.get(trigger='welcome', language='en').template_id
        self.fake.templates[tx_id]['body'] = self.fake.templates[tx_id]['body'].replace('</h1>', '</h1><p>Added in listmonk</p>')

        site = SiteSettings.get_settings()
        site.color_accent, site.company_name = '#123456', 'Riverside "Helpers"'
        site.save()
        self.assertGreater(listmonk.refresh_brand(), 0)

        tx = self.fake.templates[tx_id]
        self.assertIn('$color_accent := "#123456"', tx['body'])
        self.assertIn('$org_name := "Riverside \\"Helpers\\""', tx['body'])
        self.assertIn('Added in listmonk', tx['body'])
        visual = self.fake.templates[EmailTemplate.objects.get(trigger='event_published', language='en').template_id]
        self.assertIn('#123456', visual['body_source'])
        self.assertIn('Riverside', visual['body'])
        self.assertNotIn('#ac3509', visual['body_source'].lower())

    def test_publishing_creates_a_visual_campaign_for_every_chosen_list(self):
        from base import listmonk
        listmonk.connect()
        users_list = SiteSettings.get_settings().listmonk_list_id
        EmailTrigger.objects.create(key='event_published', enabled=True, extra_list_ids=[42])
        event = make_event('Winter Drive', timezone.now() + timedelta(days=3), timezone.now() + timedelta(days=3, hours=4),
                           published=False)
        Event.objects.filter(pk=event.pk).update(description='Coats for everyone')
        self.client.force_login(self.staff)
        with translation.override('en'):  # the campaign uses the publishing admin's language
            response = self.client.post(reverse('console_event_publish', args=[event.pk]), follow=True)

        campaign = self.fake.campaigns[-1]
        self.assertEqual((campaign['content_type'], campaign['lists']), ('visual', [users_list, 42]))
        self.assertEqual(campaign['subject'], 'New opportunity: Winter Drive')
        self.assertIn('Winter Drive', campaign['body'])
        self.assertIn('Coats for everyone', campaign['body_source'])
        self.assertIn(f'/opportunities/{event.pk}/', campaign['body'])
        for leftover in ('[event_title]', '[event_description]', 'town-hall.invalid'):
            self.assertNotIn(leftover, campaign['body'])
            self.assertNotIn(leftover, campaign['body_source'])  # no photo: the image block is dropped
        self.assertNotIn(('PUT', f'/campaigns/{self.fake._next}/status'), self.fake.calls)  # left as a draft
        self.assertContains(response, 'waiting as a draft')

    def test_publishing_with_the_trigger_off_creates_nothing(self):
        event = make_event('Winter Drive', timezone.now() + timedelta(days=3), timezone.now() + timedelta(days=3, hours=4),
                           published=False)
        self.client.force_login(self.staff)
        self.client.post(reverse('console_event_publish', args=[event.pk]))
        self.assertEqual(self.fake.campaigns, [])

    def test_emails_use_the_recipients_language(self):
        from base.email import deliver, notify
        EmailTemplate.objects.create(trigger='volunteer_message', language='en', template_id=1)
        EmailTemplate.objects.create(trigger='volunteer_message', language='es', template_id=2)
        EmailTrigger.objects.create(key='volunteer_message', enabled=True)
        Profile.objects.filter(user=self.vol).update(language='es')
        self.vol.refresh_from_db()
        with patch('base.email.threading.Thread') as thread, self.captureOnCommitCallbacks(execute=True):
            notify('volunteer_message', self.vol, {'message': 'Hola'})
        trigger, email, data, lang = thread.call_args.kwargs['args']
        self.assertEqual((trigger, email, lang, data['name']), ('volunteer_message', 'vol@example.com', 'es', 'Val'))
        deliver(trigger, [email], data, lang)
        self.assertEqual(self.fake.tx[-1]['template_id'], 2)
        deliver(trigger, [email], data, 'fr')  # no French template: falls back to the default language
        self.assertEqual(self.fake.tx[-1]['template_id'], 1)


class CommunicationEmailTests(ConsoleFixture):
    def setUp(self):
        cache.clear()
        self.client.force_login(self.staff)
        self.url = reverse('console_communication_email')

    def tearDown(self):
        cache.clear()

    def connect_listmonk(self, **extra):
        site = SiteSettings.get_settings()
        site.listmonk_url, site.listmonk_api_user, site.listmonk_api_token = 'http://lists.local', 'town-hall', 'tok'
        for name, value in extra.items():
            setattr(site, name, value)
        site.save()

    def remote(self):
        patches = [
            patch('base.listmonk.get_lists', return_value=[(2, 'Town Hall users'), (3, 'Cultural events')]),
            patch('base.listmonk.get_templates', return_value={
                'tx': [(5, 'Town Hall · Invitation received · English'), (6, 'Fancy invite')],
                'campaign': [], 'campaign_visual': [(8, 'Town Hall · New event published · English')]}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_nav_and_page_without_listmonk(self):
        response = self.client.get(self.url)
        self.assertContains(response, "connected yet, so emails are only printed")
        self.assertContains(response, 'Invitation received')
        self.assertContains(self.client.get(reverse('console_dashboard')), self.url)
        # Offline: one template ID input per event and language.
        self.assertContains(response, 'name="invitation_en"')
        self.assertContains(response, 'name="invitation_uk"')
        self.assertNotContains(response, 'Defaults')

    def test_choose_templates_per_event_and_language(self):
        self.connect_listmonk(listmonk_list_id=2)
        self.remote()
        EmailTemplate.objects.create(trigger='invitation', language='en', template_id=5)
        response = self.client.get(self.url)
        self.assertContains(response, '<option value="5" selected>Town Hall · Invitation received · English</option>', html=True)
        self.assertContains(response, '<option value="8">Town Hall · New event published · English</option>', html=True)
        self.assertContains(response, 'Cultural events')        # extra list for the campaign
        self.assertNotContains(response, 'value="2" id="id_event_published_lists')  # the users list is always included
        self.assertContains(response, 'templates are missing in listmonk')

        response = self.client.post(self.url, {
            'invitation_enabled': 'on', 'invitation_en': '6', 'invitation_es': '5',
            'signup_confirmed_enabled': 'on',
            'event_published_enabled': 'on', 'event_published_en': '8', 'event_published_auto_send': 'on',
            'event_published_lists': ['3'],
        })
        self.assertRedirects(response, self.url)
        templates = dict(((r.trigger, r.language), r.template_id) for r in EmailTemplate.objects.all())
        self.assertEqual(templates, {('invitation', 'en'): 6, ('invitation', 'es'): 5, ('event_published', 'en'): 8})
        rows = {t.key: t for t in EmailTrigger.objects.all()}
        self.assertTrue(rows['signup_confirmed'].enabled)
        self.assertEqual((rows['event_published'].auto_send, rows['event_published'].extra_list_ids), (True, [3]))
        self.assertTrue(rows['password_reset'].enabled)   # always on
        self.assertFalse(rows['welcome'].enabled)

    def test_buttons_run_listmonk_jobs(self):
        from base import listmonk
        self.connect_listmonk()
        with patch('base.listmonk.run_in_background') as job:
            response = self.client.post(reverse('console_listmonk_seed'), **HTMX)
        self.assertIs(job.call_args.args[1], listmonk.seed_templates)
        self.assertEqual(response.headers['HX-Refresh'], 'true')

        with patch('base.listmonk.seed_templates') as seed:
            response = self.client.post(reverse('console_listmonk_reset', args=['invitation']), **HTMX)
        seed.assert_called_once_with(keys=['invitation'], overwrite=True)
        self.assertEqual(response.headers['HX-Refresh'], 'true')
        self.assertEqual(self.client.post(reverse('console_listmonk_reset', args=['nope']), **HTMX).status_code, 404)

    def test_progress_banner_polls_until_the_job_is_done(self):
        cache.set('listmonk_task', 'Setting up listmonk')
        self.assertContains(self.client.get(self.url), 'Setting up listmonk')
        self.assertContains(self.client.get(reverse('console_listmonk_status'), **HTMX), 'every 3s')
        cache.delete('listmonk_task')
        self.assertEqual(self.client.get(reverse('console_listmonk_status'), **HTMX).headers['HX-Refresh'], 'true')

    def test_switched_off_trigger_sends_nothing(self):
        self.client.force_login(self.mate)
        self.client.post(reverse('role_slot_signup', args=[self.slot.pk]))  # signup_confirmed is off by default
        self.assertEqual(len(mail.outbox), 0)

    def test_switched_on_trigger_sends_its_data_only(self):
        EmailTrigger.objects.create(key='signup_confirmed', enabled=True)
        slot = EventRoleSlot.objects.create(event=self.live, role=self.role, start_time=self.live.end_date - timedelta(hours=1),
                                            end_time=self.live.end_date, required_qty=2)
        self.client.force_login(self.vol)
        self.client.post(reverse('role_slot_signup', args=[slot.pk]))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.listmonk_kind, 'signup_confirmed')
        self.assertEqual((message.listmonk_data['event'], message.listmonk_data['role']), ('Fall Festival', 'Greeter'))
        self.assertEqual(message.listmonk_data['name'], 'Val')

    def test_browsing_language_is_remembered_for_emails(self):
        self.addCleanup(translation.activate, 'en')  # requests below switch the thread's language
        self.client.force_login(self.vol)
        self.client.get('/es/')
        self.assertEqual(Profile.objects.get(user=self.vol).language, 'es')
        self.client.get('/fr/')
        self.assertEqual(Profile.objects.get(user=self.vol).language, 'fr')


class ListmonkSyncTests(ConsoleFixture):
    def setUp(self):
        cache.clear()
        site = SiteSettings.get_settings()
        site.listmonk_url, site.listmonk_api_user, site.listmonk_api_token = 'http://lists.local', 'town-hall', 'tok'
        site.listmonk_list_id = 4
        site.save()

    def tearDown(self):
        cache.clear()

    def test_new_subscriber_is_created_on_the_list(self):
        from base import listmonk
        Profile.objects.filter(user=self.vol).update(language='uk')
        self.vol.refresh_from_db()
        with patch('base.listmonk.api', return_value={'results': []}) as api:
            listmonk.sync_user(self.vol)
        method, path, payload = api.call_args.args
        self.assertEqual((method, path), ('POST', '/subscribers'))
        self.assertEqual((payload['email'], payload['lists']), (self.vol.email, [4]))
        self.assertEqual((payload['attribs']['town_hall_id'], payload['attribs']['language']), (self.vol.pk, 'uk'))
        self.assertTrue(payload['preconfirm_subscriptions'])

    def test_existing_subscriber_is_updated_and_added_to_the_list(self):
        from base import listmonk
        existing = {'id': 9, 'attribs': {'town_hall_id': self.vol.pk, 'city': 'Kyiv'}, 'lists': [{'id': 1}]}
        with patch('base.listmonk.api', side_effect=[{'results': [existing]}, None, None]) as api:
            listmonk.sync_user(self.vol)
        patch_call, list_call = api.call_args_list[1:]
        self.assertEqual(patch_call.args[:2], ('PATCH', '/subscribers/9'))
        self.assertEqual(patch_call.args[2]['attribs']['city'], 'Kyiv')  # attributes set in listmonk survive
        self.assertEqual(list_call.args[2], {'ids': [9], 'action': 'add', 'target_list_ids': [4], 'status': 'confirmed'})

    def test_unsubscribed_member_is_not_resubscribed(self):
        from base import listmonk
        existing = {'id': 9, 'attribs': {}, 'lists': [{'id': 4, 'subscription_status': 'unsubscribed'}]}
        with patch('base.listmonk.api', side_effect=[{'results': [existing]}, None]) as api:
            listmonk.sync_user(self.vol)
        self.assertEqual(len(api.call_args_list), 2)  # lookup + PATCH, no list change

    def test_account_changes_queue_a_sync_but_logins_do_not(self):
        with patch('base.listmonk.queue_sync') as queue:
            self.client.force_login(self.vol)
            queue.assert_not_called()
            self.vol.first_name = 'Valentina'
            self.vol.save()
        queue.assert_called_once_with(self.vol)

    def test_sync_button_runs_in_the_background(self):
        self.client.force_login(self.staff)
        with patch('base.listmonk.run_in_background') as job:
            response = self.client.post(reverse('console_listmonk_sync'), **HTMX)
        self.assertEqual(response.status_code, 204)
        with patch('base.listmonk.sync_user') as sync:
            job.call_args.args[1]()
        self.assertEqual(sync.call_count, User.objects.filter(is_active=True).exclude(email='').count())


class PasswordResetTests(ConsoleFixture):
    def test_reset_round_trip(self):
        self.assertContains(self.client.get(reverse('login')), reverse('password_reset'))
        response = self.client.post(reverse('password_reset'), {'email': 'VOL@example.com'})
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn('Val', message.body)
        link = re.search(r'http://testserver(\S+)', message.body).group(1)

        # The link redirects to a session-bound "set-password" URL.
        form_page = self.client.get(link, follow=True)
        self.assertContains(form_page, 'new_password1')
        response = self.client.post(form_page.redirect_chain[-1][0], {
            'new_password1': 'a-much-better-pass-42', 'new_password2': 'a-much-better-pass-42',
        })
        self.assertRedirects(response, reverse('password_reset_complete'))
        self.vol.refresh_from_db()
        self.assertTrue(self.vol.check_password('a-much-better-pass-42'))

    def test_unknown_email_looks_the_same(self):
        response = self.client.post(reverse('password_reset'), {'email': 'nobody@example.com'})
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)

    def test_bad_link_shows_expired_state(self):
        response = self.client.get(reverse('password_reset_confirm', args=['MQ', 'bad-token']))
        self.assertContains(response, 'This link has expired')


class KioskCodeTests(ConsoleFixture):
    def test_account_page_shows_the_code_but_the_public_profile_does_not(self):
        code = self.vol.profile.id_code
        self.client.force_login(self.vol)
        self.assertContains(self.client.get(reverse('edit_profile')), code[:3])
        self.client.force_login(self.mate)
        self.assertNotContains(self.client.get(reverse('profile_view', args=[self.vol.username])), code)

    def test_reset_gives_a_new_code_and_the_old_one_stops_working(self):
        old = self.vol.profile.id_code
        self.client.force_login(self.vol)
        self.assertEqual(self.client.get(reverse('reset_kiosk_code')).status_code, 405)
        response = self.client.post(reverse('reset_kiosk_code'), **HTMX)
        self.vol.profile.refresh_from_db()
        new = self.vol.profile.id_code
        self.assertNotEqual(new, old)
        self.assertEqual(len(new), 6)
        self.assertContains(response, new[3:])
        self.assertIn('toasts', response.headers['HX-Trigger'])

        self.client.logout()
        kiosk = reverse('kiosk_login_id_code', args=['event', self.live.pk])
        self.client.post(kiosk, {'code': old})
        self.assertNotIn('kiosk_user_id', self.client.session)
        self.client.post(kiosk, {'code': new})
        self.assertEqual(self.client.session['kiosk_user_id'], self.vol.pk)

    def test_reset_requires_login(self):
        response = self.client.post(reverse('reset_kiosk_code'))
        self.assertEqual(response.status_code, 302)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class KioskTests(ConsoleFixture):
    def test_language_picker_switches_kiosk_without_touching_staff_profile(self):
        self.addCleanup(translation.activate, 'en')  # requests below switch the thread's language
        self.client.force_login(self.staff)
        Profile.objects.filter(user=self.staff).update(language='en')
        self.client.cookies['django_language'] = 'fr'  # a stale cookie must not win over the picker
        login_url = reverse('kiosk_login_id_code', args=['venue', self.venue.pk])
        response = self.client.get(login_url)
        self.assertContains(response, 'class="kiosk-lang"')
        self.assertNotContains(response, 'mailto:')
        uk_url = re.search(r'href="([^"]+)" lang="uk"', response.content.decode())[1]
        self.assertEqual(uk_url, login_url.replace('/en/', '/uk/', 1))

        response = self.client.get(uk_url)
        self.assertContains(response, '<html lang="uk"')
        self.assertEqual(Profile.objects.get(user=self.staff).language, 'en')

    def test_event_check_in_and_out_with_id_code(self):
        login_url = reverse('kiosk_login_id_code', args=['event', self.live.pk])
        self.assertEqual(self.client.get(login_url).status_code, 200)
        response = self.client.post(login_url, {'code': self.vol.profile.id_code})
        self.assertRedirects(response, reverse('kiosk_start', args=['event', self.live.pk]), fetch_redirect_response=False)

        response = self.client.get(reverse('kiosk_start', args=['event', self.live.pk]))
        self.assertRedirects(response, reverse('kiosk_logged_in'), fetch_redirect_response=False)
        shift = Shift.objects.get(user=self.vol, end_time__isnull=True)
        self.assertEqual(shift.event_role_slot, self.slot)
        self.assertFalse(shift.clutched)
        self.assertEqual(self.client.get(reverse('kiosk_logged_in')).status_code, 200)
        # The kiosk never logs the volunteer into the site itself.
        self.assertNotIn('_auth_user_id', self.client.session)

        self.client.post(reverse('kiosk_check_out'), {'kind': 'event', 'place': self.live.pk})
        shift.refresh_from_db()
        self.assertIsNotNone(shift.end_time)
        self.assertNotIn('kiosk_user_id', self.client.session)

    def test_wrong_code_is_rejected(self):
        response = self.client.post(reverse('kiosk_login_id_code', args=['event', self.live.pk]), {'code': '000000'}, follow=True)
        self.assertContains(response, 'find that Volunteer ID')

    def test_unscheduled_volunteer_can_clutch(self):
        walk_in = User.objects.create_user('walkin', 'walk@example.com', 'pw-walk-123')
        TrainingModuleCompletion.objects.create(training_module=self.module, user=walk_in)
        self.client.post(reverse('kiosk_login_email_password', args=['event', self.live.pk]),
                         {'email': 'walk@example.com', 'password': 'pw-walk-123'})
        start_url = reverse('kiosk_start', args=['event', self.live.pk])
        self.assertContains(self.client.get(start_url), 'clutch bonus')
        self.client.post(start_url, {'choice': self.slot.pk})
        shift = Shift.objects.get(user=walk_in)
        self.assertTrue(shift.clutched)
        self.assertIn(walk_in, self.slot.signups.all())

    def test_venue_permanent_role_check_in(self):
        self.client.post(reverse('kiosk_login_id_code', args=['venue', self.venue.pk]), {'code': self.vol.profile.id_code})
        start_url = reverse('kiosk_start', args=['venue', self.venue.pk])
        self.assertContains(self.client.get(start_url), 'Librarian Assistant')
        self.client.post(start_url, {'choice': self.perm_role.pk})
        self.assertTrue(Shift.objects.filter(user=self.vol, role=self.perm_role, end_time__isnull=True).exists())
        self.assertEqual(self.client.get(reverse('kiosk_logged_in')).status_code, 200)

    def test_unassigned_volunteer_cannot_start_permanent_role(self):
        self.client.post(reverse('kiosk_login_id_code', args=['venue', self.venue.pk]), {'code': self.mate.profile.id_code})
        start_url = reverse('kiosk_start', args=['venue', self.venue.pk])
        self.assertNotContains(self.client.get(start_url), 'Librarian Assistant')
        self.client.post(start_url, {'choice': self.perm_role.pk})
        self.assertFalse(Shift.objects.filter(user=self.mate, end_time__isnull=True).exists())

    def test_device_heartbeat_needs_assigned_permanent_role(self):
        from django.test import RequestFactory
        from api.models import APIKey
        from api.views import register_heartbeat
        key = APIKey.objects.create(expires_at=timezone.now() + timedelta(days=1))

        def beat(user):
            request = RequestFactory().post('/', {'role_id': self.perm_role.pk}, HTTP_X_TOWNHALL_API_KEY=key.key)
            request.user = user
            return register_heartbeat(request)

        self.assertEqual(beat(self.mate).status_code, 403)
        self.assertFalse(Shift.objects.filter(user=self.mate, role=self.perm_role).exists())
        self.assertEqual(beat(self.vol).status_code, 200)
        self.assertTrue(Shift.objects.filter(user=self.vol, role=self.perm_role, end_time__isnull=True).exists())


@patch('base.geo.geocode', return_value=(10.0, 20.0))
class VenueTests(ConsoleFixture):
    def setUp(self):
        self.client.force_login(self.staff)
        self.stage = VenueFeature.objects.get(name_en='Stage')
        self.kitchen = VenueFeature.objects.get(name_en='Full kitchen')

    def venue_data(self, **extra):
        data = {'name_en': 'Community Hall', 'address': '5 Oak Ave', 'capacity': 200,
                'features': [self.stage.pk, self.kitchen.pk], 'open_0': '08:00', 'close_0': '20:00', 'staff_0': 'on'}
        data.update(extra)
        return data

    def event_data(self, **extra):
        start = timezone.localtime() + timedelta(days=3)
        data = {'title_en': 'Bake Sale', 'description_en': 'Cakes.', 'attendees': 0,
                'start_date': start.strftime('%Y-%m-%dT09:00'), 'end_date': start.strftime('%Y-%m-%dT17:00')}
        data.update(extra)
        return data

    def test_default_features_are_seeded(self, geocode):
        self.assertGreater(VenueFeature.objects.count(), 10)
        self.assertEqual(self.stage.category, 'facilities')

    def test_create_venue_with_features_and_hours(self, geocode):
        response = self.client.post(reverse('console_venue_create'), self.venue_data())
        venue = Venue.objects.get(name_en='Community Hall')
        self.assertRedirects(response, reverse('console_venue_edit', args=[venue.pk]))
        self.assertEqual(set(venue.features.all()), {self.stage, self.kitchen})
        self.assertEqual((venue.latitude, venue.longitude), (10.0, 20.0))
        hours = venue.operating_hours.get()
        self.assertEqual((hours.day_of_week, hours.open_to_staff_outside_hours), (0, True))

    def test_hours_must_close_after_opening(self, geocode):
        response = self.client.post(reverse('console_venue_create'), self.venue_data(open_0='18:00', close_0='09:00'))
        self.assertContains(response, 'Closing time must be after opening time.')
        self.assertFalse(Venue.objects.filter(name_en='Community Hall').exists())

    def test_clearing_hours_closes_the_day(self, geocode):
        self.client.post(reverse('console_venue_edit', args=[self.venue.pk]),
                         self.venue_data(name_en='Library', address='1 Main St', open_0='', close_0=''))
        self.assertFalse(self.venue.operating_hours.exists())

    def test_add_custom_feature_keeps_selection(self, geocode):
        response = self.client.post(reverse('console_venue_feature_add'), {
            'feature-name': 'Piano', 'feature-category': 'equipment', 'features': [self.stage.pk],
        }, **HTMX)
        piano = VenueFeature.objects.get(name_en='Piano')
        self.assertEqual(piano.name_es, 'Piano')
        self.assertContains(response, f'value="{piano.pk}" checked')
        self.assertContains(response, f'value="{self.stage.pk}" checked')

    def test_notes_post_expire_and_delete(self, geocode):
        self.client.post(reverse('console_venue_note_add', args=[self.venue.pk]),
                         {'note-text': 'Use the side door.', 'note-expires_in_days': 3}, **HTMX)
        note = self.venue.notes.get()
        self.assertEqual(note.expires_after, timedelta(days=3))
        old = VenueNote.objects.create(venue=self.venue, text='Old news', expires_after=timedelta(days=1))
        VenueNote.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=2))
        self.assertEqual(self.venue.active_notes(), [note])
        self.client.post(reverse('console_venue_note_delete', args=[self.venue.pk, note.pk]), **HTMX)
        self.assertFalse(self.venue.notes.filter(pk=note.pk).exists())

    def test_event_at_venue_uses_its_location(self, geocode):
        response = self.client.post(reverse('console_event_create'), self.event_data(venue=self.venue.pk, location_en='Ignored'))
        event = Event.objects.get(title_en='Bake Sale')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(event.venue, self.venue)
        self.assertEqual(event.location_en, '')
        self.assertEqual((event.latitude, event.longitude), (49.27, -123.1))
        self.assertEqual(event.place_name, 'Library')
        self.assertEqual(event.full_location, 'Library, 1 Main St')
        self.assertIn('LOCATION:Library\\, 1 Main St', event.generate_ics())
        geocode.assert_not_called()

    def test_event_needs_venue_or_address(self, geocode):
        response = self.client.post(reverse('console_event_create'), self.event_data())
        self.assertContains(response, 'Choose a venue or type an address.')
        self.client.post(reverse('console_event_create'), self.event_data(location_en='12 Elm St'))
        event = Event.objects.get(title_en='Bake Sale')
        self.assertIsNone(event.venue)
        self.assertEqual((event.place_name, event.latitude), ('12 Elm St', 10.0))

    def test_moving_venue_updates_its_events(self, geocode):
        self.live.venue = self.venue
        self.live.save()
        self.client.post(reverse('console_venue_edit', args=[self.venue.pk]), self.venue_data(name_en='Library', address='9 New Rd'))
        self.live.refresh_from_db()
        self.assertEqual((self.live.latitude, self.live.longitude), (10.0, 20.0))
        self.assertEqual(self.live.address, '9 New Rd')

    def test_deleting_venue_keeps_event_address(self, geocode):
        self.live.venue = self.venue
        self.live.save()
        self.client.post(reverse('console_venue_delete', args=[self.venue.pk]))
        self.live.refresh_from_db()
        self.assertIsNone(self.live.venue)
        self.assertEqual(self.live.location_en, 'Library, 1 Main St')
        self.assertTrue(Role.objects.filter(pk=self.perm_role.pk).exists())

    def test_search_finds_events_by_venue(self, geocode):
        self.live.venue = self.venue
        self.live.save()
        self.assertIn(self.live, Event.search_events('library'))
        self.assertIn(self.live, Event.search_events('main st'))

    def test_public_venue_page(self, geocode):
        self.client.logout()
        self.venue.features.add(self.stage)
        self.live.venue = self.venue
        self.live.save()
        VenueNote.objects.create(venue=self.venue, text='Elevator out of service')
        response = self.client.get(reverse('venue_detail', args=[self.venue.pk]))
        self.assertContains(response, 'Library')
        self.assertContains(response, 'Stage')
        self.assertContains(response, 'Elevator out of service')
        self.assertContains(response, 'Fall Festival')
        self.assertContains(response, 'Librarian Assistant')
        self.assertNotContains(response, reverse('console_venue_edit', args=[self.venue.pk]))

    def test_event_page_links_to_venue(self, geocode):
        self.live.venue = self.venue
        self.live.save()
        response = self.client.get(reverse('opportunity_detail', args=[self.live.pk]))
        self.assertContains(response, reverse('venue_detail', args=[self.venue.pk]))

    def test_role_editor_lists_venues(self, geocode):
        response = self.client.get(reverse('console_role_edit', args=[self.perm_role.pk]))
        self.assertContains(response, 'Manage venues')
        self.assertContains(response, f'value="{self.venue.pk}"')


class FakeBellTower:
    """Stands in for a Bell Tower server at base.belltower._http, so the client code
    (URL building, pagination, error mapping) runs for real."""
    BASE = 'http://tasks.local:8001'

    def __init__(self):
        self.lists, self.tasks, self.next_id = {}, {}, 1
        self.users = {'townhall': 'hall@example.org', 'pager1': 'pager@example.org', 'carol': 'vol@example.com'}
        self.staff = True  # Town Hall's own account is Bell Tower staff
        self.staff_accounts = {'townhall'}
        self.login_links = []
        self.codes = {'good-code': 'townhall'}
        self.revoked = False
        self.calls = []

    def _id(self):
        self.next_id += 1
        return self.next_id

    def __call__(self, method, url, *, data=None, headers=None):
        import json
        import urllib.parse
        from base.belltower import BellTowerError
        self.calls.append((method, url))
        parts = urllib.parse.urlsplit(url)
        path, query = parts.path, dict(urllib.parse.parse_qsl(parts.query))
        body = json.loads(data) if data and headers.get('Content-Type') == 'application/json' else dict(urllib.parse.parse_qsl((data or b'').decode()))
        if path == '/.well-known/belltower':
            return {'service': 'belltower', 'version': 1, 'authorize_url': self.BASE + '/connect/authorize/',
                    'token_url': self.BASE + '/connect/token/', 'revoke_url': self.BASE + '/api/connect/revoke/',
                    'api_url': self.BASE + '/api/', 'list_url': self.BASE + '/lists/{id}/', 'mcp_url': self.BASE + '/mcp'}
        if path == '/connect/token/':
            user = self.codes.pop(body.get('code'), None)
            if not user:
                raise BellTowerError('Bell Tower returned 400: invalid_grant', 400)
            return {'api_key': 'bt_secret', 'username': user}
        if headers.get('Authorization') != 'Bearer bt_secret' or self.revoked:
            raise BellTowerError('Bell Tower returned 401: Invalid token.', 401)
        if path == '/api/connect/revoke/':
            self.revoked = True
            return None
        if path == '/api/me/':
            return {'username': 'townhall', 'email': self.users['townhall'], 'name': '', 'is_staff': self.staff}
        if path == '/api/users/link/':
            if not self.staff:
                raise BellTowerError('Bell Tower returned 403: Only staff accounts can manage Belltower accounts.', 403)
            email = body['email'].lower()
            username = next((u for u, e in self.users.items() if e.lower() == email), None)
            if username:
                return {'username': username, 'email': email, 'name': '', 'created': False}
            username = email.split('@')[0]
            self.users[username] = email
            return {'username': username, 'email': email, 'name': '', 'created': True}
        if path.startswith('/api/users/') and path.endswith('/login-link/'):
            username = path.split('/')[3]
            if username in self.staff_accounts:
                raise BellTowerError('Bell Tower returned 403: Sign-in links are not issued for staff accounts.', 403)
            self.login_links.append((username, body['next']))
            return {'url': f'{self.BASE}/connect/login/token-for-{username}/'}
        segments = path.strip('/').split('/')[1:]  # drop "api"
        kind, pk = segments[0], int(segments[1]) if len(segments) > 1 else None
        store = self.lists if kind == 'lists' else self.tasks
        if pk is not None and pk not in store:
            raise BellTowerError('Bell Tower returned 404: Not found.', 404)
        if kind == 'lists':
            if method == 'GET' and pk is None:
                return {'results': [self._list(row) for row in self.lists.values()], 'next': None}
            if method == 'POST' and pk is None:
                row = {'id': self._id(), 'name': body['name'], 'persistent': body['persistent'],
                       'users': ['townhall', *body['users']], 'admins': ['townhall']}
                self.lists[row['id']] = row
                return self._list(row)
            row = store[pk]
            if segments[2:3] == ['members']:
                if method == 'POST':
                    username = body.get('username') or next((u for u, e in self.users.items() if e == body.get('email', '').lower()), None)
                    if username not in self.users:
                        raise BellTowerError('Bell Tower returned 404: No Belltower account matches.', 404)
                    admin = body.get('admin')  # None: keep an existing member's role
                    if username == 'townhall' and admin is False:
                        raise BellTowerError("Bell Tower returned 400: You can't remove your own admin role.", 400)
                    if username not in row['users']:
                        row['users'].append(username)
                    if admin is not None:
                        row['admins'] = [u for u in row['admins'] if u != username] + ([username] if admin else [])
                else:  # DELETE members/<username>/
                    row['users'].remove(segments[3])
                    row['admins'] = [u for u in row['admins'] if u != segments[3]]
                return self._list(row)
            if segments[2:3] == ['merge']:
                other = self.lists.pop(int(body['list']))
                for task in self.tasks.values():
                    if task['list'] == other['id']:
                        task['list'] = pk
                row['users'] += [u for u in other['users'] if u not in row['users']]
                row['admins'] += [u for u in other['admins'] if u not in row['admins']]
                row['name'] = body.get('name') or f"{row['name']} & {other['name']}"
                return self._list(row)
            if method == 'PATCH':
                row.update(body)
            if method == 'DELETE':
                del store[pk]
                return None
            return self._list(row)
        if method == 'GET' and pk is None:
            rows = [t for t in self.tasks.values() if str(t['list']) == query['list']]
            page = int(query.get('page', 1))
            # Pages of two, to exercise pagination.
            return {'results': [self._task(r) for r in rows[(page - 1) * 2:page * 2]], 'next': 'more' if len(rows) > page * 2 else None}
        if method == 'POST':
            row = {'id': self._id(), 'completed': False, 'is_expired': False, 'assignee': None, 'expires_at': None, **body}
            self.tasks[row['id']] = row
            return self._task(row)
        if method == 'PATCH':
            store[pk].update(body)
        if method == 'DELETE':
            del store[pk]
            return None
        return self._task(store[pk])

    def _list(self, row):
        return {**row, 'members': [
            {'username': u, 'name': '', 'email': self.users[u], 'admin': u in row['admins']} for u in row['users']
        ]}

    def _task(self, row):
        return {**row, 'assignee_name': row['assignee'] or ''}


class BellTowerConnectTests(ConsoleFixture):
    def setUp(self):
        cache.clear()
        self.client.force_login(self.staff)
        self.fake = FakeBellTower()
        patcher = patch('base.belltower._http', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        cache.clear()

    def test_normalize_url(self):
        from base.belltower import normalize_url
        self.assertEqual(normalize_url(' tasks.example.org/lists/3/?x=1 '), 'https://tasks.example.org')
        self.assertEqual(normalize_url('HTTPS://Tasks.Example.org:8443/'), 'https://tasks.example.org:8443')
        self.assertEqual(normalize_url('localhost:8001/'), 'http://localhost:8001')
        self.assertEqual(normalize_url('http://[::1]:8001/x'), 'http://[::1]:8001')
        for bad in ('', 'ftp://x.org', 'http://x.org:99999'):
            with self.assertRaises(ValueError):
                normalize_url(bad)

    def test_connect_flow(self):
        self.assertContains(self.client.get(reverse('console_settings_backend')), 'Connect to Bell Tower')
        response = self.client.post(reverse('console_belltower_connect'), {'belltower_url': 'tasks.local:8001/lists/'})
        location = response['Location']
        self.assertTrue(location.startswith(self.fake.BASE + '/connect/authorize/?client_name=Town+Hall'))
        state = self.client.session['belltower_connect']['state']
        callback = reverse('console_belltower_callback')
        self.assertIn('redirect_uri=http%3A%2F%2Ftestserver' + callback.replace('/', '%2F'), location)

        response = self.client.get(callback, {'code': 'good-code', 'state': state})
        self.assertRedirects(response, reverse('console_settings_backend'), fetch_redirect_response=False)
        site = SiteSettings.objects.get()
        self.assertEqual((site.belltower_url, site.belltower_api_key, site.belltower_username),
                         ('http://tasks.local:8001', 'bt_secret', 'townhall'))
        page = self.client.get(reverse('console_settings_backend'))
        self.assertContains(page, 'Acting as townhall')
        self.assertNotContains(page, 'bt_secret')

        self.client.post(reverse('console_belltower_disconnect'))
        self.assertTrue(self.fake.revoked)
        self.assertEqual(SiteSettings.objects.get().belltower_api_key, '')

    def test_callback_rejects_wrong_state(self):
        self.client.post(reverse('console_belltower_connect'), {'belltower_url': 'tasks.local:8001'})
        self.client.get(reverse('console_belltower_callback'), {'code': 'good-code', 'state': 'forged'})
        self.assertEqual(SiteSettings.objects.get().belltower_api_key, '')
        self.assertIn('good-code', self.fake.codes)  # never redeemed

    def test_callback_cancelled(self):
        self.client.post(reverse('console_belltower_connect'), {'belltower_url': 'tasks.local:8001'})
        state = self.client.session['belltower_connect']['state']
        response = self.client.get(reverse('console_belltower_callback'), {'error': 'access_denied', 'state': state}, follow=True)
        self.assertContains(response, 'connection cancelled')
        self.assertEqual(SiteSettings.objects.get().belltower_api_key, '')

    def test_connect_to_something_else(self):
        from base.belltower import BellTowerError
        with patch('base.belltower._http', side_effect=BellTowerError('Bell Tower returned 404: nope', 404)):
            response = self.client.post(reverse('console_belltower_connect'), {'belltower_url': 'example.org'}, follow=True)
        self.assertContains(response, 'find Bell Tower there')
        self.assertNotIn('belltower_connect', self.client.session)


class EventTasksTests(ConsoleFixture):
    def setUp(self):
        cache.clear()
        self.client.force_login(self.staff)
        self.fake = FakeBellTower()
        patcher = patch('base.belltower._http', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        site = SiteSettings.get_settings()
        site.belltower_url, site.belltower_api_key, site.belltower_username = FakeBellTower.BASE, 'bt_secret', 'townhall'
        site.belltower_endpoints = self.fake('GET', FakeBellTower.BASE + '/.well-known/belltower')
        site.save()
        start = timezone.now() + timedelta(days=3)
        self.event = make_event('Gala', start, start + timedelta(hours=4))
        EventRoleSlot.objects.create(event=self.event, role=self.role, start_time=start, end_time=start + timedelta(hours=2), required_qty=2).signups.add(self.vol)

    def tearDown(self):
        cache.clear()

    def url(self, name, *args):
        return reverse(name, args=[self.event.pk, *args])

    def test_not_connected(self):
        site = SiteSettings.get_settings()
        site.belltower_api_key = ''
        site.save()
        self.assertContains(self.client.get(self.url('console_event_tasks')), 'Connect Bell Tower to plan with tasks')

    def test_planning_list_created_on_first_visit(self):
        page = self.client.get(self.url('console_event_tasks'))
        self.assertContains(page, 'Start the planning list')
        self.assertContains(page, '<option value="admin@example.com">')  # staff
        self.assertContains(page, '<option value="vol@example.com">')  # signed up for this event
        self.assertNotContains(page, 'mate@example.com')  # neither
        response = self.client.post(self.url('console_task_list_create'), {'kind': 'planning'}, **HTMX)
        self.assertContains(response, 'No tasks yet')
        planning = EventTaskList.objects.get(event=self.event, kind='planning')
        remote = self.fake.lists[planning.belltower_id]
        self.assertEqual(remote['name'], 'Gala: Planning')
        self.assertTrue(remote['persistent'])
        # A second request (double click, two tabs) reuses it.
        self.client.post(self.url('console_task_list_create'), {'kind': 'planning'}, **HTMX)
        self.assertEqual(EventTaskList.objects.filter(kind='planning').count(), 1)
        self.assertNotContains(self.client.get(self.url('console_event_tasks')), 'Start the planning list')

    def test_tasks_flow(self):
        self.client.post(self.url('console_task_list_create'), {'kind': 'event', 'name': 'Kitchen'}, **HTMX)
        task_list = EventTaskList.objects.get(name='Kitchen')
        for title in ('Order food', 'Buy napkins', 'Wash trays'):
            response = self.client.post(self.url('console_task_add', task_list.pk), {'title': title}, **HTMX)
        self.assertContains(response, 'Wash trays')  # third task comes from the second page
        self.assertContains(response, '0 of 3 done')

        task_id = next(t['id'] for t in self.fake.tasks.values() if t['title'] == 'Order food')
        response = self.client.post(self.url('console_task_toggle', task_list.pk, task_id), **HTMX)
        self.assertTrue(self.fake.tasks[task_id]['completed'])
        self.assertContains(response, '1 of 3 done')
        self.assertContains(response, 'Done (1)')

        self.client.post(self.url('console_task_delete', task_list.pk, task_id), **HTMX)
        self.assertNotIn(task_id, self.fake.tasks)

    def test_cannot_touch_tasks_of_other_lists(self):
        self.client.post(self.url('console_task_list_create'), {'kind': 'event', 'name': 'A'}, **HTMX)
        self.client.post(self.url('console_task_list_create'), {'kind': 'event', 'name': 'B'}, **HTMX)
        a, b = EventTaskList.objects.order_by('name')
        self.client.post(self.url('console_task_add', b.pk), {'title': 'Secret'}, **HTMX)
        task_id = next(iter(self.fake.tasks))
        response = self.client.post(self.url('console_task_toggle', a.pk, task_id), **HTMX)
        self.assertContains(response, 'not in this list')
        self.assertFalse(self.fake.tasks[task_id]['completed'])

    def test_sharing_by_email(self):
        self.client.post(self.url('console_task_list_create'), {'kind': 'event', 'name': 'Setup'}, **HTMX)
        task_list = EventTaskList.objects.get()
        share = self.url('console_task_list_share', task_list.pk)
        response = self.client.post(share, {'email': 'VOL@example.com'}, **HTMX)
        self.assertContains(response, 'vol@example.com')
        remote = self.fake.lists[task_list.belltower_id]
        self.assertEqual((remote['users'], remote['admins']), (['townhall', 'carol'], ['townhall']))

        # Role toggle, by username.
        self.client.post(share, {'role': 'carol', 'admin': '1'}, **HTMX)
        self.assertIn('carol', remote['admins'])
        self.client.post(share, {'role': 'carol'}, **HTMX)
        self.assertNotIn('carol', remote['admins'])

        response = self.client.post(share, {'email': 'nobody@example.org'}, **HTMX)
        self.assertContains(response, 'No Bell Tower account uses nobody@example.org')

        # Town Hall's own account can't be demoted or removed from here.
        self.client.post(share, {'role': 'townhall'}, **HTMX)
        self.client.post(share, {'remove': 'townhall'}, **HTMX)
        response = self.client.post(share, {'email': 'hall@example.org'}, **HTMX)
        self.assertContains(response, 'remove your own admin role')
        self.assertIn('townhall', remote['admins'])

        self.client.post(share, {'remove': 'carol'}, **HTMX)
        self.assertEqual(remote['users'], ['townhall'])

    def test_task_details_and_editing(self):
        self.client.post(self.url('console_task_list_create'), {'kind': 'event', 'name': 'Kitchen'}, **HTMX)
        task_list = EventTaskList.objects.get()
        self.client.post(self.url('console_task_list_share', task_list.pk), {'email': 'pager@example.org'}, **HTMX)
        response = self.client.post(self.url('console_task_add', task_list.pk), {
            'title': 'Order food', 'description': 'Vegetarian options', 'expires_at': '2030-01-02T15:30', 'assignee': 'pager1',
        }, **HTMX)
        self.assertContains(response, 'Vegetarian options')
        self.assertContains(response, 'Jan 2, 15:30')
        task_id, task = next(iter(self.fake.tasks.items()))
        self.assertEqual(task['assignee'], 'pager1')
        sent = timezone.datetime.fromisoformat(task['expires_at'])
        self.assertEqual(timezone.localtime(sent).strftime('%Y-%m-%d %H:%M'), '2030-01-02 15:30')

        form = self.client.get(self.url('console_task_edit', task_list.pk, task_id), **HTMX)
        self.assertContains(form, 'value="Order food"')
        self.assertContains(form, 'value="2030-01-02T15:30"')
        self.assertContains(form, '<option value="pager1" selected>')

        self.client.post(self.url('console_task_edit', task_list.pk, task_id),
                         {'title': 'Order pizza', 'description': '', 'expires_at': '', 'assignee': ''}, **HTMX)
        self.assertEqual((task['title'], task['description'], task['expires_at'], task['assignee']), ('Order pizza', '', None, None))

    def test_planning_hides_once_event_starts(self):
        page = self.client.get(self.url('console_event_tasks'))
        self.assertContains(page, 'Start the planning list')
        self.assertNotContains(page, 'planning-hidden')

        # Staff can hide it before the event...
        self.client.get(self.url('console_event_tasks') + '?planning=hide')
        page = self.client.get(self.url('console_event_tasks'))
        self.assertContains(page, 'planning-hidden')
        self.assertContains(page, 'Show planning')

        # ...and it's hidden by default once the event is under way.
        self.event = self.live
        page = self.client.get(self.url('console_event_tasks'))
        self.assertContains(page, 'planning-hidden')
        self.assertNotContains(page, 'Start the planning list')
        self.client.get(self.url('console_event_tasks') + '?planning=show')
        self.assertContains(self.client.get(self.url('console_event_tasks')), 'Start the planning list')

    def make_lists(self, *names):
        for name in names:
            self.client.post(self.url('console_task_list_create'), {'kind': 'event', 'name': name}, **HTMX)
        return [EventTaskList.objects.get(name=name) for name in names]

    def test_quick_expiry(self):
        (task_list,) = self.make_lists('Cleanup')
        before = timezone.now()
        self.client.post(self.url('console_task_add', task_list.pk), {'title': 'Bins', 'expires_in': '15'}, **HTMX)
        self.client.post(self.url('console_task_add', task_list.pk),
                         {'title': 'Report', 'expires_in': 'custom', 'expires_at': '2030-01-02T08:00'}, **HTMX)
        self.client.post(self.url('console_task_add', task_list.pk), {'title': 'Sweep', 'expires_in': ''}, **HTMX)
        bins, report, sweep = self.fake.tasks.values()
        delta = timezone.datetime.fromisoformat(bins['expires_at']) - before
        self.assertTrue(timedelta(minutes=14) < delta < timedelta(minutes=16))
        self.assertEqual(timezone.localtime(timezone.datetime.fromisoformat(report['expires_at'])).hour, 8)
        self.assertIsNone(sweep['expires_at'])
        # Editing a task with an expiry starts on "Pick" with its date filled in.
        form = self.client.get(self.url('console_task_edit', task_list.pk, report['id']), **HTMX)
        self.assertContains(form, 'value="custom" checked')
        self.assertContains(form, 'value="2030-01-02T08:00"')

    def test_rename(self):
        (task_list,) = self.make_lists('Kitchen')
        self.assertContains(self.client.get(self.url('console_task_list', task_list.pk) + '?rename=1', **HTMX), 'name="name"')
        response = self.client.post(self.url('console_task_list_rename', task_list.pk), {'name': 'Food'}, **HTMX)
        self.assertContains(response, 'Food')
        self.assertEqual(self.fake.lists[task_list.belltower_id]['name'], 'Gala: Food')
        task_list.refresh_from_db()
        self.assertEqual(task_list.name, 'Food')

    def test_renamed_in_belltower_is_picked_up(self):
        (task_list,) = self.make_lists('Kitchen')
        self.fake.lists[task_list.belltower_id]['name'] = 'Gala: Catering'
        self.client.get(self.url('console_task_list', task_list.pk), **HTMX)
        task_list.refresh_from_db()
        self.assertEqual(task_list.name, 'Catering')

    def test_merge_lists(self):
        kitchen, setup = self.make_lists('Kitchen', 'Setup')
        self.client.post(self.url('console_task_add', setup.pk), {'title': 'Chairs'}, **HTMX)
        response = self.client.post(self.url('console_task_list_merge', kitchen.pk), {'source': setup.pk}, **HTMX)
        self.assertContains(response, 'Kitchen &amp; Setup')
        self.assertContains(response, 'Chairs')
        self.assertContains(response, f'id="task-list-{setup.pk}" hx-swap-oob="delete"')
        self.assertEqual(self.fake.lists[kitchen.belltower_id]['name'], 'Gala: Kitchen & Setup')
        self.assertNotIn(setup.belltower_id, self.fake.lists)
        self.assertEqual(list(EventTaskList.objects.values_list('name', flat=True)), ['Kitchen & Setup'])

    def test_planning_cannot_be_merged(self):
        self.client.post(self.url('console_task_list_create'), {'kind': 'planning'}, **HTMX)
        (kitchen,) = self.make_lists('Kitchen')
        planning = EventTaskList.objects.get(kind='planning')
        response = self.client.post(self.url('console_task_list_merge', kitchen.pk), {'source': planning.pk}, **HTMX)
        self.assertContains(response, "planning list can")
        self.assertEqual(EventTaskList.objects.count(), 2)

    def test_move_task(self):
        kitchen, setup = self.make_lists('Kitchen', 'Setup')
        self.client.post(self.url('console_task_add', setup.pk), {'title': 'Chairs'}, **HTMX)
        task_id = next(iter(self.fake.tasks))
        response = self.client.post(self.url('console_task_move', kitchen.pk), {'task': task_id, 'source': setup.pk}, **HTMX)
        self.assertEqual(self.fake.tasks[task_id]['list'], kitchen.belltower_id)
        html = response.content.decode()
        self.assertIn(f'id="task-list-{kitchen.pk}"', html)
        self.assertIn(f'id="task-list-{setup.pk}"', html)  # source card re-rendered out of band
        self.assertIn('hx-swap-oob="true"', html)
        # A task that isn't in the claimed source list isn't moved.
        response = self.client.post(self.url('console_task_move', setup.pk), {'task': task_id, 'source': setup.pk}, **HTMX)
        self.assertContains(response, 'not in this list')
        self.assertEqual(self.fake.tasks[task_id]['list'], kitchen.belltower_id)

    def test_list_deleted_in_belltower_is_forgotten(self):
        self.client.post(self.url('console_task_list_create'), {'kind': 'event', 'name': 'Gone'}, **HTMX)
        task_list = EventTaskList.objects.get()
        self.fake.lists.clear()
        response = self.client.get(self.url('console_task_list', task_list.pk), **HTMX)
        self.assertEqual(response.content, b'')
        self.assertFalse(EventTaskList.objects.exists())

    def test_delete_list(self):
        self.client.post(self.url('console_task_list_create'), {'kind': 'event', 'name': 'Temp'}, **HTMX)
        task_list = EventTaskList.objects.get()
        self.client.post(self.url('console_task_list_delete', task_list.pk), **HTMX)
        self.assertFalse(EventTaskList.objects.exists())
        self.assertEqual(self.fake.lists, {})

    def test_belltower_down_shows_error_in_card(self):
        from base.belltower import BellTowerError
        self.client.post(self.url('console_task_list_create'), {'kind': 'event', 'name': 'Setup'}, **HTMX)
        task_list = EventTaskList.objects.get()
        with patch('base.belltower._http', side_effect=BellTowerError('Bell Tower unreachable at tasks.local:8001: refused')):
            response = self.client.get(self.url('console_task_list', task_list.pk), **HTMX)
        self.assertContains(response, 'unreachable')
        self.assertTrue(EventTaskList.objects.exists())

    def test_lists_from_another_server_are_hidden(self):
        EventTaskList.objects.create(event=self.event, kind='event', name='Old server list', belltower_url='http://old.local', belltower_id=99)
        self.assertNotContains(self.client.get(self.url('console_event_tasks')), 'Old server list')


@override_settings(BELLTOWER_RUN_INLINE=True)
class BellTowerPeopleTests(ConsoleFixture):
    """Role lists, linking Town Hall users to Bell Tower accounts, and sign-in links."""

    def setUp(self):
        cache.clear()
        self.fake = FakeBellTower()
        patcher = patch('base.belltower._http', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        site = SiteSettings.get_settings()
        site.belltower_url, site.belltower_api_key, site.belltower_username = FakeBellTower.BASE, 'bt_secret', 'townhall'
        site.belltower_endpoints = self.fake('GET', FakeBellTower.BASE + '/.well-known/belltower')
        site.save()
        start = timezone.now() + timedelta(days=3)
        self.event = make_event('Gala', start, start + timedelta(hours=4))
        self.start = start

    def tearDown(self):
        cache.clear()

    def add_slot(self, role=None):
        with self.captureOnCommitCallbacks(execute=True):
            return EventRoleSlot.objects.create(event=self.event, role=role or self.role, start_time=self.start,
                                                end_time=self.start + timedelta(hours=2), required_qty=3)

    def role_list(self):
        task_list = EventTaskList.objects.get(event=self.event, roles=self.role)
        return task_list, self.fake.lists[task_list.belltower_id]

    def test_new_user_is_linked_to_existing_account_by_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            user = User.objects.create_user('pagerperson', 'PAGER@example.org', 'pw')
        self.assertEqual(user.belltower_links.get().username, 'pager1')

    def test_new_user_gets_an_account_created(self):
        with self.captureOnCommitCallbacks(execute=True):
            user = User.objects.create_user('newbie', 'newbie@example.org', 'pw')
        self.assertEqual(user.belltower_links.get().username, 'newbie')
        self.assertEqual(self.fake.users['newbie'], 'newbie@example.org')

    def test_users_without_email_are_skipped(self):
        with self.captureOnCommitCallbacks(execute=True):
            user = User.objects.create_user('noemail', '', 'pw')
        self.assertFalse(user.belltower_links.exists())

    def test_role_list_made_when_slot_added_and_signups_join(self):
        slot = self.add_slot()
        task_list, remote = self.role_list()
        self.assertEqual((task_list.name, remote['name']), ('Greeter tasks', 'Gala: Greeter tasks'))
        self.assertEqual(remote['users'], ['townhall'])

        with self.captureOnCommitCallbacks(execute=True):
            slot.add_signup(self.vol)  # vol@example.com is "carol" in Bell Tower
        self.assertEqual(remote['users'], ['townhall', 'carol'])
        self.assertNotIn('carol', remote['admins'])  # members complete and take tasks

        with self.captureOnCommitCallbacks(execute=True):
            slot.signups.remove(self.vol)
        self.assertEqual(remote['users'], ['townhall'])

    def test_second_slot_of_same_role_keeps_membership(self):
        first, second = self.add_slot(), self.add_slot()
        with self.captureOnCommitCallbacks(execute=True):
            first.signups.add(self.vol)
            second.signups.add(self.vol)
        with self.captureOnCommitCallbacks(execute=True):
            first.signups.remove(self.vol)
        _, remote = self.role_list()
        self.assertIn('carol', remote['users'])  # still on the second slot
        self.assertEqual(EventTaskList.objects.filter(roles=self.role).count(), 1)

    def test_staff_promoted_admins_stay(self):
        slot = self.add_slot()
        _, remote = self.role_list()
        with self.captureOnCommitCallbacks(execute=True):
            slot.signups.add(self.vol)
        remote['admins'].append('carol')  # staff made them an admin
        with self.captureOnCommitCallbacks(execute=True):
            slot.signups.add(self.mate)
            slot.signups.remove(self.vol)
        self.assertIn('carol', remote['users'])

    def test_tasks_tab_makes_missing_role_lists_with_existing_signups(self):
        # Slots and sign-ups from before Bell Tower was connected (no signals ran).
        slot = EventRoleSlot.objects.create(event=self.event, role=self.role, start_time=self.start,
                                            end_time=self.start + timedelta(hours=2), required_qty=3)
        slot.signups.add(self.vol)
        self.client.force_login(self.staff)
        page = self.client.get(reverse('console_event_tasks', args=[self.event.pk]))
        self.assertContains(page, 'Greeter tasks')
        _, remote = self.role_list()
        self.assertEqual(remote['users'], ['townhall', 'carol'])

    def test_role_list_cannot_be_deleted(self):
        self.add_slot()
        task_list, _ = self.role_list()
        self.client.force_login(self.staff)
        response = self.client.post(reverse('console_task_list_delete', args=[self.event.pk, task_list.pk]), **HTMX)
        self.assertContains(response, "stays while the role is at the event")
        self.assertNotContains(response, 'task-card-delete')
        # Its role icon doubles as the drag grip.
        self.assertContains(response, 'task-drag-handle has-role')
        self.assertContains(response, 'task-handle-grip')

    def test_role_list_merged_into_another_keeps_its_role(self):
        slot = self.add_slot()
        role_list, _ = self.role_list()
        self.client.force_login(self.staff)
        self.client.post(reverse('console_task_list_create', args=[self.event.pk]), {'kind': 'event', 'name': 'Setup'}, **HTMX)
        setup = EventTaskList.objects.get(name='Setup')
        self.client.post(reverse('console_task_list_merge', args=[self.event.pk, setup.pk]), {'source': role_list.pk}, **HTMX)
        setup.refresh_from_db()
        self.assertEqual(setup.name, 'Setup & Greeter tasks')
        self.assertEqual(list(setup.roles.all()), [self.role])
        self.assertFalse(EventTaskList.objects.filter(pk=role_list.pk).exists())

        # It isn't recreated, and new sign-ups for the role land on the merged list.
        self.client.get(reverse('console_event_tasks', args=[self.event.pk]))
        self.assertEqual(EventTaskList.objects.filter(event=self.event).count(), 1)
        with self.captureOnCommitCallbacks(execute=True):
            slot.signups.add(self.vol)
        self.assertIn('carol', self.fake.lists[setup.belltower_id]['users'])
        self.assertContains(self.client.get(reverse('console_task_list', args=[self.event.pk, setup.pk]), **HTMX), 'task-drag-handle has-role')

    def test_two_role_lists_merge_into_one(self):
        other_role = Role.objects.create(name='Cashier', icon='payments')
        self.add_slot()
        cashier_slot = self.add_slot(other_role)
        greeter, _ = self.role_list()
        cashier = EventTaskList.objects.get(roles=other_role)
        self.client.force_login(self.staff)
        self.client.post(reverse('console_task_list_merge', args=[self.event.pk, greeter.pk]), {'source': cashier.pk}, **HTMX)
        self.assertCountEqual(greeter.roles.all(), [self.role, other_role])
        with self.captureOnCommitCallbacks(execute=True):
            cashier_slot.signups.add(self.vol)
        self.assertIn('carol', self.fake.lists[greeter.belltower_id]['users'])
        # Leaving their only slot for either role takes them off.
        with self.captureOnCommitCallbacks(execute=True):
            cashier_slot.signups.remove(self.vol)
        self.assertNotIn('carol', self.fake.lists[greeter.belltower_id]['users'])

    def test_volunteer_opens_their_role_list(self):
        slot = self.add_slot()
        with self.captureOnCommitCallbacks(execute=True):
            slot.signups.add(self.vol)
        task_list, _ = self.role_list()
        self.client.force_login(self.vol)
        page = self.client.get(reverse('opportunity_detail', args=[self.event.pk]))
        self.assertContains(page, f'?list={task_list.pk}')
        self.assertContains(page, 'Your tasks')
        response = self.client.get(reverse('open_tasks') + f'?list={task_list.pk}')
        self.assertRedirects(response, f'{FakeBellTower.BASE}/connect/login/token-for-carol/', fetch_redirect_response=False)
        self.assertEqual(self.fake.login_links, [('carol', f'/lists/{task_list.belltower_id}/')])
        self.assertContains(self.client.get(reverse('home')), reverse('open_tasks'))  # "My tasks" in the menu

    def test_staff_bell_tower_accounts_sign_in_themselves(self):
        self.fake.users['townhall'] = 'admin@example.com'  # the staff user's email is Town Hall's own Bell Tower account
        self.client.force_login(self.staff)
        response = self.client.get(reverse('open_tasks'))
        self.assertRedirects(response, FakeBellTower.BASE + '/', fetch_redirect_response=False)

    def test_backend_card_shows_people_status(self):
        BellTowerLink = self.vol.belltower_links.model
        BellTowerLink.objects.create(user=self.vol, belltower_url=FakeBellTower.BASE, username='carol')
        self.client.force_login(self.staff)
        page = self.client.get(reverse('console_settings_backend'))
        self.assertContains(page, '1 of')
        self.assertContains(page, 'people linked to Bell Tower')
        self.fake.staff = False
        self.assertContains(self.client.get(reverse('console_settings_backend')), "isn't Bell Tower staff")

    def test_link_all_users_after_connecting(self):
        from base import belltower
        belltower.link_all_users()
        linked = dict(self.vol.belltower_links.model.objects.values_list('user__username', 'username'))
        self.assertEqual(linked['vol'], 'carol')
        self.assertEqual(linked['mate'], 'mate')  # created


class ChainOfCommandFixture(ConsoleFixture):
    def setUp(self):
        # Keep the request-driven 48-hour pass out of the way; tests call it directly.
        cache.set('leadership_due_check', True, 3600)
        self.client.login(username='admin', password='pw-admin-123')
        self.start = (timezone.now() + timedelta(days=5)).replace(second=0, microsecond=0)
        self.event = make_event('Harvest Fair', self.start, self.start + timedelta(hours=8))
        self.event.coordinators.add(self.staff)
        self.cleaner = Role.objects.create(name='Cleaner', icon='cleaning_services')
        Profile.objects.filter(user=self.vol).update(impact_points=10)
        Profile.objects.filter(user=self.mate).update(impact_points=50)

    def tearDown(self):
        cache.clear()

    def make_slot(self, role=None, hours=(0, 4), qty=3, area=None, event=None):
        event = event or self.event
        return EventRoleSlot.objects.create(
            event=event, role=role or self.cleaner, area=area, required_qty=qty,
            start_time=event.start_date + timedelta(hours=hours[0]), end_time=event.start_date + timedelta(hours=hours[1]),
        )

    def enable_chain(self):
        self.event.chain_of_command = True
        Event.objects.filter(pk=self.event.pk).update(chain_of_command=True)

    def area(self, name):
        from events.models import EventArea
        return EventArea.objects.create(event=self.event, name=name, icon='restaurant')


class ChainOfCommandConsoleTests(ChainOfCommandFixture):
    def roles_page(self):
        return self.client.get(reverse('console_event_roles', args=[self.event.pk]))

    def test_chain_suggested_past_twenty_volunteers_but_always_available(self):
        self.make_slot(qty=5)
        page = self.roles_page()
        self.assertFalse(page.context['suggest_chain'])
        self.assertContains(page, 'Set Up Chain of Command')  # the sidebar button is always there
        self.make_slot(qty=16, hours=(4, 8))
        page = self.roles_page()
        self.assertTrue(page.context['suggest_chain'])
        self.assertContains(page, 'This event needs 21 volunteers.')

    def test_crossing_the_threshold_while_adding_roles_suggests_a_chain(self):
        response = self.client.post(reverse('console_add_role_slots', args=[self.event.pk]), {
            'role': self.cleaner.pk, 'slot_date': timezone.localtime(self.start).date().isoformat(),
            'slot_start': '08:00', 'slot_end': '12:00', 'slot_qty': '25', 'slot_over': '0', 'is_public': 'on',
        }, follow=True)
        self.assertContains(response, 'Consider setting up a chain of command')

    def test_areas_wrap_roles_and_the_same_role_can_be_in_two_areas(self):
        self.client.post(reverse('console_chain_toggle', args=[self.event.pk]), {'enable': '1'})
        self.event.refresh_from_db()
        self.assertTrue(self.event.chain_of_command)
        self.client.post(reverse('console_area_create', args=[self.event.pk]), {'name': 'Kitchen', 'icon': 'restaurant'})
        self.client.post(reverse('console_area_create', args=[self.event.pk]), {'name': 'Kids Zone', 'icon': 'child_care'})
        kitchen, kids = self.event.areas.order_by('pk')

        dialog = self.client.get(reverse('console_add_role_slots', args=[self.event.pk]) + f'?area={kitchen.pk}')
        self.assertContains(dialog, 'Add Role to Kitchen')
        for area in (kitchen, kids):
            self.client.post(reverse('console_add_role_slots', args=[self.event.pk]), {
                'role': self.cleaner.pk, 'area': area.pk, 'slot_date': timezone.localtime(self.start).date().isoformat(),
                'slot_start': '08:00', 'slot_end': '12:00', 'slot_qty': '2', 'slot_over': '0', 'is_public': 'on',
            })
        self.assertEqual(sorted(s.area.name for s in self.event.role_slots.all()), ['Kids Zone', 'Kitchen'])

        page = self.roles_page()
        self.assertContains(page, 'Add role to Kitchen')
        self.assertContains(page, 'Add role to Kids Zone')
        self.assertContains(page, 'Assign area lead')
        self.assertEqual([len(group['staffing']) for group in page.context['areas']], [1, 1])

    def lead_shifts(self, area):
        return EventRoleSlot.objects.filter(area=area, role__system_key=Role.AREA_LEAD).order_by('start_time')

    def test_area_leads_sign_up_for_an_area_lead_shift(self):
        self.enable_chain()
        food = self.area('Food Court')
        self.make_slot(area=food, hours=(0, 4))
        self.make_slot(area=food, hours=(4, 8))
        lead_url = reverse('console_area_lead_create', args=[self.event.pk, food.pk])
        local = lambda h: timezone.localtime(self.start + timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(lead_url, {'user': self.mate.pk, 'start': local(0), 'end': local(4)})
        shift = self.lead_shifts(food).get()
        self.assertEqual(shift.role, Role.area_lead())
        self.assertEqual(list(shift.signups.all()), [self.mate])
        self.assertEqual((shift.start_time, shift.end_time, shift.is_public), (self.start, self.start + timedelta(hours=4), False))
        self.assertTrue(SlotSignup.objects.filter(slot=shift, user=self.mate).exists())
        self.assertTrue(Notification.objects.filter(user=self.mate, message__contains='area lead for Food Court').exists())

        page = self.roles_page()
        group = page.context['areas'][0]
        self.assertEqual([e['role'] for e in group['staffing']], [self.cleaner])  # the lead shift isn't a role card
        self.assertEqual(group['gaps'], [(self.start + timedelta(hours=4), self.start + timedelta(hours=8))])
        self.assertContains(page, 'Assign another area lead')
        self.assertFalse(page.context['lead_positions'] and any(
            s.lead_position for e in group['staffing'] for s in e['slots'] if s.is_area_lead))

        self.client.post(lead_url, {'user': self.vol.pk, 'start': local(4), 'end': local(8)})
        self.assertEqual(self.roles_page().context['areas'][0]['gaps'], [])

        # Swapping the person keeps the shift; removing the lead deletes it.
        edit = reverse('console_area_lead_edit', args=[self.event.pk, food.pk, shift.pk])
        self.client.post(edit, {'user': self.staff.pk, 'start': local(0), 'end': local(4)})
        self.assertEqual(list(self.lead_shifts(food).first().signups.all()), [self.staff])
        self.client.post(reverse('console_area_lead_delete', args=[self.event.pk, food.pk, shift.pk]), **HTMX)
        self.assertEqual(self.lead_shifts(food).count(), 1)

    def test_area_lead_shifts_go_with_their_area(self):
        from events.leadership import assign_area_lead
        self.enable_chain()
        food = self.area('Food Court')
        assign_area_lead(food, self.mate, self.start, self.start + timedelta(hours=4))
        self.client.post(reverse('console_area_delete', args=[self.event.pk, food.pk]), **HTMX)
        self.assertFalse(EventRoleSlot.objects.filter(role__system_key=Role.AREA_LEAD).exists())

    def test_only_the_general_coordinator_assigns_area_leads(self):
        self.enable_chain()
        food = self.area('Food Court')
        User.objects.create_user('other', 'other@example.com', 'pw-other-123', is_staff=True)
        self.client.login(username='other', password='pw-other-123')
        page = self.roles_page()
        self.assertFalse(page.context['can_manage_leads'])
        self.assertContains(page, 'Area lead needed')
        self.client.post(reverse('console_area_lead_create', args=[self.event.pk, food.pk]), {
            'user': self.mate.pk, 'start': '2030-01-01T08:00', 'end': '2030-01-01T12:00'})
        self.assertFalse(self.lead_shifts(food).exists())

    def test_area_lead_role_is_built_in(self):
        role = Role.area_lead()
        self.assertEqual(Role.objects.filter(system_key=Role.AREA_LEAD).count(), 1)  # made by migration
        directory = self.client.get(reverse('console_roles'))
        self.assertNotIn(role, directory.context['roles'])
        self.assertNotContains(self.client.get(reverse('console_add_role_slots', args=[self.event.pk])), 'Area lead')
        self.assertEqual(self.client.get(reverse('console_role_edit', args=[role.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('console_role_delete', args=[role.pk])).status_code, 404)
        with self.assertRaises(ValueError):
            role.delete()
        self.assertTrue(Role.objects.filter(pk=role.pk).exists())

    def test_event_editor_says_general_coordinators(self):
        page = self.client.get(reverse('console_event_edit', args=[self.event.pk]))
        self.assertContains(page, 'General coordinators')
        self.assertContains(page, 'event-pick-group is-coordinators')

    def test_shift_lead_positions(self):
        from events.leadership import has_lead_position
        small, busy = self.make_slot(qty=5), self.make_slot(qty=4, hours=(4, 8))
        busy.allowed_overstaffing_qty = 2
        busy.save()
        self.assertFalse(has_lead_position(small))
        self.assertTrue(has_lead_position(busy))  # more than 5 people can sign up
        self.enable_chain()
        small.event.chain_of_command = True
        self.assertTrue(has_lead_position(small))  # every shift has a lead with a chain of command

    def test_edit_slot_and_pick_its_lead(self):
        slot = self.make_slot(qty=6)
        slot.signups.add(self.vol, self.mate)
        dialog = self.client.get(reverse('console_slot_edit', args=[self.event.pk, slot.pk]))
        self.assertContains(dialog, 'Top pick')
        self.assertEqual(dialog.context['candidates'][0], self.mate)  # most impact points first
        local = lambda h: timezone.localtime(self.start + timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(reverse('console_slot_edit', args=[self.event.pk, slot.pk]), {
            'start': local(1), 'end': local(5), 'qty': '7', 'over': '1', 'is_public': 'on', 'lead': self.vol.pk,
        })
        slot.refresh_from_db()
        self.assertEqual((slot.lead, slot.required_qty, slot.allowed_overstaffing_qty), (self.vol, 7, 1))
        self.assertEqual(slot.start_time, self.start + timedelta(hours=1))
        self.assertFalse(slot.lead_auto_assigned)
        self.assertTrue(Notification.objects.filter(user=self.vol, message__contains='shift lead for Cleaner').exists())
        self.assertContains(self.roles_page(), 'Shift lead')

    def test_slot_cannot_shrink_below_its_signups(self):
        slot = self.make_slot(qty=2)
        slot.signups.add(self.vol, self.mate)
        local = lambda h: timezone.localtime(self.start + timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(reverse('console_slot_edit', args=[self.event.pk, slot.pk]), {
            'start': local(0), 'end': local(4), 'qty': '1', 'over': '0'})
        slot.refresh_from_db()
        self.assertEqual(slot.required_qty, 2)

    def test_leads_are_picked_48_hours_ahead_and_coordinators_reminded(self):
        from events.leadership import run_due_assignments
        slot = self.make_slot(qty=6)
        slot.signups.add(self.vol, self.mate)
        self.assertEqual(run_due_assignments(), 0)  # five days out: still open

        Event.objects.filter(pk=self.event.pk).update(start_date=timezone.now() + timedelta(hours=30))
        self.assertEqual(run_due_assignments(), 1)
        slot.refresh_from_db()
        self.assertEqual(slot.lead, self.mate)
        self.assertTrue(slot.lead_auto_assigned)
        reminder = Notification.objects.filter(user=self.staff, message__contains='picked automatically')
        self.assertEqual(reminder.count(), 1)

        later = self.make_slot(qty=6, hours=(4, 8))
        later.signups.add(self.vol)
        self.assertEqual(run_due_assignments(), 1)
        self.assertEqual(reminder.count(), 1)  # one reminder per event

    def test_management_command(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('assign_shift_leads', stdout=out)
        self.assertIn('shift lead(s) assigned', out.getvalue())

    def test_leads_lock_a_day_ahead_unless_nobody_holds_the_position(self):
        Event.objects.filter(pk=self.event.pk).update(start_date=timezone.now() + timedelta(hours=10))
        self.event.refresh_from_db()
        led, open_slot = self.make_slot(qty=6), self.make_slot(qty=6, hours=(4, 8))
        for slot in (led, open_slot):
            slot.signups.add(self.vol, self.mate)
        EventRoleSlot.objects.filter(pk=led.pk).update(lead=self.vol)
        local = lambda dt: timezone.localtime(dt).strftime('%Y-%m-%dT%H:%M')
        for slot in (led, open_slot):
            slot.refresh_from_db()
            self.client.post(reverse('console_slot_edit', args=[self.event.pk, slot.pk]), {
                'start': local(slot.start_time), 'end': local(slot.end_time), 'qty': '6', 'over': '0', 'lead': self.mate.pk})
        led.refresh_from_db()
        open_slot.refresh_from_db()
        self.assertEqual(led.lead, self.vol)  # locked
        self.assertEqual(open_slot.lead, self.mate)  # empty positions can still be filled

    def test_leaving_a_slot_gives_up_leading_it(self):
        slot = self.make_slot(qty=6)
        slot.signups.add(self.vol, self.mate)
        EventRoleSlot.objects.filter(pk=slot.pk).update(lead=self.mate)
        slot.signups.remove(self.mate)
        slot.refresh_from_db()
        self.assertIsNone(slot.lead)

    def test_removing_the_chain_deletes_areas_but_keeps_roles(self):
        self.enable_chain()
        food = self.area('Food Court')
        slot = self.make_slot(area=food, qty=2)
        slot.signups.add(self.vol)
        EventRoleSlot.objects.filter(pk=slot.pk).update(lead=self.vol)
        self.client.post(reverse('console_chain_toggle', args=[self.event.pk]), {'enable': '0'})
        self.event.refresh_from_db()
        slot.refresh_from_db()
        self.assertFalse(self.event.chain_of_command)
        self.assertFalse(self.event.areas.exists())
        self.assertIsNone(slot.area)
        self.assertIsNone(slot.lead)  # 2 people: no lead position without a chain
        self.assertTrue(slot.signups.filter(pk=self.vol.pk).exists())


class ChainOfCommandVolunteerTests(ChainOfCommandFixture):
    def setUp(self):
        super().setUp()
        self.client.login(username='vol', password='pw-vol-123')

    def page(self):
        return self.client.get(reverse('opportunity_detail', args=[self.event.pk]))

    def test_my_team_lists_the_others_on_my_shift(self):
        slot = self.make_slot(qty=6)
        slot.signups.add(self.vol, self.mate)
        EventRoleSlot.objects.filter(pk=slot.pk).update(lead=self.mate)
        page = self.page()
        self.assertContains(page, 'My Team')
        self.assertNotContains(page, 'My Teams')
        self.assertContains(page, 'Your shift lead')
        self.assertEqual(page.context['teams'][0]['teammates'], [self.mate])

    def test_several_shifts_become_my_teams_accordion(self):
        self.make_slot(hours=(0, 2)).signups.add(self.vol, self.mate)
        self.make_slot(role=self.role, hours=(3, 5)).signups.add(self.vol)
        page = self.page()
        self.assertContains(page, 'My Teams')
        self.assertContains(page, 'class="team-accordion"', count=2)
        self.assertContains(page, 'Nobody else has signed up for this shift yet.')

    def test_no_team_card_before_signing_up(self):
        self.make_slot()
        self.assertNotContains(self.page(), 'My Team')

    def test_area_leads_join_the_coordinators_and_others_point_to_them(self):
        from events.leadership import assign_area_lead
        self.enable_chain()
        kitchen = self.area('Kitchen')
        slot = self.make_slot(area=kitchen, hours=(0, 4))
        slot.signups.add(self.vol)
        lead_user = User.objects.create_user('lena', 'lena@example.com', 'pw', first_name='Lena')
        assign_area_lead(kitchen, lead_user, self.start, self.start + timedelta(hours=8))
        stage = self.area('Stage')
        other = User.objects.create_user('omar', 'omar@example.com', 'pw', first_name='Omar')
        assign_area_lead(stage, other, self.start, self.start + timedelta(hours=8))

        page = self.page()
        self.assertContains(page, 'Event Coordinators')
        self.assertContains(page, 'General coordinator')
        self.assertContains(page, 'Kitchen lead')
        self.assertContains(page, 'Your lead')
        entries = {e['user'].username: e for e in page.context['coordinators']}
        self.assertFalse(entries['lena']['redirect'])
        self.assertTrue(entries['admin']['redirect'])
        self.assertTrue(entries['omar']['redirect'])
        self.assertContains(page, 'Try your lead first')
        self.assertContains(page, 'data-lead-check data-name', count=2)

    def test_area_lead_sees_their_shift_leads_and_is_not_offered_the_role(self):
        from events.leadership import assign_area_lead
        self.enable_chain()
        kitchen = self.area('Kitchen')
        slot = self.make_slot(area=kitchen, hours=(0, 4), qty=6)
        slot.signups.add(self.mate)
        EventRoleSlot.objects.filter(pk=slot.pk).update(lead=self.mate)
        assign_area_lead(kitchen, self.vol, self.start, self.start + timedelta(hours=8))
        page = self.page()
        self.assertContains(page, 'You lead Kitchen.')
        self.assertEqual(page.context['teams'][0]['shift_leads'], [EventRoleSlot.objects.get(pk=slot.pk)])
        self.assertEqual([s.role for s in page.context['role_slots']], [self.cleaner])
        self.assertContains(self.client.get(reverse('my_events')), 'Harvest Fair')

    def test_no_redirect_without_an_area_lead(self):
        self.make_slot().signups.add(self.vol)
        page = self.page()
        self.assertNotContains(page, 'data-lead-check data-name')
        self.assertNotContains(page, 'Try your lead first')


@override_settings(BELLTOWER_RUN_INLINE=True)
class ChainOfCommandBellTowerTests(ChainOfCommandFixture):
    def setUp(self):
        super().setUp()
        self.fake = FakeBellTower()
        patcher = patch('base.belltower._http', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        site = SiteSettings.get_settings()
        site.belltower_url, site.belltower_api_key, site.belltower_username = FakeBellTower.BASE, 'bt_secret', 'townhall'
        site.belltower_endpoints = self.fake('GET', FakeBellTower.BASE + '/.well-known/belltower')
        site.save()
        self.enable_chain()
        self.kitchen, self.kids = self.area('Kitchen'), self.area('Kids Zone')

    def add_slot(self, area, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return self.make_slot(area=area, **kwargs)

    def remote(self, area):
        task_list = EventTaskList.objects.get(event=self.event, roles=self.cleaner, area=area)
        return task_list, self.fake.lists[task_list.belltower_id]

    def test_each_area_gets_its_own_role_list(self):
        self.add_slot(self.kitchen)
        self.add_slot(self.kids)
        kitchen, kitchen_remote = self.remote(self.kitchen)
        kids, _ = self.remote(self.kids)
        self.assertNotEqual(kitchen.pk, kids.pk)
        self.assertEqual(kitchen.name, 'Cleaner tasks · Kitchen')
        self.assertEqual(kitchen_remote['name'], 'Harvest Fair: Cleaner tasks · Kitchen')

    def test_shift_and_area_leads_are_list_admins(self):
        from events.leadership import set_slot_lead
        slot = self.add_slot(self.kitchen, qty=6)
        with self.captureOnCommitCallbacks(execute=True):
            slot.signups.add(self.vol, self.mate)
        with self.captureOnCommitCallbacks(execute=True):
            set_slot_lead(slot, self.vol)
        _, remote = self.remote(self.kitchen)
        self.assertIn('carol', remote['admins'])  # vol's Bell Tower account
        with self.captureOnCommitCallbacks(execute=True):
            set_slot_lead(slot, self.mate)
        _, remote = self.remote(self.kitchen)
        self.assertIn('mate', remote['admins'])
        self.assertNotIn('carol', remote['admins'])
        self.assertIn('carol', remote['users'])  # still on the team

        local = lambda h: timezone.localtime(self.start + timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M')
        lead = User.objects.create_user('lena', 'lena@example.com', 'pw')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('console_area_lead_create', args=[self.event.pk, self.kitchen.pk]),
                             {'user': lead.pk, 'start': local(0), 'end': local(8)})
        _, remote = self.remote(self.kitchen)
        self.assertIn('lena', remote['admins'])
        self.assertFalse(EventTaskList.objects.filter(roles__system_key=Role.AREA_LEAD).exists())  # no list of its own

        shift = EventRoleSlot.objects.get(area=self.kitchen, role__system_key=Role.AREA_LEAD)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('console_area_lead_delete', args=[self.event.pk, self.kitchen.pk, shift.pk]), **HTMX)
        _, remote = self.remote(self.kitchen)
        self.assertNotIn('lena', remote['users'])

    def test_lists_from_different_areas_dont_merge(self):
        self.add_slot(self.kitchen)
        self.add_slot(self.kids)
        kitchen, _ = self.remote(self.kitchen)
        kids, _ = self.remote(self.kids)
        response = self.client.post(reverse('console_task_list_merge', args=[self.event.pk, kitchen.pk]),
                                    {'source': kids.pk}, **HTMX)
        self.assertContains(response, 'different areas')
        self.assertTrue(EventTaskList.objects.filter(pk=kids.pk).exists())
