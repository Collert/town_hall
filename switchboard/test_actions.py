"""The actions Switchboard can run beyond the first four (those are in tests.py): what each
does, and what it refuses. `SwitchboardTestCase.action` holds every answer to the manifest.
"""
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from api.models import APIKey, ShiftHeartbeat
from base.models import Endorsement, Level, Notification, PointsEntry, Venue, VenueNote
from education.models import (ExternalCertificate, Skill, TrainingLesson, TrainingModule, TrainingModuleCompletion,
                              UserCertification)
from events.models import (Event, EventArea, EventRoleSlot, EventSlotInvite, SlotCancellation, SlotSignup)
from jobs.models import Role, RoleCertificateRequirement, RoleTrainingRequirement, Shift

from . import events
from .models import IdempotencyRecord
from .tests import SwitchboardTestCase, User


class ActionTestCase(SwitchboardTestCase):
    def setUp(self):
        super().setUp()
        self.connection, self.key = self.connect()
        self.me = str(self.volunteer.pk)

    def ok(self, name, data=None, **headers):
        response = self.action(name, self.key, data, **headers)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def refuses(self, name, data, status, contains=''):
        """The call is refused with this status, in the protocol's error shape, with a message for a person."""
        response = self.action(name, self.key, data)
        self.assertEqual(response.status_code, status, response.content)
        error = response.json()['error']
        self.assertEqual(error['code'], {409: 'conflict', 422: 'invalid_params'}[status])
        self.assertIn(contains, error['message'])
        return error['message']


class ParamTests(ActionTestCase):
    """views._check_params: settings arrive as their manifest types and within their limits."""

    def test_numbers_bools_and_times_are_read_as_their_type(self):
        venue = Venue.objects.create(name='Parish Hall', latitude=1, longitude=1)
        for days in (3, '3', 3.0):
            note = self.ok('venue.post_note', {'venue': venue.pk, 'note': 'Closed', 'days': days})
            self.assertEqual(VenueNote.objects.get(pk=note['note_id']).expires_after, timedelta(days=3))
        for wrong in (2.5, 'soon', True, [3]):
            self.refuses('venue.post_note', {'venue': 'Parish Hall', 'note': 'Closed', 'days': wrong}, 422, 'days')
        self.refuses('venue.post_note', {'venue': 'Parish Hall', 'note': 'Closed', 'days': 5}, 422, 'one of')

        self.assertIs(self.ok('event.find', {'include_hidden': 'true'})['found'], False)
        self.refuses('event.find', {'include_hidden': 'maybe'}, 422, 'include_hidden')
        self.refuses('event.find', {'when': 'someday'}, 422, 'when')
        self.refuses('event.create', {'title': 'Bazaar', 'starts_at': 'tomorrow', 'ends_at': '2026-12-06T17:00:00-08:00'},
                     422, 'starts_at')
        self.refuses('person.get', {'person_id': {'id': 1}}, 422, 'person_id')

    def test_limits_from_the_manifest_are_enforced(self):
        self.refuses('points.award', {'person_id': self.me, 'points': 10001, 'reason': 'x'}, 422, 'at most 10000')
        self.refuses('points.award', {'person_id': self.me, 'points': -10001, 'reason': 'x'}, 422, 'at least -10000')
        self.refuses('points.award', {'person_id': self.me, 'points': 5, 'reason': 'x' * 201}, 422, '200 characters')
        self.refuses('person.search', {'min_level': 0}, 422, 'min_level')
        self.assertFalse(PointsEntry.objects.exists())

    def test_names_are_matched_in_the_default_language_whatever_the_request_asks_for(self):
        Role.objects.create(name='Librarian', icon='x', permanent=True)
        found = self.ok('role.find', {'name': 'libr'}, **{'Accept-Language': 'uk'})
        self.assertEqual(found['name'], 'Librarian')


