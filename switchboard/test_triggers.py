"""The events Town Hall sends Switchboard beyond the first seven (those are in tests.py):
each from the thing that really happens, once, and the ones that depend on the clock.

`SwitchboardTestCase` holds every envelope a test produces to the manifest: declared types,
no undeclared fields, every declared field present, the person's email on the subject.
"""
import pathlib
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from base.models import Endorsement, PointsEntry, Venue, VenueNote
from base.points import award_profile
from console.views.events import send_invites
from education.models import (ExternalCertificate, Quiz, Skill, TrainingModule, TrainingModuleCompletion,
                              UserCertification)
from events import leadership
from events.models import Event, EventArea, EventFeedback, EventRoleSlot, EventSlotInvite
from jobs.models import Role, Shift

from . import events, manifest, timed
from .models import Delivery, EmittedOnce, SwitchboardConnection
from .tests import SwitchboardTestCase


class CoverageTests(SwitchboardTestCase):
    def test_every_trigger_and_action_is_named_in_a_test(self):
        """Adding to the manifest without a test fails here."""
        folder = pathlib.Path(__file__).parent
        source = ''.join(path.read_text() for path in sorted(folder.glob('test*.py')))
        for name in list(manifest.TRIGGERS_BY_TYPE) + list(manifest.ACTIONS_BY_NAME):
            self.assertIn(f"'{name}'", source, f'{name} has no test')

    def test_no_field_is_listed_twice(self):
        for where, fields in ([(t['type'], t['fields']) for t in manifest.TRIGGERS]
                              + [(a['name'], a['output']) for a in manifest.ACTIONS]
                              + [(a['name'], a['params']) for a in manifest.ACTIONS]):
            keys = [f['key'] for f in fields]
            self.assertEqual(len(keys), len(set(keys)), where)

    def test_events_about_a_person_declare_their_email(self):
        for trigger in manifest.TRIGGERS:
            keys = {f['key'] for f in trigger['fields']}
            if 'person.id' in keys:
                self.assertLessEqual({'person.name', 'person.email'}, keys, trigger['type'])