class PeopleLookupTests(ActionTestCase):
    def test_person_get_has_the_new_details(self):
        profile = self.volunteer.profile
        profile.phone, profile.location = '604 555 0134', 'New Westminster'
        profile.save()
        profile.skills.add(Skill.objects.create(name='Cooking'), Skill.objects.create(name='First aid'))
        profile.permanent_roles.add(Role.objects.create(name='Librarian', icon='x', permanent=True))
        got = self.ok('person.get', {'person_id': self.me})
        self.assertEqual((got['active'], got['staff'], got['phone'], got['location'], got['skills'], got['permanent_roles'],
                          got['reliability']), (True, False, '604 555 0134', 'New Westminster', 'Cooking, First aid', 'Librarian', 100))

    def test_person_search(self):
        Level.objects.all().delete()
        Level.objects.create(name='Seedling', numeric_name=1, min_points=0)
        Level.objects.create(name='Pillar', numeric_name=2, min_points=100)
        petro, ivan = self.person('petro', last_name='Shevchenko'), self.person('ivan')
        self.person('gone', is_active=False)
        PointsEntry.objects.create(user=petro, amount=150, source=PointsEntry.ADJUSTMENT)
        PointsEntry.objects.create(user=ivan, amount=20, source=PointsEntry.ADJUSTMENT)
        librarian = Role.objects.create(name='Librarian', icon='x', permanent=True)
        ivan.profile.permanent_roles.add(librarian)
        event, slot = self.event_with_slot()
        slot.add_signup(self.volunteer)
        slot.add_signup(ivan)

        everyone = self.ok('person.search')
        self.assertEqual((everyone['found'], everyone['count'], everyone['email']), (True, 4, 'petro@example.org'))
        self.assertEqual(everyone['names'], 'Petro Shevchenko, Ivan, Sam Staff, Maria K.')
        self.assertEqual(everyone['emails'].split(', ')[0], 'petro@example.org')

        by_name = self.ok('person.search', {'query': 'petro shev'})
        self.assertEqual((by_name['count'], by_name['person_id'], by_name['level_name']), (1, str(petro.pk), 'Pillar'))
        self.assertEqual(self.ok('person.search', {'min_level': 2})['emails'], 'petro@example.org')
        self.assertEqual(self.ok('person.search', {'min_level': 3}), {'found': False, 'count': 0})
        self.assertEqual(self.ok('person.search', {'role': 'librarian'})['emails'], 'ivan@example.org')
        at_event = self.ok('person.search', {'event_id': str(event.pk)})
        self.assertEqual((at_event['count'], at_event['emails']), (2, 'ivan@example.org, Maria@example.org'))
        self.assertEqual(self.ok('person.search', {'query': 'nobody'}), {'found': False, 'count': 0})

        self.refuses('person.search', {'role': 'Juggler'}, 422, 'Juggler')
        self.refuses('person.search', {'event_id': '999999'}, 422, 'no event')

    def test_person_next_shift(self):
        self.assertEqual(self.ok('person.next_shift', {'person_id': self.me}), {'found': False, 'count': 0})
        event, later = self.event_with_slot()
        sooner = EventRoleSlot.objects.create(event=event, role=later.role, required_qty=1,
                                              start_time=later.start_time - timedelta(hours=6),
                                              end_time=later.start_time - timedelta(hours=3))
        over = EventRoleSlot.objects.create(event=event, role=later.role, start_time=timezone.now() - timedelta(days=2),
                                            end_time=timezone.now() - timedelta(days=1))
        for slot in (later, sooner, over):
            slot.signups.add(self.volunteer)
        found = self.ok('person.next_shift', {'person_id': self.me})
        self.assertEqual((found['found'], found['count'], found['shift_id'], found['role'], found['event_title'], found['open_spots']),
                         (True, 2, str(sooner.pk), 'Greeter', 'Christmas Bazaar', 0))
        self.assertEqual(self.ok('person.next_shift', {'person_id': '999999'}), {'found': False})

    def test_shift_current(self):
        self.assertEqual(self.ok('shift.current', {'person_id': self.me}), {'found': False})
        event, slot = self.event_with_slot()
        shift = Shift.objects.create(user=self.volunteer, event_role_slot=slot)
        self.assertEqual(self.ok('shift.current', {'person_id': self.me}), {
            'found': True, 'role': 'Greeter', 'started_at': events.iso(shift.start_time),
            'shift_id': str(slot.pk), 'event_id': str(event.pk), 'event_title': 'Christmas Bazaar'})
        shift.end_shift()
        self.assertEqual(self.ok('shift.current', {'person_id': self.me}), {'found': False})

    def test_role_check(self):
        role = Role.objects.create(name='Cook', icon='x')
        module = TrainingModule.objects.create(title='Food Safety Basics', description='x', icon='x')
        first_aid = ExternalCertificate.objects.create(name='First Aid', description='x')
        RoleTrainingRequirement.objects.create(role=role, training_module=module, mandatory=True)
        RoleTrainingRequirement.objects.create(
            role=role, training_module=TrainingModule.objects.create(title='Nice to have', description='x', icon='x'))
        RoleCertificateRequirement.objects.create(role=role, certificate=first_aid, mandatory=True)
        self.assertEqual(self.ok('role.check', {'person_id': self.me, 'role': 'cook'}), {
            'found': True, 'qualified': False, 'missing_training': 'Food Safety Basics', 'missing_certificates': 'First Aid',
            'assigned': False, 'role_id': str(role.pk), 'role': 'Cook'})
        TrainingModuleCompletion.objects.create(training_module=module, user=self.volunteer)
        UserCertification.objects.create(user=self.volunteer, certificate=first_aid, verified=True)
        checked = self.ok('role.check', {'person_id': self.me, 'role': str(role.pk)})
        self.assertEqual((checked['qualified'], checked['missing_training'], checked['missing_certificates']), (True, '', ''))
        self.assertEqual(self.ok('role.check', {'person_id': '999999', 'role': 'Cook'}), {'found': False})
        self.refuses('role.check', {'person_id': self.me, 'role': 'Juggler'}, 422, 'Juggler')

    def test_training_status(self):
        module = TrainingModule.objects.create(title='Food Safety Basics', description='x', icon='x', expires_after_days=365)
        lesson = TrainingLesson.objects.create(training_module=module, title='Hands', content='Wash them.')
        TrainingLesson.objects.create(training_module=module, title='Fridges', content='Keep them cold.')
        self.assertEqual(self.ok('training.status', {'person_id': self.me, 'module': 'food safety basics'}), {
            'found': True, 'completed': False, 'progress': 0, 'expired': False,
            'module_id': str(module.pk), 'module_title': 'Food Safety Basics'})
        lesson.completed_by.add(self.volunteer)
        self.assertEqual(self.ok('training.status', {'person_id': self.me, 'module': module.pk})['progress'], 50)

        done = TrainingModuleCompletion.objects.create(training_module=module, user=self.volunteer)
        status = self.ok('training.status', {'person_id': self.me, 'module': str(module.pk)})
        self.assertEqual((status['completed'], status['progress'], status['completed_at'], status['expired'], status['expires_at']),
                         (True, 100, events.iso(done.completed_at), False, events.iso(done.completed_at + timedelta(days=365))))
        TrainingModuleCompletion.objects.update(completed_at=timezone.now() - timedelta(days=400))
        self.assertIs(self.ok('training.status', {'person_id': self.me, 'module': 'Food Safety Basics'})['expired'], True)

        self.assertEqual(self.ok('training.status', {'person_id': 'abc', 'module': 'Food Safety Basics'}), {'found': False})
        self.refuses('training.status', {'person_id': self.me, 'module': 'Juggling'}, 422, 'Juggling')
        TrainingModule.objects.create(title='Admin guide', description='x', icon='x', staff_only=True)
        self.refuses('training.status', {'person_id': self.me, 'module': 'Admin guide'}, 422)

    def test_certificate_status(self):
        first_aid = ExternalCertificate.objects.create(name='First Aid', description='x')
        ask = {'person_id': self.me, 'certificate': 'first aid'}
        self.assertEqual(self.ok('certificate.status', ask), {
            'found': True, 'status': 'none', 'valid': False, 'certificate_id': str(first_aid.pk), 'certificate': 'First Aid'})
        held = UserCertification.objects.create(user=self.volunteer, certificate=first_aid)
        self.assertEqual(self.ok('certificate.status', ask)['status'], 'pending')
        expires = timezone.now() + timedelta(days=30)
        UserCertification.objects.filter(pk=held.pk).update(verified=True, expiration_date=expires)
        status = self.ok('certificate.status', ask)
        self.assertEqual((status['status'], status['valid'], status['expires_at']), ('verified', True, events.iso(expires)))
        UserCertification.objects.filter(pk=held.pk).update(expiration_date=timezone.now() - timedelta(days=1))
        status = self.ok('certificate.status', ask)
        self.assertEqual((status['status'], status['valid']), ('expired', False))
        UserCertification.objects.filter(pk=held.pk).update(verified=False, rejected=True)
        self.assertEqual(self.ok('certificate.status', ask)['status'], 'rejected')
        self.refuses('certificate.status', {'person_id': self.me, 'certificate': 'Juggling'}, 422, 'Juggling')
        self.assertEqual(self.ok('certificate.status', {'person_id': '0', 'certificate': 'First Aid'}), {'found': False})


class PeopleActionTests(ActionTestCase):
    def test_person_update(self):
        updated = self.ok('person.update', {'person_id': self.me, 'last_name': ' Kovalenko ', 'phone': '604 555 0134',
                                            'location': 'New Westminster'})
        self.assertEqual(updated, {
            'person_id': self.me, 'name': 'Maria Kovalenko', 'email': 'Maria@example.org', 'updated': True,
            'changed': 'last_name, phone, location', 'phone': '604 555 0134', 'location': 'New Westminster'})
        self.volunteer.refresh_from_db()
        self.assertEqual((self.volunteer.last_name, self.volunteer.profile.phone), ('Kovalenko', '604 555 0134'))
        again = self.ok('person.update', {'person_id': self.me, 'phone': '604 555 0134'})
        self.assertEqual((again['updated'], again['changed']), (False, ''))

        self.refuses('person.update', {'person_id': self.me}, 422, 'at least one')
        self.refuses('person.update', {'person_id': self.me, 'phone': '6' * 31}, 422, 'phone')
        self.refuses('person.update', {'person_id': '999999', 'phone': '1'}, 422, 'no volunteer')
        # The email is how the organization's apps match people: it isn't a setting of this action.
        self.ok('person.update', {'person_id': self.me, 'phone': '1', 'email': 'other@example.org'})
        self.volunteer.refresh_from_db()
        self.assertEqual(self.volunteer.email, 'Maria@example.org')

    def test_person_deactivate_and_reactivate(self):
        with self.committing():
            off = self.ok('person.deactivate', {'person_id': self.me})
        self.assertEqual((off['active'], off['changed'], off['email']), (False, True, 'Maria@example.org'))
        self.volunteer.refresh_from_db()
        self.assertFalse(self.volunteer.is_active)
        self.one('volunteer.deactivated')
        self.assertIs(self.ok('person.deactivate', {'person_id': self.me})['changed'], False)
        on = self.ok('person.reactivate', {'person_id': self.me})
        self.assertEqual((on['active'], on['changed']), (True, True))

        self.refuses('person.deactivate', {'person_id': str(self.staff.pk)}, 409, 'staff')
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)
        self.refuses('person.reactivate', {'person_id': 'abc'}, 422, 'no volunteer')

    def test_points_award_goes_through_the_ledger_once(self):
        Level.objects.all().delete()
        Level.objects.create(name='Seedling', numeric_name=1, min_points=0)
        Level.objects.create(name='Pillar', numeric_name=2, min_points=100)
        call = {'person_id': self.me, 'points': 120, 'reason': 'Ran the café for a month'}
        with self.committing():
            first = self.ok('points.award', call, **{'Idempotency-Key': 'run-9:step-2'})
            again = self.ok('points.award', call, **{'Idempotency-Key': 'run-9:step-2'})
        self.assertEqual(first, again)
        entry = PointsEntry.objects.get()
        self.assertEqual(first, {'entry_id': str(entry.pk), 'person_id': self.me, 'points': 120, 'impact_points': 120,
                                 'level': 2, 'level_name': 'Pillar'})
        self.assertEqual((entry.source, entry.reason, entry.created_by), (PointsEntry.ADJUSTMENT, 'Ran the café for a month', None))
        self.assertEqual(self.one('points.awarded')['data']['reason'], 'Ran the café for a month')
        self.one('volunteer.level_reached')

        taken = self.ok('points.award', {'person_id': self.me, 'points': '-20', 'reason': 'Correction'})
        self.assertEqual((taken['points'], taken['impact_points'], taken['level']), (-20, 100, 2))
        self.refuses('points.award', {'person_id': self.me, 'points': 0, 'reason': 'Nothing'}, 422, 'not be 0')
        self.refuses('points.award', {'person_id': self.me, 'points': 5}, 422, 'reason')
        self.refuses('points.award', {'person_id': '999999', 'points': 5, 'reason': 'x'}, 422, 'no volunteer')
        self.assertEqual(PointsEntry.objects.count(), 2)

    def test_endorsement_give(self):
        teamwork = Skill.objects.create(name='Teamwork')
        call = {'person_id': self.me, 'from_person_id': str(self.staff.pk), 'skill': 'teamwork', 'message': 'Calm all day.'}
        with self.committing(), patch('base.email.notify') as notify:
            given = self.ok('endorsement.give', call)
        endorsement = Endorsement.objects.get()
        self.assertEqual(given, {'endorsement_id': str(endorsement.pk), 'created': True,
                                 'points': PointsEntry.objects.get(endorsement=endorsement).amount, 'skill': 'Teamwork'})
        self.assertGreater(given['points'], 0)   # staff endorsing earns points under the usual rules
        self.assertEqual((endorsement.endorser, endorsement.endorsed, endorsement.text), (self.staff, self.volunteer, 'Calm all day.'))
        self.assertEqual(list(endorsement.skills.all()), [teamwork])
        self.assertIn('Sam Staff', Notification.objects.get(user=self.volunteer).message)
        self.assertEqual(notify.call_args.args[:2], ('endorsement_received', self.volunteer))
        self.assertEqual(self.one('endorsement.given')['data']['skills'], 'Teamwork')

        again = self.ok('endorsement.give', call)   # a person endorses each skill once
        self.assertEqual((again['created'], again['endorsement_id'], again['points']), (False, str(endorsement.pk), 0))
        self.assertEqual(Endorsement.objects.count(), 1)

    def test_endorsement_give_refusals(self):
        Skill.objects.create(name='Teamwork')
        staff = str(self.staff.pk)
        self.refuses('endorsement.give', {'person_id': self.me, 'from_person_id': self.me, 'skill': 'Teamwork'}, 422, 'themselves')
        self.refuses('endorsement.give', {'person_id': self.me, 'from_person_id': staff, 'skill': 'Juggling'}, 422, 'Juggling')
        self.refuses('endorsement.give', {'person_id': self.me, 'from_person_id': '999999', 'skill': 'Teamwork'}, 422, 'no volunteer')
        self.refuses('endorsement.give', {'person_id': self.me, 'from_person_id': staff, 'skill': 'Teamwork', 'event_id': '999999'},
                     422, 'no event')
        self.volunteer.is_active = False
        self.volunteer.save()
        self.refuses('endorsement.give', {'person_id': self.me, 'from_person_id': staff, 'skill': 'Teamwork'}, 409, 'deactivated')
        self.assertFalse(Endorsement.objects.exists())

    def test_endorsement_by_a_teammate_earns_points_only_under_the_usual_rule(self):
        Skill.objects.create(name='Teamwork')
        petro = self.person('petro')
        event, slot = self.event_with_slot()
        apart = self.ok('endorsement.give', {'person_id': self.me, 'from_person_id': str(petro.pk), 'skill': 'Teamwork'})
        self.assertEqual((apart['created'], apart['points']), (True, 0))   # no shared event: an endorsement, no points
        Endorsement.objects.all().delete()
        slot.add_signup(self.volunteer)
        slot.add_signup(petro)
        together = self.ok('endorsement.give', {'person_id': self.me, 'from_person_id': str(petro.pk), 'skill': 'Teamwork',
                                                'event_id': str(event.pk)})
        self.assertGreater(together['points'], 0)