class PeopleTriggerTests(SwitchboardTestCase):
    def setUp(self):
        super().setUp()
        self.connect()

    def test_deactivated_reactivated_and_email_changed(self):
        with self.committing():
            self.volunteer.is_active = False
            self.volunteer.save(update_fields=['is_active'])
        self.assertEqual(self.one('volunteer.deactivated')['data']['person']['email'], 'Maria@example.org')
        with self.committing():
            self.volunteer.is_active = True
            self.volunteer.email = 'maria.k@example.org'
            self.volunteer.save()
        self.one('volunteer.reactivated')
        changed = self.one('volunteer.email_changed')
        self.assertEqual((changed['data']['previous_email'], changed['subject']['email']),
                         ('Maria@example.org', 'maria.k@example.org'))
        with self.committing():
            self.volunteer.email = 'MARIA.K@example.org'      # the same address: people are matched ignoring case
            self.volunteer.first_name = 'Mariya'
            self.volunteer.save()
            self.volunteer.last_login = timezone.now()        # signing in saves only this
            self.volunteer.save(update_fields=['last_login'])
        self.assertEqual(len(self.fake.requests), 3)

    def test_permanent_role_assigned_and_unassigned(self):
        librarian = Role.objects.create(name='Librarian', icon='x', permanent=True)
        with self.committing():
            self.volunteer.profile.permanent_roles.set([librarian])
            self.volunteer.profile.permanent_roles.set([librarian])   # nothing new
        assigned = self.one('volunteer.role_assigned')
        self.assertEqual(assigned['data']['role'], {'id': str(librarian.pk), 'name': 'Librarian'})
        self.assertEqual(assigned['subject']['id'], str(self.volunteer.pk))
        with self.committing():
            librarian.staff.remove(self.volunteer.profile)            # from the role's side
        self.assertEqual(self.one('volunteer.role_unassigned')['data']['person']['email'], 'Maria@example.org')

    def test_points_awarded_for_each_new_ledger_line(self):
        with self.committing():
            entry = PointsEntry.objects.create(user=self.volunteer, amount=-20, source=PointsEntry.ADJUSTMENT,
                                               reason='Correction after the bazaar')
            entry.reason = 'Correction'
            entry.save()   # an edited line is not new points
        envelope = self.one('points.awarded')
        self.assertEqual({k: v for k, v in envelope['data'].items() if k != 'person'}, {
            'points': -20, 'source': 'adjustment', 'reason': 'Correction after the bazaar', 'impact_points': -20})

    def test_a_shift_gives_points_once_however_often_it_is_recalculated(self):
        _event, slot = self.event_with_slot()
        with self.committing():
            shift = Shift.objects.create(user=self.volunteer, event_role_slot=slot)
            Shift.objects.filter(pk=shift.pk).update(start_time=timezone.now() - timedelta(hours=2))
            shift.refresh_from_db()
            shift.end_shift()
            call_command('recalculate_points', verbosity=0)
        envelope = self.one('points.awarded')
        self.assertEqual((envelope['data']['source'], envelope['data']['reason']), ('shift', 'Greeter at Christmas Bazaar'))
        self.assertEqual(envelope['data']['points'], self.one('shift.completed')['data']['points'])
        self.assertEqual(envelope['occurred_at'], events.iso(shift.end_time))

    def test_profile_completed_once(self):
        profile = self.volunteer.profile
        profile.bio, profile.location, profile.use_gravatar = 'Hello', 'New Westminster', True
        profile.save()
        with self.committing():
            self.assertIsNone(award_profile(profile))   # no skills yet
        self.assertEqual(self.fake.of_type('volunteer.profile_completed'), [])
        profile.skills.add(Skill.objects.create(name='Cooking'))
        with self.committing():
            entry = award_profile(profile)
            award_profile(profile)
        self.assertEqual(self.one('volunteer.profile_completed')['data']['points'], entry.amount)
        self.assertEqual(self.one('points.awarded')['data']['source'], 'profile')

    def test_endorsement_given_for_the_skills_added(self):
        event, slot = self.event_with_slot()
        teamwork, cooking = Skill.objects.create(name='Teamwork'), Skill.objects.create(name='Cooking')
        with self.committing():
            endorsement = Endorsement.give(self.staff, self.volunteer, [teamwork], text='Calm all day.', event=event)
        envelope = self.one('endorsement.given')
        self.assertEqual(envelope['subject']['email'], 'Maria@example.org')
        self.assertEqual(envelope['data']['endorser'], {'id': str(self.staff.pk), 'name': 'Sam Staff', 'email': 'staff@example.org'})
        self.assertEqual((envelope['data']['skills'], envelope['data']['message']), ('Teamwork', 'Calm all day.'))
        self.assertEqual(envelope['data']['event'], {'id': str(event.pk), 'title': 'Christmas Bazaar'})
        with self.committing():
            endorsement.skills.add(cooking)    # the one-click chip adds to the same endorsement
            endorsement.skills.add(cooking)
        self.assertEqual([e['data']['skills'] for e in self.fake.of_type('endorsement.given')], ['Teamwork', 'Cooking'])

    def test_endorsement_without_an_event(self):
        with self.committing():
            Endorsement.give(self.staff, self.volunteer, [Skill.objects.create(name='Teamwork')])
        self.assertEqual(self.one('endorsement.given')['data']['event'], {'id': '', 'title': ''})