class RoleActionTests(ActionTestCase):
    def setUp(self):
        super().setUp()
        self.librarian = Role.objects.create(name='Librarian', icon='x', permanent=True, description='Runs the library desk.')
        self.greeter = Role.objects.create(name='Greeter', icon='x')

    def test_role_find(self):
        Role.area_lead()   # built-in roles are never offered
        found = self.ok('role.find', {'name': 'libr'})
        self.assertEqual(found, {'found': True, 'count': 1, 'role_id': str(self.librarian.pk), 'name': 'Librarian',
                                 'permanent': True, 'description': 'Runs the library desk.', 'names': 'Librarian'})
        every = self.ok('role.find')
        self.assertEqual((every['count'], every['names'], every['name']), (2, 'Greeter, Librarian', 'Greeter'))
        self.assertEqual(self.ok('role.find', {'name': 'Area lead'}), {'found': False, 'count': 0})

    def test_role_assign_and_unassign(self):
        with self.committing():
            assigned = self.ok('role.assign', {'person_id': self.me, 'role': 'Librarian'})
        self.assertEqual(assigned, {'person_id': self.me, 'role_id': str(self.librarian.pk), 'role': 'Librarian',
                                    'assigned': True, 'changed': True, 'permanent_roles': 'Librarian'})
        self.assertTrue(self.librarian.is_assigned_to(self.volunteer))
        self.one('volunteer.role_assigned')
        self.assertIs(self.ok('role.assign', {'person_id': self.me, 'role': str(self.librarian.pk)})['changed'], False)

        removed = self.ok('role.unassign', {'person_id': self.me, 'role': 'librarian'})
        self.assertEqual((removed['assigned'], removed['changed'], removed['permanent_roles']), (False, True, ''))
        self.assertFalse(self.librarian.is_assigned_to(self.volunteer))
        self.assertIs(self.ok('role.unassign', {'person_id': self.me, 'role': 'Librarian'})['changed'], False)

    def test_role_assign_refusals(self):
        self.refuses('role.assign', {'person_id': self.me, 'role': 'Greeter'}, 422, 'event role')
        self.refuses('role.assign', {'person_id': self.me, 'role': 'Juggler'}, 422, 'Juggler')
        self.refuses('role.assign', {'person_id': self.me, 'role': 'Area lead'}, 422)
        self.refuses('role.unassign', {'person_id': '999999', 'role': 'Librarian'}, 422, 'no volunteer')
        Role.objects.create(name='librarian', icon='x', permanent=True)
        self.refuses('role.assign', {'person_id': self.me, 'role': 'Librarian'}, 422, 'More than one')
        self.volunteer.is_active = False
        self.volunteer.save()
        self.refuses('role.assign', {'person_id': self.me, 'role': str(self.librarian.pk)}, 409, 'deactivated')
        self.assertFalse(self.volunteer.profile.permanent_roles.exists())


class EventActionTests(ActionTestCase):
    def test_event_get(self):
        event, slot = self.event_with_slot()
        event.coordinators.add(self.staff)
        slot.add_signup(self.volunteer)
        self.assertEqual(self.ok('event.get', {'event_id': str(event.pk)}), {
            'found': True, 'event_id': str(event.pk), 'title': 'Christmas Bazaar', 'place': 'Parish Hall', 'address': 'Parish Hall',
            'starts_at': events.iso(event.start_date), 'ends_at': events.iso(event.end_date), 'published': True,
            'status': 'upcoming', 'url': f'http://testserver/en/opportunities/{event.pk}/',
            'volunteers_needed': 2, 'volunteers_signed_up': 1, 'open_spots': 1,
            'coordinators': 'Sam Staff', 'coordinator_emails': 'staff@example.org', 'volunteer_emails': 'Maria@example.org'})
        for unknown in ('999999', 'abc'):
            self.assertEqual(self.ok('event.get', {'event_id': unknown}), {'found': False})

    def test_event_find(self):
        now = timezone.now()
        bazaar, _slot = self.event_with_slot()
        dinner = Event.objects.create(title='New Year Dinner', description='x', location='Hall', latitude=1, longitude=1,
                                      start_date=now + timedelta(days=1), end_date=now + timedelta(days=1, hours=3))
        hidden = Event.objects.create(title='Secret Dinner', description='x', location='Hall', latitude=1, longitude=1,
                                      start_date=now + timedelta(hours=5), end_date=now + timedelta(hours=8), published=False)
        picnic = Event.objects.create(title='Summer Picnic', description='x', location='Park', latitude=1, longitude=1,
                                      start_date=now - timedelta(days=90), end_date=now - timedelta(days=90, hours=-4))

        upcoming = self.ok('event.find')
        self.assertEqual((upcoming['found'], upcoming['count'], upcoming['event_id'], upcoming['titles']),
                         (True, 2, str(dinner.pk), 'New Year Dinner, Christmas Bazaar'))
        self.assertEqual(self.ok('event.find', {'query': 'bazaar'})['event_id'], str(bazaar.pk))
        with_hidden = self.ok('event.find', {'query': 'dinner', 'include_hidden': True})
        self.assertEqual((with_hidden['count'], with_hidden['event_id'], with_hidden['published']), (2, str(hidden.pk), False))
        past = self.ok('event.find', {'when': 'past'})
        self.assertEqual((past['count'], past['event_id'], past['status']), (1, str(picnic.pk), 'past'))
        self.assertEqual(self.ok('event.find', {'query': 'wedding'}), {'found': False, 'count': 0})

    def test_event_create_is_hidden_until_published(self):
        venue = Venue.objects.create(name='Parish Hall', address='501 4th Ave', latitude=1, longitude=1)
        call = {'title': ' Christmas  Bazaar ', 'starts_at': '2031-12-06T09:00:00-08:00', 'ends_at': '2031-12-06T17:00:00-08:00',
                'description': 'Stalls and carols.', 'venue': 'parish hall', 'attendees': 400}
        with self.committing():
            created = self.ok('event.create', call, **{'Idempotency-Key': 'run-1:step-1'})
            self.assertEqual(self.ok('event.create', call, **{'Idempotency-Key': 'run-1:step-1'}), created)
        event = Event.objects.get()
        self.assertEqual((created['created'], created['event_id'], created['title'], created['published'], created['place'],
                          created['address'], created['coordinators']),
                         (True, str(event.pk), 'Christmas Bazaar', False, 'Parish Hall', '501 4th Ave', 'Sam Staff'))
        self.assertEqual(created['starts_at'], events.iso(event.start_date))
        self.assertEqual((event.venue, event.attendees, event.description, event.published), (venue, 400, 'Stalls and carols.', False))
        self.assertEqual(event.start_date.isoformat(), '2031-12-06T17:00:00+00:00')
        self.assertEqual(list(event.coordinators.all()), [self.staff])   # whoever connected Switchboard
        self.assertIs(self.one('event.created')['data']['event']['published'], False)
        self.assertEqual(self.fake.of_type('event.published'), [])

    def test_event_create_with_an_address_and_a_coordinator(self):
        with patch('base.geo.geocode', return_value=(49.2, -122.9)) as geocode:
            created = self.ok('event.create', {
                'title': 'Picnic', 'starts_at': '2031-07-01T11:00:00', 'ends_at': '2031-07-01T15:00:00',
                'place': 'Queens Park', 'coordinator_id': self.me})
        geocode.assert_called_once_with('Queens Park')
        event = Event.objects.get()
        self.assertEqual((created['place'], created['coordinator_emails'], event.latitude), ('Queens Park', 'Maria@example.org', 49.2))
        # Without an offset, a time is the organization's local time.
        self.assertEqual(timezone.localtime(event.start_date).strftime('%Y-%m-%d %H:%M'), '2031-07-01 11:00')

    def test_event_create_refusals(self):
        times = {'starts_at': '2031-12-06T09:00:00-08:00', 'ends_at': '2031-12-06T17:00:00-08:00'}
        self.refuses('event.create', {'title': 'Bazaar', 'starts_at': times['ends_at'], 'ends_at': times['starts_at']}, 422, 'end after')
        self.refuses('event.create', {'title': 'Bazaar', **times, 'venue': 'Nowhere Hall'}, 422, 'Nowhere Hall')
        self.refuses('event.create', {'title': 'B' * 101, **times}, 422, 'title')
        self.refuses('event.create', {'title': 'Bazaar', **times, 'attendees': -1}, 422, 'attendees')
        self.refuses('event.create', {'title': 'Bazaar', **times, 'coordinator_id': '999999'}, 422, 'no volunteer')
        self.refuses('event.create', {**times}, 422, 'title')
        self.assertFalse(Event.objects.exists())

    def test_event_update(self):
        event, _slot = self.event_with_slot()
        new_start = event.start_date + timedelta(days=1)
        with self.committing():
            updated = self.ok('event.update', {'event_id': str(event.pk), 'title': 'Christmas Bazaar 2031', 'attendees': 350,
                                               'starts_at': new_start.isoformat(), 'ends_at': (new_start + timedelta(hours=8)).isoformat()})
        self.assertEqual((updated['updated'], updated['changed'], updated['title']),
                         (True, 'title, starts_at, ends_at, attendees', 'Christmas Bazaar 2031'))
        event.refresh_from_db()
        self.assertEqual((event.title, event.attendees, event.start_date), ('Christmas Bazaar 2031', 350, new_start))
        self.one('event.rescheduled')
        same = self.ok('event.update', {'event_id': str(event.pk), 'attendees': 350})
        self.assertEqual((same['updated'], same['changed']), (False, ''))

        self.refuses('event.update', {'event_id': str(event.pk)}, 422, 'at least one')
        self.refuses('event.update', {'event_id': str(event.pk), 'ends_at': (new_start - timedelta(hours=1)).isoformat()}, 422, 'end after')
        self.refuses('event.update', {'event_id': '999999', 'title': 'x'}, 422, 'no event')

    def test_event_publish_and_unpublish(self):
        event, _slot = self.event_with_slot(published=False)
        with self.committing(), patch('base.push.announce_event') as announce:
            published = self.ok('event.publish', {'event_id': str(event.pk)})
        self.assertEqual(published, {'event_id': str(event.pk), 'title': 'Christmas Bazaar', 'published': True, 'changed': True,
                                     'url': f'http://testserver/en/opportunities/{event.pk}/'})
        announce.assert_not_called()   # a workflow sends its own announcement
        self.one('event.published')
        self.assertIs(self.ok('event.publish', {'event_id': str(event.pk)})['changed'], False)

        with self.committing():
            hidden = self.ok('event.unpublish', {'event_id': str(event.pk)})
        self.assertEqual((hidden['published'], hidden['changed']), (False, True))
        event.refresh_from_db()
        self.assertFalse(event.published)
        self.one('event.unpublished')
        self.refuses('event.publish', {'event_id': '999999'}, 422, 'no event')
        self.refuses('event.unpublish', {'event_id': 'abc'}, 422, 'no event')

    def test_event_add_shift(self):
        event, first = self.event_with_slot()
        start, end = event.start_date + timedelta(hours=4), event.start_date + timedelta(hours=8)
        added = self.ok('event.add_shift', {'event_id': str(event.pk), 'role': 'greeter', 'starts_at': start.isoformat(),
                                            'ends_at': end.isoformat(), 'needed': 3, 'extra': 1, 'invite_only': True})
        slot = EventRoleSlot.objects.exclude(pk=first.pk).get()
        self.assertEqual((added['created'], added['shift_id'], added['role'], added['needed'], added['taken'], added['open_spots'],
                          added['invite_only'], added['event_id']), (True, str(slot.pk), 'Greeter', 3, 0, 3, True, str(event.pk)))
        self.assertEqual((slot.start_time, slot.end_time, slot.required_qty, slot.allowed_overstaffing_qty, slot.is_public),
                         (start, end, 3, 1, False))
        plain = self.ok('event.add_shift', {'event_id': str(event.pk), 'role': str(first.role_id),
                                            'starts_at': start.isoformat(), 'ends_at': end.isoformat()})
        self.assertEqual((plain['needed'], plain['invite_only']), (1, False))

    def test_event_add_shift_refusals(self):
        event, slot = self.event_with_slot()
        Role.objects.create(name='Librarian', icon='x', permanent=True)
        Role.area_lead()
        times = {'starts_at': slot.start_time.isoformat(), 'ends_at': slot.end_time.isoformat()}
        ask = {'event_id': str(event.pk), 'role': 'Greeter', **times}
        self.refuses('event.add_shift', {**ask, 'role': 'Librarian'}, 422, 'permanent')
        self.refuses('event.add_shift', {**ask, 'role': 'Area lead'}, 422)
        self.refuses('event.add_shift', {**ask, 'role': 'Juggler'}, 422, 'Juggler')
        self.refuses('event.add_shift', {**ask, 'ends_at': times['starts_at']}, 422, 'end after')
        self.refuses('event.add_shift', {**ask, 'needed': 0}, 422, 'needed')
        self.refuses('event.add_shift', {**ask, 'event_id': '999999'}, 422, 'no event')
        Event.objects.filter(pk=event.pk).update(start_date=timezone.now() - timedelta(days=3), end_date=timezone.now() - timedelta(days=2))
        self.refuses('event.add_shift', ask, 409, 'over')
        self.assertEqual(EventRoleSlot.objects.count(), 1)

    def test_event_notify_volunteers(self):
        event, slot = self.event_with_slot()
        gone = self.person('gone', is_active=False)
        for person in (self.volunteer, self.person('petro'), gone):
            slot.signups.add(person)
        with self.committing(), patch('base.email.notify') as notify:
            sent = self.ok('event.notify_volunteers', {'event_id': str(event.pk), 'message': 'Doors open at 8.'})
        self.assertEqual(sent, {'sent': 2, 'event_id': str(event.pk), 'title': 'Christmas Bazaar'})
        notification = Notification.objects.get(user=self.volunteer)
        self.assertEqual((notification.message, notification.link),
                         ('Christmas Bazaar: Doors open at 8.', f'http://testserver/en/opportunities/{event.pk}/'))
        self.assertFalse(Notification.objects.filter(user=gone).exists())
        self.assertEqual({call.args[0] for call in notify.call_args_list}, {'event_message'})
        self.assertEqual(notify.call_count, 2)
        self.refuses('event.notify_volunteers', {'event_id': str(event.pk)}, 422, 'message')
        self.refuses('event.notify_volunteers', {'event_id': '999999', 'message': 'Hi'}, 422, 'no event')