class ShiftTriggerTests(SwitchboardTestCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.event, self.slot = self.event_with_slot()
        self.petro = self.person('petro')

    def test_filled_when_the_last_needed_volunteer_signs_up(self):
        self.slot.allowed_overstaffing_qty = 1
        self.slot.save()
        with self.committing():
            self.slot.add_signup(self.volunteer)
        self.assertEqual(self.fake.of_type('shift.filled'), [])
        with self.committing():
            self.slot.add_signup(self.petro)
            self.slot.add_signup(self.person('ivan'))   # one over what it needs: still filled, not filled again
        filled = self.one('shift.filled')
        self.assertEqual(filled['subject'], {'type': 'shift', 'id': str(self.slot.pk), 'name': 'Greeter at Christmas Bazaar'})
        self.assertEqual((filled['data']['shift']['taken'], filled['data']['shift']['open_spots']), (2, 0))
        with self.committing():
            self.slot.signups.remove(self.petro, self.volunteer)
            self.petro.commitments.add(self.slot)        # filled a second time is a second event
        self.assertEqual(len(self.fake.of_type('shift.filled')), 2)

    def test_cancelling_close_to_the_shift_is_late(self):
        Event.objects.filter(pk=self.event.pk).update(start_date=timezone.now() + timedelta(hours=5))
        EventRoleSlot.objects.filter(pk=self.slot.pk).update(start_time=timezone.now() + timedelta(hours=5),
                                                             end_time=timezone.now() + timedelta(hours=9))
        self.slot.refresh_from_db()
        self.slot.add_signup(self.volunteer)
        with self.committing():
            self.slot.signups.remove(self.volunteer)
        self.assertIs(self.one('shift.cancelled')['data']['late'], True)

    def test_shift_lead_assigned(self):
        self.slot.add_signup(self.volunteer)
        self.slot.add_signup(self.petro)
        with self.committing():
            leadership.set_slot_lead(self.slot, self.volunteer)
            leadership.set_slot_lead(self.slot, self.volunteer)   # still her
            self.slot.required_qty = 3
            self.slot.save()                                      # an unrelated edit
        envelope = self.one('shift.lead_assigned')
        self.assertEqual((envelope['data']['person']['email'], envelope['data']['automatic']), ('Maria@example.org', False))
        self.assertEqual(envelope['data']['shift']['id'], str(self.slot.pk))
        with self.committing():
            leadership.set_slot_lead(self.slot, self.petro, auto=True)
            leadership.set_slot_lead(self.slot, None)
        self.assertEqual([e['data']['automatic'] for e in self.fake.of_type('shift.lead_assigned')], [False, True])

    def test_area_lead_assigned(self):
        self.event.chain_of_command = True
        self.event.save()
        area = EventArea.objects.create(event=self.event, name='Kitchen')
        with self.committing():
            lead_slot = leadership.assign_area_lead(area, self.volunteer, self.slot.start_time, self.slot.end_time)
        envelope = self.one('area.lead_assigned')
        self.assertEqual(envelope['data']['area'], {'id': str(area.pk), 'name': 'Kitchen'})
        self.assertEqual((envelope['data']['shift']['id'], envelope['data']['shift']['area']), (str(lead_slot.pk), 'Kitchen'))
        self.assertEqual(self.one('shift.signed_up')['data']['shift']['role'], 'Area lead')
        self.assertEqual(self.fake.of_type('shift.filled'), [])   # a lead's own shift is not a volunteer shift

    def test_started_at_an_event_and_in_a_permanent_role(self):
        with self.committing():
            shift = Shift.objects.create(user=self.volunteer, event_role_slot=self.slot, clutched=True)
        envelope = self.one('shift.started')
        self.assertEqual(envelope['data']['shift'], {'id': str(self.slot.pk), 'role': 'Greeter',
                                                     'started_at': events.iso(shift.start_time)})
        self.assertEqual(envelope['data']['event'], {'id': str(self.event.pk), 'title': 'Christmas Bazaar', 'place': 'Parish Hall'})
        self.assertIs(envelope['data']['walk_in'], True)
        with self.committing():
            shift.end_shift()   # ending it is not another check-in
            Shift.objects.create(user=self.petro, role=Role.objects.create(name='Librarian', icon='x', permanent=True))
            Shift.objects.create(user=self.petro, event_role_slot=self.slot, end_time=timezone.now() + timedelta(hours=1))
        permanent = self.fake.of_type('shift.started')[1]
        self.assertEqual(len(self.fake.of_type('shift.started')), 2)
        self.assertEqual((permanent['data']['shift']['id'], permanent['data']['shift']['role'], permanent['data']['walk_in']),
                         ('', 'Librarian', False))
        self.assertEqual(permanent['data']['event'], {'id': '', 'title': '', 'place': ''})


class InviteTriggerTests(SwitchboardTestCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.event, self.slot = self.event_with_slot(is_public=False, required_qty=1)

    def invite(self, user):
        with self.committing():
            send_invites(self.slot, [user], 'Join us', 'Hello [Name]', lambda path: 'https://volunteers.example.org' + path)
        return EventSlotInvite.objects.get(event_role_slot=self.slot, user=user)

    def respond(self, invite, user, response):
        self.client.force_login(user)
        with self.committing():
            return self.client.post(reverse('respond_to_invite', args=[invite.token]), {'response': response})

    def test_sent_then_accepted(self):
        invite = self.invite(self.volunteer)
        sent = self.one('invite.sent')
        self.assertEqual((sent['data']['person']['email'], sent['data']['shift']['id']), ('Maria@example.org', str(self.slot.pk)))
        self.invite(self.volunteer)   # sending it again reuses the invitation
        self.one('invite.sent')
        self.respond(invite, self.volunteer, 'accept')
        self.assertEqual(self.one('invite.accepted')['data']['shift']['taken'], 1)
        self.one('shift.signed_up')
        self.assertEqual(self.fake.of_type('invite.declined'), [])

    def test_declined(self):
        invite = self.invite(self.volunteer)
        self.respond(invite, self.volunteer, 'decline')
        declined = self.one('invite.declined')
        self.assertEqual((declined['subject']['email'], declined['data']['event']['title']), ('Maria@example.org', 'Christmas Bazaar'))
        self.assertFalse(EventSlotInvite.objects.exists())
        self.assertEqual(self.fake.of_type('invite.accepted'), [])

    def test_an_invitation_cleared_away_is_not_a_decline(self):
        self.invite(self.volunteer)
        with self.committing():
            self.slot.invites.all().delete()   # what Event.save() does once the event is over
        self.assertEqual(self.fake.of_type('invite.declined'), [])

    def test_answering_after_the_shift_filled_is_not_an_acceptance(self):
        invite = self.invite(self.volunteer)
        self.slot.add_signup(self.person('petro'))
        self.respond(invite, self.volunteer, 'accept')
        invite.refresh_from_db()
        self.assertTrue(invite.accepted)   # the view closes the invitation either way
        self.assertFalse(self.slot.user_signed_up(self.volunteer))
        self.assertEqual(self.fake.of_type('invite.accepted'), [])

    def test_declining_never_breaks_the_page(self):
        invite = self.invite(self.volunteer)
        with patch('switchboard.events.emit', side_effect=RuntimeError('boom')), self.assertLogs('switchboard', 'ERROR'):
            response = self.respond(invite, self.volunteer, 'decline')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(EventSlotInvite.objects.exists())


class EventTriggerTests(SwitchboardTestCase):
    def setUp(self):
        super().setUp()
        self.connect()

    def test_created_published_rescheduled_and_hidden(self):
        with self.committing():
            event, _slot = self.event_with_slot(published=False)
        created = self.one('event.created')
        self.assertEqual(created['subject'], {'type': 'event', 'id': str(event.pk), 'name': 'Christmas Bazaar'})
        self.assertEqual(created['data']['event'], {
            'id': str(event.pk), 'title': 'Christmas Bazaar', 'place': 'Parish Hall', 'published': False,
            'starts_at': events.iso(event.start_date), 'ends_at': events.iso(event.end_date),
            'url': f'https://volunteers.example.org/en/opportunities/{event.pk}/'})
        self.assertEqual(self.fake.of_type('event.published'), [])

        was_start, was_end = event.start_date, event.end_date
        with self.committing():
            event.title = 'Christmas Bazaar 2026'
            event.save()                                  # not a move
            event.start_date, event.end_date = was_start + timedelta(days=1), was_end + timedelta(days=1)
            event.save()
        moved = self.one('event.rescheduled')
        self.assertEqual((moved['data']['previous_starts_at'], moved['data']['previous_ends_at']),
                         (events.iso(was_start), events.iso(was_end)))
        self.assertEqual((moved['data']['event']['starts_at'], moved['data']['event']['published']),
                         (events.iso(event.start_date), False))

        with self.committing():
            event.published = True
            event.save(update_fields=['published'])
            event.published = False
            event.save(update_fields=['published'])
            event.save()
        self.one('event.published')
        self.assertEqual(self.one('event.unpublished')['data']['event']['title'], 'Christmas Bazaar 2026')
        self.one('event.created')
        self.one('event.rescheduled')

    def test_an_event_created_published_is_both(self):
        with self.committing():
            self.event_with_slot(published=True)
        self.assertIs(self.one('event.created')['data']['event']['published'], True)
        self.one('event.published')

    def test_coordinator_added(self):
        event, _slot = self.event_with_slot()
        with self.committing():
            event.coordinators.add(self.staff)
            event.coordinators.add(self.staff)
            event.coordinators.remove(self.staff)
        envelope = self.one('event.coordinator_added')
        self.assertEqual((envelope['subject']['email'], envelope['data']['event']['id']), ('staff@example.org', str(event.pk)))
        self.assertEqual(envelope['data']['event']['url'], f'https://volunteers.example.org/en/opportunities/{event.pk}/')

    def test_feedback_submitted_the_first_time(self):
        event, _slot = self.event_with_slot()
        with self.committing():
            EventFeedback.objects.update_or_create(event=event, user=self.volunteer, defaults={
                'rating': 5, 'enjoyed': 'Great team.', 'suggestions': ''})
            EventFeedback.objects.update_or_create(event=event, user=self.volunteer, defaults={'rating': 4})
        envelope = self.one('event.feedback_submitted')
        self.assertEqual({k: v for k, v in envelope['data'].items() if k != 'person'}, {
            'event': {'id': str(event.pk), 'title': 'Christmas Bazaar'}, 'rating': 5, 'enjoyed': 'Great team.', 'suggestions': ''})

    def test_venue_note_posted(self):
        venue = Venue.objects.create(name='Parish Hall', latitude=1, longitude=1)
        with self.committing():
            note = VenueNote.objects.create(venue=venue, text='Side entrance closed.', expires_after=timedelta(days=3))
            note.text = 'Side entrance closed this week.'
            note.save()
        envelope = self.one('venue.note_posted')
        self.assertEqual(envelope['subject'], {'type': 'venue', 'id': str(venue.pk), 'name': 'Parish Hall'})
        self.assertEqual(envelope['data'], {'venue': {'id': str(venue.pk), 'name': 'Parish Hall'},
                                            'note': 'Side entrance closed.', 'expires_at': events.iso(note.expires_at)})


class TrainingTriggerTests(SwitchboardTestCase):
    def setUp(self):
        super().setUp()
        self.connect()

    def test_module_published(self):
        with self.committing():
            module = TrainingModule.objects.create(title='Food Safety Basics', description='x', icon='x')
            module.title = 'Food Safety'
            module.save()
            TrainingModule.objects.create(title='Admin guide', description='x', icon='x', staff_only=True, published=True)
        self.assertEqual(self.fake.of_type('training.published'), [])
        with self.committing():
            module.published = True
            module.save()
            module.save()
        envelope = self.one('training.published')
        self.assertEqual(envelope['subject'], {'type': 'training_module', 'id': str(module.pk), 'name': 'Food Safety'})
        self.assertEqual(envelope['data']['module'], {
            'id': str(module.pk), 'title': 'Food Safety',
            'url': f'https://volunteers.example.org/en/training/module/{module.pk}/'})
        with self.committing():
            TrainingModule.objects.create(title='Welcome Desk', description='x', icon='x', published=True)
        self.assertEqual(len(self.fake.of_type('training.published')), 2)

    def test_started_and_quiz_passed_once_each(self):
        module = TrainingModule.objects.create(title='Food Safety Basics', description='x', icon='x', published=True)
        guide = TrainingModule.objects.create(title='Admin guide', description='x', icon='x', staff_only=True)
        quiz = Quiz.objects.create(training_module=module, title='Food Safety Quiz')
        with self.committing():
            for _again in range(2):
                module.started_by.add(self.volunteer)
                quiz.completed_by.add(self.volunteer)
            guide.started_by.add(self.staff)
            Quiz.objects.create(training_module=guide, title='Guide quiz').completed_by.add(self.staff)
        self.assertEqual(self.one('training.started')['data']['module'], {'id': str(module.pk), 'title': 'Food Safety Basics'})
        passed = self.one('training.quiz_passed')
        self.assertEqual(passed['data']['quiz'], {'id': str(quiz.pk), 'title': 'Food Safety Quiz'})
        self.assertEqual(passed['data']['person']['email'], 'Maria@example.org')

    def test_certificate_submitted_approved_rejected_and_sent_again(self):
        first_aid = ExternalCertificate.objects.create(name='First Aid', description='x')
        with self.committing():
            held = UserCertification.objects.create(user=self.volunteer, certificate=first_aid)
            held.admin_notes = 'Looks fine'
            held.save(update_fields=['admin_notes'])
        submitted = self.one('certificate.submitted')
        self.assertEqual((submitted['data']['certificate'], submitted['data']['resubmitted']),
                         ({'id': str(first_aid.pk), 'name': 'First Aid'}, False))

        with self.committing():
            held.verified, held.rejected, held.admin_notes = False, True, 'The scan is unreadable.'
            held.save()
        self.assertEqual(self.one('certificate.rejected')['data']['notes'], 'The scan is unreadable.')
        with self.committing():
            held.rejected = False
            held.save(update_fields=['rejected'])    # what uploading again does
        self.assertEqual([e['data']['resubmitted'] for e in self.fake.of_type('certificate.submitted')], [False, True])

        expires = timezone.now() + timedelta(days=365)
        with self.committing():
            held.verified, held.expiration_date = True, expires
            held.save()
            held.save()
        approved = self.one('certificate.approved')
        self.assertEqual((approved['data']['expires_at'], approved['subject']['email']), (events.iso(expires), 'Maria@example.org'))

    def test_certificate_approved_without_an_expiry(self):
        first_aid = ExternalCertificate.objects.create(name='First Aid', description='x')
        with self.committing():
            UserCertification.objects.create(user=self.volunteer, certificate=first_aid, verified=True)
        self.assertIsNone(self.one('certificate.approved')['data']['expires_at'])
        self.assertEqual(self.fake.of_type('certificate.submitted'), [])


class TimedTests(SwitchboardTestCase):
    """switchboard/timed.py: what comes due as time passes, each exactly once."""

    def setUp(self):
        super().setUp()
        self.connection, _key = self.connect()
        self.now = timezone.now()
        # Connected a while ago: what happened before a Switchboard was connected is never reported.
        SwitchboardConnection.objects.update(created_at=self.now - timedelta(days=30))

    def run_due(self, at=None, times=2):
        """Run the pass (twice by default, since a second pass must add nothing). Returns what the first sent."""
        with self.committing():
            sent = timed.run_due(at or self.now)
            for _again in range(times - 1):
                self.assertEqual(timed.run_due(at or self.now), 0)
        return sent

    def test_event_starting_soon_started_and_ended(self):
        event, slot = self.event_with_slot(starts_in=timedelta(hours=30))
        slot.add_signup(self.volunteer)
        self.run_due()   # 30 hours ahead: only its short-staffed shift is worth a word
        self.assertEqual({e['type'] for e in self.fake.envelopes if e['type'] != 'shift.signed_up'}, {'shift.understaffed'})

        self.run_due(self.now + timedelta(hours=10))
        soon = self.one('event.starting_soon')
        self.assertEqual({k: v for k, v in soon['data'].items() if k != 'event'}, {
            'volunteers_needed': 2, 'volunteers_signed_up': 1, 'open_spots': 1, 'hours_until': 20.0})
        self.assertEqual(soon['data']['event']['url'], f'https://volunteers.example.org/en/opportunities/{event.pk}/')
        self.assertEqual(self.fake.of_type('event.started'), [])

        with self.committing():
            shift = Shift.objects.create(user=self.volunteer, event_role_slot=slot)
        Shift.objects.filter(pk=shift.pk).update(start_time=event.start_date, end_time=event.start_date + timedelta(hours=4))
        self.run_due(event.start_date + timedelta(minutes=5))
        started = self.one('event.started')
        self.assertEqual((started['occurred_at'], started['data']['volunteers_signed_up']), (events.iso(event.start_date), 1))
        self.assertEqual(self.fake.of_type('event.ended'), [])

        self.run_due(event.end_date + timedelta(minutes=5))
        ended = self.one('event.ended')
        self.assertEqual({k: v for k, v in ended['data'].items() if k != 'event'},
                         {'volunteers': 1, 'hours': 4.0, 'volunteers_signed_up': 1})
        self.assertEqual(ended['occurred_at'], events.iso(event.end_date))
        self.one('event.starting_soon')
        self.one('event.started')

    def test_a_moved_event_is_announced_again(self):
        event, _slot = self.event_with_slot(starts_in=timedelta(hours=10))
        self.run_due()
        Event.objects.filter(pk=event.pk).update(start_date=event.start_date + timedelta(hours=3))
        self.run_due()
        self.assertEqual(len(self.fake.of_type('event.starting_soon')), 2)

    def test_hidden_events_are_left_out(self):
        _event, slot = self.event_with_slot(published=False, starts_in=timedelta(hours=10))
        slot.add_signup(self.volunteer)
        self.assertEqual(self.run_due(), 0)
        self.assertEqual(self.run_due(self.now + timedelta(days=2)), 0)

    def test_shift_understaffed_48_hours_ahead(self):
        _event, slot = self.event_with_slot(starts_in=timedelta(hours=60))
        slot.add_signup(self.volunteer)
        self.assertEqual(self.run_due(), 0)
        self.run_due(self.now + timedelta(hours=20))
        envelope = self.one('shift.understaffed')
        self.assertEqual(envelope['subject']['type'], 'shift')
        self.assertEqual((envelope['data']['shift']['open_spots'], envelope['data']['hours_until']), (1, 40.0))

    def test_a_full_shift_is_not_understaffed(self):
        _event, slot = self.event_with_slot(starts_in=timedelta(hours=30))
        slot.add_signup(self.volunteer)
        slot.add_signup(self.person('petro'))
        self.run_due(times=1)
        self.assertEqual(self.fake.of_type('shift.understaffed'), [])

    def test_shift_starting_soon_for_each_volunteer(self):
        _event, slot = self.event_with_slot(starts_in=timedelta(hours=20))
        slot.add_signup(self.volunteer)
        gone = self.person('gone', is_active=False)
        slot.signups.add(gone)
        self.run_due()
        envelope = self.one('shift.starting_soon')
        self.assertEqual((envelope['data']['person']['email'], envelope['data']['hours_until']), ('Maria@example.org', 20.0))
        slot.add_signup(self.person('petro'))     # signs up later: told on the next pass
        self.run_due()
        self.assertEqual([e['subject']['email'] for e in self.fake.of_type('shift.starting_soon')],
                         ['Maria@example.org', 'petro@example.org'])

    def test_no_show_follows_the_reliability_rule(self):
        _event, slot = self.event_with_slot(starts_in=timedelta(hours=1))
        petro, ivan = self.person('petro'), self.person('ivan')
        for person in (self.volunteer, petro, ivan):
            slot.signups.add(person)
        on_time = Shift.objects.create(user=petro, event_role_slot=slot)
        late = Shift.objects.create(user=ivan, event_role_slot=slot)
        Shift.objects.filter(pk=on_time.pk).update(start_time=slot.start_time + timedelta(minutes=20))
        Shift.objects.filter(pk=late.pk).update(start_time=slot.start_time + timedelta(hours=3))
        self.fake.requests.clear()
        self.assertEqual(self.fake.of_type('shift.no_show'), [])

        self.run_due(slot.end_time + timedelta(minutes=10))
        missed = {e['subject']['email']: e for e in self.fake.of_type('shift.no_show')}
        self.assertEqual(set(missed), {'Maria@example.org', 'ivan@example.org'})
        self.assertIs(missed['Maria@example.org']['data']['checked_in_late'], False)
        self.assertIs(missed['ivan@example.org']['data']['checked_in_late'], True)
        self.assertEqual(missed['ivan@example.org']['occurred_at'], events.iso(slot.end_time))

    def test_training_expired(self):
        yearly = TrainingModule.objects.create(title='Food Safety Basics', description='x', icon='x', expires_after_days=365)
        forever = TrainingModule.objects.create(title='Welcome Desk', description='x', icon='x')
        done = TrainingModuleCompletion.objects.create(training_module=yearly, user=self.volunteer)
        TrainingModuleCompletion.objects.create(training_module=forever, user=self.volunteer)
        completed_at = self.now - timedelta(days=364)
        TrainingModuleCompletion.objects.update(completed_at=completed_at)
        self.fake.requests.clear()
        self.assertEqual(self.run_due(), 0)
        self.run_due(self.now + timedelta(days=2))
        envelope = self.one('training.expired')
        self.assertEqual(envelope['data']['module'], {'id': str(yearly.pk), 'title': 'Food Safety Basics'})
        self.assertEqual((envelope['data']['completed_at'], envelope['data']['expired_at']),
                         (events.iso(completed_at), events.iso(completed_at + timedelta(days=365))))
        self.assertTrue(done.pk)

    def test_certificate_expiring_then_expired(self):
        first_aid = ExternalCertificate.objects.create(name='First Aid', description='x')
        expires = self.now + timedelta(days=45)
        UserCertification.objects.create(user=self.volunteer, certificate=first_aid, verified=True, expiration_date=expires)
        UserCertification.objects.create(user=self.person('petro'), certificate=first_aid, expiration_date=expires)  # never verified
        self.fake.requests.clear()
        self.assertEqual(self.run_due(), 0)
        self.run_due(self.now + timedelta(days=20))
        expiring = self.one('certificate.expiring')
        self.assertEqual({k: v for k, v in expiring['data'].items() if k != 'person'}, {
            'certificate': {'id': str(first_aid.pk), 'name': 'First Aid'}, 'expires_at': events.iso(expires), 'days_left': 25})
        self.assertEqual(self.fake.of_type('certificate.expired'), [])
        self.run_due(self.now + timedelta(days=46))
        expired = self.one('certificate.expired')
        self.assertEqual((expired['data']['expires_at'], expired['occurred_at']), (events.iso(expires), events.iso(expires)))
        self.one('certificate.expiring')

    def test_nothing_from_before_the_connection_or_long_ago(self):
        event, slot = self.event_with_slot(starts_in=timedelta(hours=-30))   # ended about a day ago
        slot.signups.add(self.volunteer)
        first_aid = ExternalCertificate.objects.create(name='First Aid', description='x')
        UserCertification.objects.create(user=self.volunteer, certificate=first_aid, verified=True,
                                         expiration_date=self.now - timedelta(days=2))
        self.fake.requests.clear()
        SwitchboardConnection.objects.update(created_at=self.now - timedelta(hours=1))
        self.assertEqual(self.run_due(), 0)
        # Connected all along, but the pass didn't run for longer than it looks back.
        SwitchboardConnection.objects.update(created_at=self.now - timedelta(days=90))
        self.assertEqual(self.run_due(self.now + timedelta(days=9)), 0)
        self.assertFalse(EmittedOnce.objects.exists())
        self.assertTrue(event.is_past)

    def test_nothing_without_a_connection(self):
        self.event_with_slot(starts_in=timedelta(hours=10))
        SwitchboardConnection.objects.all().delete()
        self.assertEqual(self.run_due(), 0)
        self.assertFalse(EmittedOnce.objects.exists())

    def test_a_second_worker_is_turned_away(self):
        event, _slot = self.event_with_slot(starts_in=timedelta(hours=10))
        EmittedOnce.objects.create(kind='event.starting_soon', key=f'{event.pk}:{events.iso(event.start_date)}')
        self.run_due(times=1)
        self.assertEqual(self.fake.of_type('event.starting_soon'), [])

    def test_an_event_that_could_not_be_queued_is_tried_again(self):
        self.event_with_slot(starts_in=timedelta(hours=10))
        with patch('switchboard.models.Delivery.objects.create', side_effect=RuntimeError('boom')), \
                self.assertLogs('switchboard', 'ERROR'):
            self.assertEqual(timed.run_due(self.now), 0)
        self.assertFalse(EmittedOnce.objects.exists())
        self.run_due()
        self.one('event.starting_soon')

    def test_one_failing_check_does_not_stop_the_rest(self):
        self.event_with_slot(starts_in=timedelta(hours=10))
        with patch('switchboard.timed.CHECKS', (lambda now, since: 1 / 0, timed.events_starting_soon)), \
                self.assertLogs('switchboard', 'ERROR'), self.committing():
            self.assertEqual(timed.run_due(self.now), 1)

    def test_the_pass_runs_from_requests_at_most_every_five_minutes(self):
        self.event_with_slot(starts_in=timedelta(hours=10))
        with self.committing():
            self.client.get('/.well-known/switchboard')
        self.one('event.starting_soon')
        with patch('switchboard.timed.run_due') as run_due:
            self.client.get('/.well-known/switchboard')
        run_due.assert_not_called()

    def test_the_command_runs_the_pass_and_old_marks_are_pruned(self):
        self.event_with_slot(starts_in=timedelta(hours=10))
        with self.committing():
            call_command('send_switchboard_deliveries', verbosity=0)
        self.one('event.starting_soon')
        self.assertEqual(Delivery.objects.filter(event_type='event.starting_soon', status='delivered').count(), 1)
        EmittedOnce.objects.update(created_at=self.now - timedelta(days=61))
        call_command('send_switchboard_deliveries', verbosity=0)
        self.assertFalse(EmittedOnce.objects.filter(kind='event.starting_soon', created_at__lt=self.now - timedelta(days=60)).exists())