class ShiftLookupTests(ActionTestCase):
    def test_shift_get(self):
        event, slot = self.event_with_slot()
        slot.add_signup(self.volunteer)
        slot.lead = self.volunteer
        slot.save()
        self.assertEqual(self.ok('shift.get', {'shift_id': slot.pk}), {
            'found': True, 'shift_id': str(slot.pk), 'role': 'Greeter', 'role_id': str(slot.role_id),
            'starts_at': events.iso(slot.start_time), 'ends_at': events.iso(slot.end_time),
            'event_id': str(event.pk), 'event_title': 'Christmas Bazaar', 'place': 'Parish Hall', 'area': '',
            'needed': 2, 'taken': 1, 'open_spots': 1, 'invite_only': False,
            'lead_name': 'Maria K.', 'lead_email': 'Maria@example.org',
            'volunteers': 'Maria K.', 'volunteer_emails': 'Maria@example.org'})
        self.assertEqual(self.ok('shift.get', {'shift_id': '999999'}), {'found': False})

    def test_shift_find(self):
        event, morning = self.event_with_slot(required_qty=1)
        cook = Role.objects.create(name='Cook', icon='x')
        afternoon = EventRoleSlot.objects.create(event=event, role=morning.role, required_qty=2,
                                                 start_time=morning.end_time, end_time=morning.end_time + timedelta(hours=4))
        kitchen = EventRoleSlot.objects.create(event=event, role=cook, start_time=morning.start_time + timedelta(hours=1),
                                               end_time=morning.end_time)
        event.chain_of_command = True
        event.save()
        area = EventArea.objects.create(event=event, name='Kitchen')
        EventRoleSlot.objects.create(event=event, role=Role.area_lead(), area=area, is_public=False,
                                     start_time=morning.start_time, end_time=morning.end_time)
        morning.add_signup(self.volunteer)

        every = self.ok('shift.find', {'event_id': str(event.pk)})
        self.assertEqual((every['found'], every['count'], every['shift_id']), (True, 3, str(morning.pk)))   # no area lead shift
        greeters = self.ok('shift.find', {'event_id': str(event.pk), 'role': 'Greeter', 'open_only': True})
        self.assertEqual((greeters['count'], greeters['shift_id'], greeters['open_spots']), (1, str(afternoon.pk), 2))
        self.assertEqual(self.ok('shift.find', {'event_id': str(event.pk), 'role': str(cook.pk)})['shift_id'], str(kitchen.pk))
        self.assertEqual(self.ok('shift.find', {'event_id': '999999'}), {'found': False, 'count': 0})
        self.refuses('shift.find', {'event_id': str(event.pk), 'role': 'Juggler'}, 422, 'Juggler')
        self.refuses('shift.find', {}, 422, 'event_id')


class SignUpTests(ActionTestCase):
    def setUp(self):
        super().setUp()
        self.event, self.slot = self.event_with_slot(required_qty=1)
        self.ask = {'person_id': self.me, 'shift_id': str(self.slot.pk)}

    def test_sign_up_like_the_volunteer_would(self):
        with self.committing(), patch('base.email.notify') as notify:
            signed = self.ok('shift.sign_up', self.ask, **{'Idempotency-Key': 'run-4:step-1'})
            self.assertEqual(self.ok('shift.sign_up', self.ask, **{'Idempotency-Key': 'run-4:step-1'}), signed)
        self.assertEqual(signed, {
            'shift_id': str(self.slot.pk), 'role': 'Greeter', 'starts_at': events.iso(self.slot.start_time),
            'ends_at': events.iso(self.slot.end_time), 'event_id': str(self.event.pk), 'event_title': 'Christmas Bazaar',
            'place': 'Parish Hall', 'open_spots': 0, 'signed_up': True, 'created': True})
        self.assertTrue(self.slot.user_signed_up(self.volunteer))
        self.assertTrue(SlotSignup.objects.filter(slot=self.slot, user=self.volunteer).exists())   # through add_signup()
        self.assertEqual(notify.call_args.args[:2], ('signup_confirmed', self.volunteer))
        self.assertEqual(notify.call_count, 1)
        self.one('shift.signed_up')
        self.one('shift.filled')
        already = self.ok('shift.sign_up', self.ask)
        self.assertEqual((already['signed_up'], already['created']), (True, False))

    def test_required_training_and_certificates_are_not_bypassed(self):
        module = TrainingModule.objects.create(title='Food Safety Basics', description='x', icon='x')
        first_aid = ExternalCertificate.objects.create(name='First Aid', description='x')
        RoleTrainingRequirement.objects.create(role=self.slot.role, training_module=module, mandatory=True)
        RoleCertificateRequirement.objects.create(role=self.slot.role, certificate=first_aid, mandatory=True)
        self.refuses('shift.sign_up', self.ask, 409, 'training')
        TrainingModuleCompletion.objects.create(training_module=module, user=self.volunteer)
        self.refuses('shift.sign_up', self.ask, 409, 'certificates')
        self.assertFalse(self.slot.user_signed_up(self.volunteer))
        UserCertification.objects.create(user=self.volunteer, certificate=first_aid, verified=True)
        self.assertIs(self.ok('shift.sign_up', self.ask)['created'], True)

    def test_full_clashing_ended_and_hidden_shifts_are_refused(self):
        other = EventRoleSlot.objects.create(event=self.event, role=self.slot.role, start_time=self.slot.start_time + timedelta(hours=1),
                                             end_time=self.slot.end_time + timedelta(hours=1))
        other.add_signup(self.volunteer)
        self.refuses('shift.sign_up', self.ask, 409, 'same time')
        other.signups.remove(self.volunteer)

        self.slot.add_signup(self.person('petro'))
        self.refuses('shift.sign_up', self.ask, 409, 'full')
        self.slot.signups.clear()

        Event.objects.filter(pk=self.event.pk).update(published=False)
        self.refuses('shift.sign_up', self.ask, 409, 'hidden')
        Event.objects.filter(pk=self.event.pk).update(published=True)

        EventRoleSlot.objects.filter(pk=self.slot.pk).update(start_time=timezone.now() - timedelta(hours=5),
                                                             end_time=timezone.now() - timedelta(hours=1))
        self.refuses('shift.sign_up', self.ask, 409, 'ended')

        self.refuses('shift.sign_up', {**self.ask, 'shift_id': '999999'}, 422, 'no shift')
        self.refuses('shift.sign_up', {**self.ask, 'person_id': '999999'}, 422, 'no volunteer')
        self.volunteer.is_active = False
        self.volunteer.save()
        self.refuses('shift.sign_up', self.ask, 409, 'deactivated')
        self.assertFalse(self.volunteer.commitments.exists())

    def test_invite_only_shifts_need_an_invitation(self):
        EventRoleSlot.objects.filter(pk=self.slot.pk).update(is_public=False)
        self.refuses('shift.sign_up', self.ask, 409, 'invitation')
        with self.committing():
            invited = self.ok('shift.invite', self.ask)
        invite = EventSlotInvite.objects.get()
        self.assertEqual((invited['invited'], invited['invite_id'], invited['shift_id']), (True, str(invite.pk), str(self.slot.pk)))
        self.one('invite.sent')
        with self.committing(), patch('base.email.notify'):
            self.assertIs(self.ok('shift.sign_up', self.ask)['created'], True)
        invite.refresh_from_db()
        self.assertTrue(invite.accepted)
        self.one('invite.accepted')

    def test_shift_invite(self):
        with patch('console.views.events.notify') as notify:
            self.ok('shift.invite', {**self.ask, 'message': 'Hello [Name], we need you.'})
        trigger, user, data = notify.call_args.args
        self.assertEqual((trigger, user, data['message'], data['role']), ('invitation', self.volunteer, 'Hello Maria, we need you.', 'Greeter'))
        self.assertEqual(data['link'], f'http://testserver/en/opportunities/invite/{EventSlotInvite.objects.get().token}/')
        self.assertIn('Christmas Bazaar', Notification.objects.get(user=self.volunteer).message)
        self.assertFalse(self.slot.user_signed_up(self.volunteer))   # the person decides

        with patch('console.views.events.notify') as notify:
            self.ok('shift.invite', self.ask)                         # the default note names the event and role
        self.assertIn('Greeter', notify.call_args.args[2]['message'])
        self.assertEqual(EventSlotInvite.objects.count(), 1)

        self.slot.add_signup(self.volunteer)
        already = self.ok('shift.invite', self.ask)
        self.assertEqual((already['invited'], already['invite_id']), (False, ''))

    def test_shift_invite_refusals(self):
        self.refuses('shift.invite', {**self.ask, 'shift_id': 'abc'}, 422, 'no shift')
        self.refuses('shift.invite', {**self.ask, 'message': 'x' * 2001}, 422, 'message')
        self.volunteer.is_active = False
        self.volunteer.save()
        self.refuses('shift.invite', self.ask, 409, 'deactivated')
        self.volunteer.is_active = True
        self.volunteer.save()
        Event.objects.filter(pk=self.event.pk).update(start_date=timezone.now() - timedelta(days=3), end_date=timezone.now() - timedelta(days=2))
        self.refuses('shift.invite', self.ask, 409, 'over')
        self.assertFalse(EventSlotInvite.objects.exists())

    def test_shift_cancel_records_it_like_the_volunteers_own(self):
        self.slot.add_signup(self.volunteer)
        with self.committing():
            cancelled = self.ok('shift.cancel', self.ask)
        self.assertEqual((cancelled['cancelled'], cancelled['late'], cancelled['open_spots']), (True, False, 1))
        self.assertFalse(self.slot.user_signed_up(self.volunteer))
        self.assertFalse(SlotCancellation.objects.get(slot=self.slot, user=self.volunteer).late)
        self.assertIs(self.one('shift.cancelled')['data']['late'], False)
        nothing = self.ok('shift.cancel', self.ask)
        self.assertEqual((nothing['cancelled'], nothing['late']), (False, False))

    def test_a_late_cancel_counts_and_a_started_shift_cannot_be_cancelled(self):
        EventRoleSlot.objects.filter(pk=self.slot.pk).update(start_time=timezone.now() + timedelta(hours=2),
                                                             end_time=timezone.now() + timedelta(hours=6))
        self.event.coordinators.add(self.staff)
        self.slot.add_signup(self.volunteer)
        late = self.ok('shift.cancel', self.ask)
        self.assertEqual((late['cancelled'], late['late']), (True, True))
        self.assertTrue(SlotCancellation.objects.get().late)
        self.assertIn('Maria K.', Notification.objects.get(user=self.staff).message)   # the coordinators are told

        self.slot.add_signup(self.volunteer)
        Shift.objects.create(user=self.volunteer, event_role_slot=self.slot)
        self.refuses('shift.cancel', self.ask, 409, 'already started')
        self.assertTrue(self.slot.user_signed_up(self.volunteer))
        self.refuses('shift.cancel', {**self.ask, 'shift_id': '999999'}, 422, 'no shift')


class CheckInTests(ActionTestCase):
    def setUp(self):
        super().setUp()
        self.event, self.slot = self.event_with_slot(starts_in=timedelta(minutes=30))
        self.librarian = Role.objects.create(name='Librarian', icon='x', permanent=True)

    def test_check_in_and_out_of_an_event_shift(self):
        self.slot.add_signup(self.volunteer)
        ask = {'person_id': self.me, 'shift_id': str(self.slot.pk)}
        with self.committing():
            started = self.ok('shift.start', ask)
        shift = Shift.objects.get()
        self.assertEqual(started, {'started': True, 'role': 'Greeter', 'started_at': events.iso(shift.start_time),
                                   'shift_id': str(self.slot.pk), 'event_id': str(self.event.pk), 'event_title': 'Christmas Bazaar'})
        self.assertEqual((shift.user, shift.event_role_slot, shift.clutched), (self.volunteer, self.slot, False))
        self.one('shift.started')
        self.assertIs(self.ok('shift.start', ask)['started'], False)   # already on it
        self.assertEqual(Shift.objects.count(), 1)

        Shift.objects.filter(pk=shift.pk).update(start_time=timezone.now() - timedelta(hours=2))
        with self.committing(), patch('events.reminders.send_shift_thanks') as thanks:
            ended = self.ok('shift.end', {'person_id': self.me})
        shift.refresh_from_db()
        self.assertEqual((ended['ended'], ended['role'], ended['ended_at'], ended['event_title']),
                         (True, 'Greeter', events.iso(shift.end_time), 'Christmas Bazaar'))
        self.assertAlmostEqual(ended['hours'], 2.0, places=1)
        self.assertEqual(ended['points'], PointsEntry.objects.get(shift=shift).amount)
        self.assertGreater(ended['points'], 0)
        thanks.assert_called_once()
        self.one('shift.completed')
        self.assertEqual(self.ok('shift.end', {'person_id': self.me}), {'ended': False})

    def test_check_in_at_an_event_refusals(self):
        ask = {'person_id': self.me, 'shift_id': str(self.slot.pk)}
        self.refuses('shift.start', ask, 409, 'not signed up')
        self.slot.add_signup(self.volunteer)
        EventRoleSlot.objects.filter(pk=self.slot.pk).update(start_time=timezone.now() + timedelta(hours=3),
                                                             end_time=timezone.now() + timedelta(hours=6))
        self.refuses('shift.start', ask, 409, 'an hour before')
        EventRoleSlot.objects.filter(pk=self.slot.pk).update(start_time=timezone.now() - timedelta(hours=6),
                                                             end_time=timezone.now() - timedelta(hours=3))
        self.refuses('shift.start', ask, 409, 'ended')
        self.refuses('shift.start', {'person_id': self.me}, 422, 'either')
        self.refuses('shift.start', {**ask, 'role': 'Librarian'}, 422, 'either')
        self.refuses('shift.start', {**ask, 'shift_id': '999999'}, 422, 'no shift')
        self.volunteer.is_active = False
        self.volunteer.save()
        self.refuses('shift.start', ask, 409, 'deactivated')
        self.assertFalse(Shift.objects.exists())

    def test_permanent_role_shift(self):
        ask = {'person_id': self.me, 'role': 'Librarian'}
        self.refuses('shift.start', ask, 409, 'not assigned')
        self.refuses('shift.start', {'person_id': self.me, 'role': 'Greeter'}, 422, 'event role')
        self.volunteer.profile.permanent_roles.add(self.librarian)
        started = self.ok('shift.start', ask)
        self.assertEqual((started['started'], started['role'], started['shift_id'], started['event_id']), (True, 'Librarian', '', ''))
        self.assertIs(self.ok('shift.start', {'person_id': self.me, 'role': str(self.librarian.pk)})['started'], False)

        # One shift at a time, here as at the kiosk.
        self.slot.add_signup(self.volunteer)
        self.refuses('shift.start', {'person_id': self.me, 'shift_id': str(self.slot.pk)}, 409, 'already checked in as Librarian')
        ended = self.ok('shift.end', {'person_id': self.me})
        self.assertEqual((ended['ended'], ended['role'], ended['event_id'], ended['event_title']), (True, 'Librarian', '', ''))
        self.assertEqual(Shift.objects.filter(end_time__isnull=False).count(), 1)

    def test_checking_out_ends_a_shift_another_app_keeps_alive(self):
        self.volunteer.profile.permanent_roles.add(self.librarian)
        shift = Shift.objects.create(user=self.volunteer, role=self.librarian)
        api_key, _plain = APIKey.create('Library door')
        ShiftHeartbeat.objects.create(shift=shift, api_key=api_key, last_seen=shift.start_time)
        self.assertIs(self.ok('shift.end', {'person_id': self.me})['ended'], True)
        self.assertFalse(ShiftHeartbeat.objects.exists())
        self.refuses('shift.end', {'person_id': '999999'}, 422, 'no volunteer')

    def test_a_repeated_check_out_is_replayed_not_redone(self):
        self.volunteer.profile.permanent_roles.add(self.librarian)
        Shift.objects.create(user=self.volunteer, role=self.librarian)
        first = self.ok('shift.end', {'person_id': self.me}, **{'Idempotency-Key': 'run-7:step-3'})
        Shift.objects.create(user=self.volunteer, role=self.librarian)   # a new shift later on
        self.assertEqual(self.ok('shift.end', {'person_id': self.me}, **{'Idempotency-Key': 'run-7:step-3'}), first)
        self.assertEqual(Shift.objects.filter(end_time__isnull=True).count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)


class TrainingAndVenueTests(ActionTestCase):
    def test_training_find(self):
        module = TrainingModule.objects.create(title='Food Safety Basics', description='x', icon='x', published=True)
        TrainingLesson.objects.create(training_module=module, title='Hands', content='Wash them. ' * 150)
        TrainingModule.objects.create(title='Welcome Desk', description='x', icon='x')
        TrainingModule.objects.create(title='Admin guide', description='x', icon='x', staff_only=True)
        found = self.ok('training.find', {'title': 'food'})
        self.assertEqual(found, {'found': True, 'count': 1, 'module_id': str(module.pk), 'title': 'Food Safety Basics',
                                 'published': True, 'minutes': module.get_total_length(),
                                 'url': f'http://testserver/en/training/module/{module.pk}/', 'titles': 'Food Safety Basics'})
        self.assertGreater(found['minutes'], 0)
        every = self.ok('training.find')
        self.assertEqual((every['count'], every['titles']), (2, 'Food Safety Basics, Welcome Desk'))   # never the staff guide
        self.assertEqual(self.ok('training.find', {'title': 'Admin'}), {'found': False, 'count': 0})

    def test_venue_find(self):
        hall = Venue.objects.create(name='Parish Hall', address='501 4th Ave', phone_number='604 555 0100', latitude=1, longitude=1)
        Venue.objects.create(name='Community Kitchen', latitude=1, longitude=1)
        self.assertEqual(self.ok('venue.find', {'name': 'hall'}), {
            'found': True, 'count': 1, 'venue_id': str(hall.pk), 'name': 'Parish Hall', 'address': '501 4th Ave',
            'phone': '604 555 0100', 'url': f'http://testserver/en/venues/{hall.pk}/', 'names': 'Parish Hall'})
        self.assertEqual(self.ok('venue.find')['names'], 'Community Kitchen, Parish Hall')
        self.assertEqual(self.ok('venue.find', {'name': 'castle'}), {'found': False, 'count': 0})

    def test_venue_post_note(self):
        hall = Venue.objects.create(name='Parish Hall', latitude=1, longitude=1)
        with self.committing():
            posted = self.ok('venue.post_note', {'venue': 'parish hall', 'note': ' Side entrance closed. '})
        note = VenueNote.objects.get()
        self.assertEqual(posted, {'note_id': str(note.pk), 'venue_id': str(hall.pk), 'venue': 'Parish Hall',
                                  'expires_at': events.iso(note.expires_at)})
        self.assertEqual((note.text, note.expires_after), ('Side entrance closed.', timedelta(days=7)))
        self.assertIn(note, hall.active_notes())
        self.assertEqual(self.one('venue.note_posted')['data']['note'], 'Side entrance closed.')

        self.refuses('venue.post_note', {'venue': 'Castle', 'note': 'Closed'}, 422, 'Castle')
        self.refuses('venue.post_note', {'venue': 'Parish Hall'}, 422, 'note')
        self.refuses('venue.post_note', {'venue': 'Parish Hall', 'note': 'x' * 501}, 422, 'note')
        self.assertEqual(VenueNote.objects.count(), 1)


class EveryActionTests(ActionTestCase):
    def test_every_action_refuses_an_empty_call_or_answers_within_its_manifest(self):
        """No action falls over without its settings: the required ones are named in a 422."""
        from . import manifest
        for action in manifest.ACTIONS:
            response = self.action(action['name'], self.key, {})
            required = [p['key'] for p in action['params'] if p['required']]
            if required:
                self.assertEqual(response.status_code, 422, action['name'])
                self.assertIn(required[0], response.json()['error']['message'])
            else:
                self.assertEqual(response.status_code, 200, action['name'])

    def test_lookups_do_not_store_answers_and_actions_do(self):
        self.ok('person.search', {}, **{'Idempotency-Key': 'k1'})
        self.assertFalse(IdempotencyRecord.objects.exists())
        self.ok('person.reactivate', {'person_id': self.me}, **{'Idempotency-Key': 'k2'})
        self.assertEqual(IdempotencyRecord.objects.get().action, 'person.reactivate')

    def test_nothing_can_be_deleted(self):
        """The connection changes things and never removes them."""
        from . import manifest
        for action in manifest.ACTIONS:
            self.assertNotRegex(action['name'], r'delete|remove|destroy', action['name'])
        self.assertTrue(User.objects.filter(pk=self.volunteer.pk).exists())
