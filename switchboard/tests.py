import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from base.models import Level, Notification, PointsEntry
from education.models import TrainingModule, TrainingModuleCompletion
from events.models import Event, EventRoleSlot
from jobs.models import Role, Shift

from . import contract, delivery, events, manifest
from .models import AuthorizeCode, Delivery, IdempotencyRecord, SwitchboardConnection, digest

User = get_user_model()

CALLBACK = 'https://switchboard.example.org/buildings/callback'
WEBHOOK = 'https://switchboard.example.org/hooks/building/tok123'


class FakeSwitchboard:
    """Stands in for Switchboard's webhook endpoint at `switchboard.delivery._http`."""

    def __init__(self):
        self.requests = []   # (url, body bytes, headers)
        self.answers = []    # statuses (or exceptions) for the next calls; 200 once it runs out

    def __call__(self, url, body, headers):
        self.requests.append((url, body, headers))
        answer = self.answers.pop(0) if self.answers else 200
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def envelopes(self):
        return [json.loads(body) for _url, body, _headers in self.requests]

    def of_type(self, event_type):
        return [e for e in self.envelopes if e['type'] == event_type]


@override_settings(SWITCHBOARD_RUN_INLINE=True, ALLOWED_HOSTS=['*'])
class SwitchboardTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(username='staff', email='staff@example.org', password='pw',
                                              first_name='Sam', last_name='Staff', is_staff=True)
        self.volunteer = User.objects.create_user(username='maria', email='Maria@example.org', password='pw',
                                                  first_name='Maria', last_name='K.')
        self.fake = FakeSwitchboard()
        patcher = patch('switchboard.delivery._http', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.check_events_against_the_manifest)

    def connect(self, webhook=WEBHOOK, name='Holy Eucharist workflows'):
        connection, key = SwitchboardConnection.create(webhook, name=name, base_url='https://volunteers.example.org',
                                                       created_by=self.staff)
        return connection, key

    def api(self, method, path, key, data=None, **headers):
        extra = {'HTTP_AUTHORIZATION': f'Bearer {key}'} if key else {}
        extra.update({f'HTTP_{name.upper().replace("-", "_")}': value for name, value in headers.items()})
        call = getattr(self.client, method)
        if method == 'post':
            return call(path, data if isinstance(data, str) else json.dumps(data or {}), content_type='application/json', **extra)
        return call(path, **extra)

    def action(self, name, key, data=None, **headers):
        response = self.api('post', f'/switchboard/v1/actions/{name}', key, data, **headers)
        # Every answer is held to the manifest, whatever the test was about.
        if response.status_code == 200 and name in manifest.ACTIONS_BY_NAME:
            output = manifest.ACTIONS_BY_NAME[name]['output']
            self.assertEqual(contract.mismatches(output, response.json()), [], f'{name} broke its manifest')
            self.assertEqual(contract.undeclared(output, response.json()), [], f'{name} answered fields its manifest leaves out')
        return response

    def check_events_against_the_manifest(self):
        for envelope in self.fake.envelopes:
            trigger = manifest.TRIGGERS_BY_TYPE.get(envelope['type'])
            self.assertIsNotNone(trigger, f'{envelope["type"]} is not in the manifest')
            self.assertEqual(contract.mismatches(trigger['fields'], envelope['data']), [], f'{envelope["type"]} broke its manifest')
            self.assertEqual(contract.undeclared(trigger['fields'], envelope['data']), [],
                             f'{envelope["type"]} carries fields its manifest leaves out')
            # ...and carries every field it lists (one with nothing to say is '' or null, not left out).
            for spec in trigger['fields']:
                value = envelope['data']
                for part in spec['key'].split('.'):
                    self.assertIn(part, value, f'{envelope["type"]} lacks {spec["key"]}')
                    value = value[part]
            if any(spec['key'] == 'person.email' for spec in trigger['fields']):
                self.assertEqual(envelope['subject']['email'], envelope['data']['person']['email'])

    def event_with_slot(self, published=True, starts_in=timedelta(days=3), **slot_fields):
        start = timezone.now() + starts_in
        event = Event.objects.create(title='Christmas Bazaar', description='x', location='Parish Hall',
                                     start_date=start, end_date=start + timedelta(hours=8),
                                     latitude=1, longitude=1, published=published)
        role = Role.objects.create(name='Greeter', icon='waving_hand')
        slot = EventRoleSlot.objects.create(event=event, role=role, start_time=start, end_time=start + timedelta(hours=4),
                                            **{'required_qty': 2, **slot_fields})
        return event, slot

    def person(self, username, **fields):
        return User.objects.create_user(username=username, email=f'{username}@example.org', password='pw',
                                        **{'first_name': username.title(), **fields})

    def committing(self):
        """Run what the block queues for after the commit: that is when events are sent."""
        return self.captureOnCommitCallbacks(execute=True)

    def one(self, event_type):
        """The only event of this type sent so far."""
        found = self.fake.of_type(event_type)
        self.assertEqual(len(found), 1, f'{len(found)} {event_type} events, expected one')
        return found[0]


class ManifestTests(SwitchboardTestCase):
    def test_the_manifest_keeps_its_own_rules(self):
        self.assertEqual(contract.manifest_problems(manifest.TRIGGERS, manifest.ACTIONS), [])

    def test_manifest_shape(self):
        response = self.client.get('/.well-known/switchboard', secure=True, HTTP_HOST='volunteers.example.org')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        data = response.json()
        self.assertEqual((data['protocol'], data['building'], data['name']), ('1', 'town-hall', 'Town Hall'))
        self.assertEqual(data['authorize_url'], 'https://volunteers.example.org/switchboard/authorize')
        self.assertEqual(data['icon'], 'https://volunteers.example.org/static/base/town-hall.svg')
        self.assertEqual(data['token_url'], 'https://volunteers.example.org/switchboard/v1/token')
        self.assertEqual(data['api_base'], 'https://volunteers.example.org/switchboard/v1')
        self.assertTrue(data['instance'])

        types = {'string', 'int', 'number', 'bool', 'datetime', 'money'}
        triggers = {t['type']: t for t in data['triggers']}
        # Workflows store these, so the list only ever grows (the protocol's section 5).
        self.assertLessEqual({
            'volunteer.level_reached', 'volunteer.registered', 'shift.signed_up', 'shift.cancelled',
            'shift.completed', 'event.published', 'training.completed'}, set(triggers))
        for trigger in data['triggers']:
            self.assertTrue(trigger['title'] and trigger['description'] and trigger['summary'])
            for f in trigger['fields']:
                self.assertIn(f['type'], types)
                self.assertIn('example', f)
                self.assertTrue(f['label'])
        keys = lambda t: {f['key'] for f in triggers[t]['fields']}
        self.assertEqual(keys('volunteer.level_reached'), {
            'person.id', 'person.name', 'person.email', 'level', 'level_name', 'previous_level', 'impact_points'})
        self.assertLessEqual({'person.id', 'person.name', 'person.email', 'shift.id', 'shift.role', 'shift.starts_at',
                              'shift.ends_at', 'event.id', 'event.title', 'event.place'}, keys('shift.signed_up'))
        self.assertLessEqual({'person.id', 'shift.role', 'hours', 'points', 'event.title'}, keys('shift.completed'))

        actions = {a['name']: a for a in data['actions']}
        self.assertLessEqual({'person.find', 'person.get', 'person.invite', 'notification.send'}, set(actions))
        for action in data['actions']:
            self.assertEqual(action['method'], 'POST')
            self.assertEqual(action['path'], f"/actions/{action['name']}")
            self.assertIn(action['kind'], ('lookup', 'action'))
            for p in action['params']:
                self.assertIn(p['type'], types)
                self.assertIn('required', p)
            for f in action['output']:
                self.assertIn('example', f)
        self.assertEqual((actions['person.find']['kind'], actions['person.get']['kind']), ('lookup', 'lookup'))
        self.assertEqual((actions['person.invite']['kind'], actions['notification.send']['kind']), ('action', 'action'))
        self.assertLessEqual({'found', 'person_id', 'name', 'email', 'level', 'level_name', 'impact_points', 'total_hours'},
                             {f['key'] for f in actions['person.find']['output']})
        self.assertEqual([p['key'] for p in actions['notification.send']['params'] if p['required']],
                         ['person_id', 'title', 'message'])

    def test_every_handler_and_summary_matches_the_manifest(self):
        from .actions import HANDLERS
        self.assertEqual(set(HANDLERS), set(manifest.ACTIONS_BY_NAME))
        for trigger in manifest.TRIGGERS:
            keys = {f['key'] for f in trigger['fields']}
            for part in trigger['summary'].split('{')[1:]:
                self.assertIn(part.split('}')[0], keys)

    def test_manifest_is_open_before_first_run_setup(self):
        User.objects.all().delete()
        from base.middleware import FirstRunMiddleware
        FirstRunMiddleware.has_users = False
        self.assertEqual(self.client.get('/.well-known/switchboard').status_code, 200)


class HandshakeTests(SwitchboardTestCase):
    def authorize_url(self, **params):
        query = {'callback': CALLBACK, 'state': 'st4te', 'name': 'Holy Eucharist workflows', **params}
        return reverse('switchboard_authorize') + '?' + '&'.join(f'{k}={v}' for k, v in query.items())

    def approve(self, decision='allow'):
        self.client.force_login(self.staff)
        return self.client.post(reverse('switchboard_authorize'), {
            'callback': CALLBACK, 'state': 'st4te', 'name': 'Holy Eucharist workflows', 'decision': decision})

    def code(self):
        return parse_qs(urlsplit(self.approve()['Location']).query)['code'][0]

    def exchange(self, code, **extra):
        return self.client.post('/switchboard/v1/token', json.dumps({'code': code, 'webhook_url': WEBHOOK, **extra}),
                                content_type='application/json', secure=True, HTTP_HOST='volunteers.example.org')

    def test_signed_out_people_log_in_and_come_back(self):
        response = self.client.get(self.authorize_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])
        self.assertIn('next=/switchboard/authorize%3Fcallback', response['Location'])

    def test_non_staff_are_refused(self):
        self.client.force_login(self.volunteer)
        self.assertEqual(self.client.get(self.authorize_url()).status_code, 403)
        response = self.client.post(reverse('switchboard_authorize'), {'callback': CALLBACK, 'state': 's', 'decision': 'allow'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AuthorizeCode.objects.exists())

    def test_callback_must_be_a_web_address(self):
        self.client.force_login(self.staff)
        for bad in ('', 'javascript:alert(1)', 'ftp://switchboard.example.org/cb', '/relative'):
            self.assertEqual(self.client.get(self.authorize_url(callback=bad)).status_code, 400, bad)

    def test_consent_page_names_the_host(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.authorize_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'switchboard.example.org')
        self.assertContains(response, 'Holy Eucharist workflows')
        self.assertContains(response, 'name="state" value="st4te"')

    def test_cancel_goes_back_with_access_denied(self):
        response = self.approve('deny')
        query = parse_qs(urlsplit(response['Location']).query)
        self.assertTrue(response['Location'].startswith(CALLBACK))
        self.assertEqual((query['error'], query['state']), (['access_denied'], ['st4te']))
        self.assertFalse(AuthorizeCode.objects.exists())

    def test_approve_then_exchange_makes_a_connection(self):
        response = self.approve()
        query = parse_qs(urlsplit(response['Location']).query)
        self.assertTrue(response['Location'].startswith(CALLBACK))
        self.assertEqual(query['state'], ['st4te'])
        self.client.logout()

        response = self.exchange(query['code'][0], name='Workflows')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {'api_key', 'webhook_secret', 'connection_id', 'building', 'instance'})
        self.assertTrue(data['api_key'].startswith('sbk_'))
        self.assertEqual(data['building'], 'town-hall')
        connection = SwitchboardConnection.objects.get()
        self.assertEqual(data['connection_id'], str(connection.pk))
        self.assertEqual(connection.key_hash, digest(data['api_key']))
        self.assertNotIn(data['api_key'], json.dumps(list(SwitchboardConnection.objects.values()), default=str))
        self.assertEqual(connection.webhook_secret, data['webhook_secret'])
        self.assertEqual((connection.name, connection.webhook_url, connection.created_by), ('Workflows', WEBHOOK, self.staff))
        self.assertEqual(connection.base_url, 'https://volunteers.example.org')
        # The key works straight away.
        check = self.api('get', '/switchboard/v1/', data['api_key']).json()
        self.assertEqual(check, {'ok': True, 'building': 'town-hall', 'instance': data['instance'],
                                 'connection_id': data['connection_id']})

    def test_wrong_used_and_expired_codes(self):
        self.assertEqual(self.exchange('sbc_nope').json()['error']['code'], 'invalid_code')
        code = self.code()
        self.assertEqual(self.exchange(code).status_code, 200)
        again = self.exchange(code)
        self.assertEqual((again.status_code, again.json()['error']['code']), (400, 'invalid_code'))
        self.assertEqual(SwitchboardConnection.objects.count(), 1)

        code = self.code()
        AuthorizeCode.objects.filter(code_hash=digest(code)).update(expires_at=timezone.now() - timedelta(seconds=1))
        expired = self.exchange(code)
        self.assertEqual((expired.status_code, expired.json()['error']['code']), (400, 'invalid_code'))

    def test_code_lasts_ten_minutes(self):
        self.code()
        row = AuthorizeCode.objects.get()
        self.assertAlmostEqual((row.expires_at - row.created_at).total_seconds(), 600, delta=5)

    def test_token_request_must_be_valid(self):
        code = self.code()
        bad = self.client.post('/switchboard/v1/token', 'not json', content_type='application/json')
        self.assertEqual((bad.status_code, bad.json()['error']['code']), (400, 'invalid_request'))
        bad = self.client.post('/switchboard/v1/token', json.dumps({'code': code, 'webhook_url': 'ftp://x'}),
                               content_type='application/json')
        self.assertEqual((bad.status_code, bad.json()['error']['code']), (400, 'invalid_request'))
        self.assertEqual(self.client.get('/switchboard/v1/token').status_code, 405)
        self.assertEqual(self.exchange(code).status_code, 200)  # the code wasn't spent by the bad requests

    def test_code_of_someone_no_longer_staff_is_refused(self):
        code = self.code()
        User.objects.filter(pk=self.staff.pk).update(is_staff=False)
        self.assertEqual(self.exchange(code).json()['error']['code'], 'invalid_code')

    def test_reconnecting_the_same_webhook_replaces_the_connection(self):
        first = self.exchange(self.code()).json()
        second = self.exchange(self.code()).json()
        self.assertEqual(SwitchboardConnection.objects.count(), 1)
        self.assertEqual(self.api('get', '/switchboard/v1/', first['api_key']).status_code, 401)
        self.assertEqual(self.api('get', '/switchboard/v1/', second['api_key']).status_code, 200)


class ApiTests(SwitchboardTestCase):
    def setUp(self):
        super().setUp()
        self.connection, self.key = self.connect()

    def test_missing_and_wrong_keys(self):
        for key in (None, 'sbk_wrong'):
            for response in (self.api('get', '/switchboard/v1/', key), self.action('person.find', key, {'email': 'a@b.co'}),
                             self.api('delete', '/switchboard/v1/connection', key)):
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()['error']['code'], 'unauthorized')
                self.assertTrue(response.json()['error']['message'])

    def test_unknown_action_and_bad_bodies(self):
        response = self.action('person.fly', self.key)
        self.assertEqual((response.status_code, response.json()['error']['code']), (404, 'unknown_action'))
        for body in ('[1, 2]', 'not json', '"text"'):
            response = self.action('person.find', self.key, body)
            self.assertEqual((response.status_code, response.json()['error']['code']), (400, 'invalid_request'), body)
        self.assertEqual(self.api('get', '/switchboard/v1/actions/person.find', self.key).status_code, 405)

    def test_missing_param_says_which(self):
        response = self.action('person.find', self.key, {})
        self.assertEqual((response.status_code, response.json()['error']['code']), (422, 'invalid_params'))
        self.assertIn('email', response.json()['error']['message'])
        response = self.action('person.find', self.key, {'email': 'not-an-email'})
        self.assertEqual(response.status_code, 422)

    def test_unexpected_errors_are_server_error(self):
        with patch.dict('switchboard.actions.HANDLERS', {'person.find': lambda *a: 1 / 0}), self.assertLogs('switchboard', 'ERROR'):
            response = self.action('person.find', self.key, {'email': 'a@b.co'})
        self.assertEqual((response.status_code, response.json()['error']['code']), (500, 'server_error'))

    def test_person_find_matches_email_ignoring_case(self):
        Level.objects.all().delete()
        Level.objects.create(name='Seedling', numeric_name=10, min_points=0)
        Level.objects.create(name='Pillar', numeric_name=20, min_points=100)
        PointsEntry.objects.create(user=self.volunteer, amount=150, source=PointsEntry.ADJUSTMENT)
        shift = Shift.objects.create(user=self.volunteer, role=Role.objects.create(name='Greeter', icon='x'))
        Shift.objects.filter(pk=shift.pk).update(start_time=timezone.now() - timedelta(hours=2), end_time=timezone.now())

        response = self.action('person.find', self.key, {'email': 'MARIA@EXAMPLE.ORG'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'found': True, 'person_id': str(self.volunteer.pk), 'name': 'Maria K.', 'email': 'Maria@example.org',
            'level': 2, 'level_name': 'Pillar', 'impact_points': 150, 'total_hours': 2.0,
            'active': True, 'staff': False, 'phone': '', 'location': '', 'skills': '', 'permanent_roles': '',
            'reliability': 100, 'joined_at': events.iso(self.volunteer.date_joined)})
        self.assertEqual(set(response.json()), {f['key'] for f in manifest.ACTIONS_BY_NAME['person.find']['output']})

        missing = self.action('person.find', self.key, {'email': 'nobody@example.org'})
        self.assertEqual((missing.status_code, missing.json()), (200, {'found': False}))

    def test_person_get(self):
        found = self.action('person.get', self.key, {'person_id': str(self.volunteer.pk)}).json()
        self.assertEqual((found['found'], found['email']), (True, 'Maria@example.org'))
        as_number = self.action('person.get', self.key, {'person_id': self.volunteer.pk}).json()
        self.assertTrue(as_number['found'])
        for unknown in ('999999', 'abc'):
            response = self.action('person.get', self.key, {'person_id': unknown})
            self.assertEqual((response.status_code, response.json()), (200, {'found': False}))

    def test_person_invite_creates_once(self):
        with self.captureOnCommitCallbacks(execute=True), patch('base.email.notify') as notify:
            response = self.action('person.invite', self.key, {'name': 'Petro  Ivanovych Koval', 'email': 'petro@example.org'},
                                   **{'Idempotency-Key': 'run-1-step-2'})
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email='petro@example.org')
        self.assertEqual(response.json(), {'person_id': str(user.pk), 'created': True,
                                           'name': 'Petro Ivanovych Koval', 'email': 'petro@example.org'})
        self.assertEqual((user.first_name, user.last_name, user.is_staff), ('Petro', 'Ivanovych Koval', False))
        self.assertTrue(user.has_usable_password())  # so the set-password link and later resets work
        self.assertTrue(user.profile.id_code)
        sent = {call.args[0]: call.args[2] for call in notify.call_args_list}
        self.assertEqual(set(sent), {'welcome', 'password_reset'})
        self.assertIn('/password-reset/', sent['password_reset']['link'])

        # The same key again: the stored answer, no second account or email.
        with patch('base.email.notify') as notify:
            replay = self.action('person.invite', self.key, {'name': 'Petro Koval', 'email': 'petro@example.org'},
                                 **{'Idempotency-Key': 'run-1-step-2'})
        self.assertEqual((replay.status_code, replay.json()), (200, response.json()))
        notify.assert_not_called()
        self.assertEqual(User.objects.filter(email__iexact='petro@example.org').count(), 1)

        # A new key for an email that exists: that person, created false.
        again = self.action('person.invite', self.key, {'name': 'P', 'email': 'PETRO@example.org'}, **{'Idempotency-Key': 'run-2'})
        self.assertEqual(again.json(), {'person_id': str(user.pk), 'created': False,
                                        'name': 'Petro Ivanovych Koval', 'email': 'petro@example.org'})

    def test_person_invite_checks_params(self):
        for body in ({'name': 'Petro'}, {'email': 'petro@example.org'}, {'name': 'Petro', 'email': 'nope'}, {'name': ['x'], 'email': 'a@b.co'}):
            response = self.action('person.invite', self.key, body)
            self.assertEqual((response.status_code, response.json()['error']['code']), (422, 'invalid_params'), body)
        self.assertFalse(User.objects.filter(email='petro@example.org').exists())

    def test_notification_send_is_idempotent(self):
        body = {'person_id': str(self.volunteer.pk), 'title': 'Level 3!', 'message': 'Your staff card is ready.', 'link': '/profile/'}
        with patch('base.email.notify') as notify:
            first = self.action('notification.send', self.key, body, **{'Idempotency-Key': 'evt_1:card'})
            second = self.action('notification.send', self.key, body, **{'Idempotency-Key': 'evt_1:card'})
        notification = Notification.objects.get(user=self.volunteer)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {'sent': True, 'notification_id': str(notification.pk)})
        self.assertEqual((second.status_code, second.json()), (200, first.json()))
        self.assertEqual(notification.message, 'Level 3!: Your staff card is ready.')
        self.assertEqual(notification.link, 'http://testserver/profile/')
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(notify.call_args.args[:2], ('volunteer_message', self.volunteer))
        self.assertEqual(IdempotencyRecord.objects.filter(connection=self.connection).count(), 1)

        # Without the header nothing is remembered; another connection's same key is separate.
        with patch('base.email.notify'):
            self.action('notification.send', self.key, body)
            _other, other_key = self.connect('https://other.example.org/hooks/x')
            self.action('notification.send', other_key, body, **{'Idempotency-Key': 'evt_1:card'})
        self.assertEqual(Notification.objects.filter(user=self.volunteer).count(), 3)

    def test_notification_send_errors_are_replayed_too(self):
        body = {'person_id': '999999', 'title': 'Hi', 'message': 'There'}
        first = self.action('notification.send', self.key, body, **{'Idempotency-Key': 'k1'})
        self.assertEqual((first.status_code, first.json()['error']['code']), (422, 'invalid_params'))
        second = self.action('notification.send', self.key, {**body, 'person_id': str(self.volunteer.pk)}, **{'Idempotency-Key': 'k1'})
        self.assertEqual((second.status_code, second.json()), (422, first.json()))
        self.assertFalse(Notification.objects.exists())

        bad_link = self.action('notification.send', self.key, {**body, 'person_id': str(self.volunteer.pk), 'link': 'javascript:x'})
        self.assertEqual(bad_link.status_code, 422)
        User.objects.filter(pk=self.volunteer.pk).update(is_active=False)
        inactive = self.action('notification.send', self.key, {**body, 'person_id': str(self.volunteer.pk)})
        self.assertEqual((inactive.status_code, inactive.json()['error']['code']), (409, 'conflict'))
        too_long = self.action('notification.send', self.key, body, **{'Idempotency-Key': 'k' * 201})
        self.assertEqual(too_long.status_code, 422)

    def test_switchboard_disconnects(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.api('delete', '/switchboard/v1/connection', self.key)
        self.assertEqual((response.status_code, response.json()), (200, {'ok': True}))
        self.assertFalse(SwitchboardConnection.objects.exists())
        self.assertEqual(self.api('get', '/switchboard/v1/', self.key).status_code, 401)
        with self.captureOnCommitCallbacks(execute=True):
            User.objects.create_user(username='new', email='new@example.org')
        self.assertEqual(self.fake.requests, [])


class EventTests(SwitchboardTestCase):
    def setUp(self):
        super().setUp()
        Level.objects.all().delete()
        Level.objects.create(name='Seedling', numeric_name=1, min_points=0)
        Level.objects.create(name='Helper', numeric_name=5, min_points=100)
        Level.objects.create(name='Pillar', numeric_name=9, min_points=500)

    def test_nothing_is_sent_or_stored_without_a_connection(self):
        with self.captureOnCommitCallbacks(execute=True):
            User.objects.create_user(username='new', email='new@example.org')
            PointsEntry.objects.create(user=self.volunteer, amount=600, source=PointsEntry.ADJUSTMENT)
        self.assertEqual(self.fake.requests, [])
        self.assertFalse(Delivery.objects.exists())

    def test_level_reached_envelope_headers_and_signature(self):
        connection, _key = self.connect()
        with self.captureOnCommitCallbacks(execute=True):
            PointsEntry.objects.create(user=self.volunteer, amount=120, source=PointsEntry.ADJUSTMENT)
        (url, body, headers), = [r for r in self.fake.requests if r[2]['X-Switchboard-Event'] == 'volunteer.level_reached']
        envelope = json.loads(body)
        self.assertEqual(url, WEBHOOK)
        self.assertRegex(envelope['id'], r'^evt_[0-9a-f]{20}$')
        self.assertEqual(envelope['type'], 'volunteer.level_reached')
        self.assertEqual(envelope['source'], {'building': 'town-hall', 'instance': 'https://volunteers.example.org'})
        self.assertRegex(envelope['occurred_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$')
        person = {'id': str(self.volunteer.pk), 'name': 'Maria K.', 'email': 'Maria@example.org'}
        self.assertEqual(envelope['subject'], {'type': 'person', **person})
        self.assertEqual(envelope['data'], {'person': person, 'level': 2, 'level_name': 'Helper',
                                            'previous_level': 1, 'impact_points': 120})
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(headers['User-Agent'], 'TownHall/1.0 (Switchboard)')
        self.assertEqual(headers['X-Switchboard-Event'], 'volunteer.level_reached')
        self.assertEqual(headers['X-Switchboard-Delivery'], envelope['id'])
        expected = hmac.new(connection.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertEqual(headers['X-Switchboard-Signature'], f'sha256={expected}')

        row = Delivery.objects.get(event_type='volunteer.level_reached')
        self.assertEqual((row.status, row.attempts, row.response_status, row.body), ('delivered', 1, 200, ''))
        self.assertEqual(row.summary, 'Maria K. reached level 2')
        connection.refresh_from_db()
        self.assertIsNotNone(connection.last_delivery_at)
        self.assertEqual(connection.state, 'ok')

    def test_level_event_only_when_the_level_goes_up(self):
        self.connect()
        with self.captureOnCommitCallbacks(execute=True):
            PointsEntry.objects.create(user=self.volunteer, amount=50, source=PointsEntry.ADJUSTMENT)   # still level 1
        self.assertEqual(self.fake.of_type('volunteer.level_reached'), [])
        with self.captureOnCommitCallbacks(execute=True):
            entry = PointsEntry.objects.create(user=self.volunteer, amount=500, source=PointsEntry.ADJUSTMENT)  # 1 -> 3
        (envelope,) = self.fake.of_type('volunteer.level_reached')
        self.assertEqual((envelope['data']['level'], envelope['data']['level_name'], envelope['data']['previous_level']),
                         (3, 'Pillar', 1))
        with self.captureOnCommitCallbacks(execute=True):
            PointsEntry.objects.create(user=self.volunteer, amount=10, source=PointsEntry.ADJUSTMENT)   # same level
            entry.delete()                                                                              # down to level 1
            self.volunteer.profile.save()                                                               # unrelated save
        self.assertEqual(len(self.fake.of_type('volunteer.level_reached')), 1)

    def test_registered(self):
        self.connect()
        with self.captureOnCommitCallbacks(execute=True):
            user = User.objects.create_user(username='new', email='new@example.org', first_name='Nina')
            user.first_name = 'Nina B.'
            user.save()
        (envelope,) = self.fake.of_type('volunteer.registered')
        self.assertEqual(envelope['subject']['email'], 'new@example.org')
        self.assertEqual(envelope['data']['person'], {'id': str(user.pk), 'name': 'Nina', 'email': 'new@example.org'})
        self.assertIn('registered_at', envelope['data'])

    def test_signed_up_and_cancelled(self):
        event, slot = self.event_with_slot()
        self.connect()
        with self.captureOnCommitCallbacks(execute=True):
            slot.add_signup(self.volunteer)
        (envelope,) = self.fake.of_type('shift.signed_up')
        self.assertEqual(envelope['data']['person']['email'], 'Maria@example.org')
        self.assertEqual(envelope['data']['shift'], {
            'id': str(slot.pk), 'role': 'Greeter',
            'starts_at': events.iso(slot.start_time), 'ends_at': events.iso(slot.end_time),
            'area': '', 'needed': 2, 'taken': 1, 'open_spots': 1})
        self.assertEqual(envelope['data']['event'], {'id': str(event.pk), 'title': 'Christmas Bazaar', 'place': 'Parish Hall'})

        with self.captureOnCommitCallbacks(execute=True):
            self.volunteer.commitments.remove(slot)   # the other side of the M2M
        (cancelled,) = self.fake.of_type('shift.cancelled')
        self.assertEqual(cancelled['data']['shift']['id'], str(slot.pk))
        self.assertEqual((cancelled['data']['shift']['taken'], cancelled['data']['late']), (0, False))
        self.assertEqual(cancelled['subject']['id'], str(self.volunteer.pk))
        self.assertNotEqual(cancelled['id'], envelope['id'])

    def test_shift_completed_once_with_points(self):
        event, slot = self.event_with_slot()
        self.connect()
        with self.captureOnCommitCallbacks(execute=True):
            shift = Shift.objects.create(user=self.volunteer, event_role_slot=slot)
        self.assertEqual(self.fake.of_type('shift.completed'), [])
        Shift.objects.filter(pk=shift.pk).update(start_time=timezone.now() - timedelta(hours=3))
        shift.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            shift.end_shift()
        (envelope,) = self.fake.of_type('shift.completed')
        data = envelope['data']
        self.assertEqual(data['shift']['role'], 'Greeter')
        self.assertAlmostEqual(data['hours'], 3.0, places=1)
        self.assertEqual(data['points'], PointsEntry.objects.get(shift=shift).amount)
        self.assertGreater(data['points'], 0)
        self.assertEqual(data['event'], {'id': str(event.pk), 'title': 'Christmas Bazaar'})
        self.assertEqual(envelope['occurred_at'], events.iso(shift.end_time))

        # Editing the finished shift or recalculating points doesn't complete it again.
        with self.captureOnCommitCallbacks(execute=True):
            shift.end_time = shift.end_time + timedelta(minutes=30)
            shift.save()
            call_command('recalculate_points', verbosity=0)
        self.assertEqual(len(self.fake.of_type('shift.completed')), 1)

    def test_permanent_role_shift_has_no_event(self):
        self.connect()
        role = Role.objects.create(name='Librarian', icon='x', permanent=True)
        with self.captureOnCommitCallbacks(execute=True):
            Shift.objects.create(user=self.volunteer, role=role).end_shift()
        (envelope,) = self.fake.of_type('shift.completed')
        self.assertEqual(envelope['data']['shift']['role'], 'Librarian')
        self.assertEqual(envelope['data']['event'], {'id': '', 'title': ''})

    def test_event_published(self):
        self.connect()
        with self.captureOnCommitCallbacks(execute=True):
            event, _slot = self.event_with_slot(published=False)
            event.title = 'Christmas Bazaar 2026'
            event.save()
        self.assertEqual(self.fake.of_type('event.published'), [])
        with self.captureOnCommitCallbacks(execute=True):
            event.published = True
            event.save(update_fields=['published'])
        (envelope,) = self.fake.of_type('event.published')
        self.assertEqual(envelope['subject'], {'type': 'event', 'id': str(event.pk), 'name': 'Christmas Bazaar 2026'})
        self.assertEqual(envelope['data']['event']['url'], f'https://volunteers.example.org/en/opportunities/{event.pk}/')
        self.assertEqual(envelope['data']['event']['place'], 'Parish Hall')
        with self.captureOnCommitCallbacks(execute=True):
            event.save()   # already published
        self.assertEqual(len(self.fake.of_type('event.published')), 1)

    def test_training_completed(self):
        self.connect()
        module = TrainingModule.objects.create(title='Food Safety Basics', description='x')
        with self.captureOnCommitCallbacks(execute=True):
            TrainingModuleCompletion.objects.create(training_module=module, user=self.volunteer)
        (envelope,) = self.fake.of_type('training.completed')
        self.assertEqual(envelope['data']['module'], {'id': str(module.pk), 'title': 'Food Safety Basics'})
        self.assertEqual(envelope['data']['person']['email'], 'Maria@example.org')

    def test_same_event_id_for_every_connection_each_signed_with_its_secret(self):
        first, _k = self.connect()
        second, _k = self.connect('https://other.example.org/hooks/building/abc')
        with self.captureOnCommitCallbacks(execute=True):
            User.objects.create_user(username='new', email='new@example.org')
        self.assertEqual(len(self.fake.requests), 2)
        self.assertEqual(len({e['id'] for e in self.fake.envelopes}), 1)
        by_url = {url: (body, headers) for url, body, headers in self.fake.requests}
        for connection in (first, second):
            body, headers = by_url[connection.webhook_url]
            self.assertEqual(headers['X-Switchboard-Signature'], delivery.sign(connection.webhook_secret, body))
        self.assertNotEqual(first.webhook_secret, second.webhook_secret)

    def test_emitting_never_breaks_the_request(self):
        self.connect()
        with patch('switchboard.events.emit', side_effect=RuntimeError('boom')), self.assertLogs('switchboard', 'ERROR'):
            user = User.objects.create_user(username='new', email='new@example.org')
        self.assertTrue(user.pk)
        with patch('switchboard.models.Delivery.objects.create', side_effect=RuntimeError('boom')), \
                self.assertLogs('switchboard', 'ERROR'):
            other = User.objects.create_user(username='new2', email='new2@example.org')
        self.assertTrue(User.objects.filter(pk=other.pk).exists())


class DeliveryTests(SwitchboardTestCase):
    def emit(self):
        with self.captureOnCommitCallbacks(execute=True):
            User.objects.create_user(username=f'u{User.objects.count()}', email=f'u{User.objects.count()}@example.org')
        return Delivery.objects.order_by('-pk').first()

    def retry(self, row):
        """Run the pass that sends what is due, as if the wait had passed."""
        row.refresh_from_db()
        delivery.run_due(now=row.next_attempt_at)
        row.refresh_from_db()

    def test_retries_with_growing_waits_then_delivers(self):
        connection, _key = self.connect()
        self.fake.answers = [500, delivery.DeliveryError('timed out'), 503]
        row = self.emit()
        self.assertEqual((row.status, row.attempts, row.response_status), ('pending', 1, 500))
        waits = []
        for _try in range(3):
            row.refresh_from_db()
            waits.append(round((row.next_attempt_at - timezone.now()).total_seconds()))
            self.assertEqual(delivery.run_due(), 0)   # not due yet
            self.retry(row)
        self.assertEqual(waits, [5, 30, 120])
        self.assertEqual((row.status, row.attempts, row.response_status, row.error), ('delivered', 4, 200, ''))
        # Every try sent the same body and event id.
        self.assertEqual(len(self.fake.requests), 4)
        self.assertEqual(len({body for _u, body, _h in self.fake.requests}), 1)
        self.assertEqual(len({h['X-Switchboard-Delivery'] for _u, _b, h in self.fake.requests}), 1)
        connection.refresh_from_db()
        self.assertEqual((connection.failures, connection.state), (0, 'ok'))

    def test_gives_up_after_five_tries(self):
        connection, _key = self.connect()
        self.fake.answers = [500] * 5
        row = self.emit()
        waits = []
        while row.status == 'pending':
            waits.append(round((row.next_attempt_at - timezone.now()).total_seconds()))
            self.retry(row)
        self.assertEqual(waits, [5, 30, 120, 600])
        self.assertEqual((row.status, row.attempts, row.body), ('failed', 5, ''))
        self.assertIsNone(row.next_attempt_at)
        self.assertEqual(len(self.fake.requests), 5)
        self.assertEqual(delivery.run_due(now=timezone.now() + timedelta(days=1)), 0)
        connection.refresh_from_db()
        self.assertEqual((connection.failures, connection.state), (5, 'failing'))
        self.assertIsNone(connection.broken_at)
        # It still gets new events, and a success clears the failing state.
        row = self.emit()
        connection.refresh_from_db()
        self.assertEqual((row.status, connection.state), ('delivered', 'ok'))

    def test_network_errors_are_recorded(self):
        self.connect()
        self.fake.answers = [delivery.DeliveryError('Connection refused')]
        row = self.emit()
        self.assertEqual((row.status, row.response_status, row.error), ('pending', None, 'Connection refused'))

    def test_410_marks_the_connection_broken_and_stops(self):
        connection, key = self.connect()
        self.fake.answers = [500, 410]
        first = self.emit()
        second = self.emit()
        self.assertEqual((first.status, second.status, second.response_status), ('pending', 'failed', 410))
        first.refresh_from_db()
        self.assertEqual(first.status, 'failed')   # the one waiting for a retry stops too
        connection.refresh_from_db()
        self.assertEqual(connection.state, 'broken')
        self.assertTrue(SwitchboardConnection.objects.filter(pk=connection.pk).exists())
        self.assertEqual(delivery.run_due(now=timezone.now() + timedelta(days=1)), 0)
        before = len(self.fake.requests)
        self.emit()
        self.assertEqual(len(self.fake.requests), before)   # nothing more is sent to it
        self.assertEqual(self.api('get', '/switchboard/v1/', key).status_code, 200)  # the key isn't revoked by us

    def test_a_try_is_claimed_once(self):
        self.connect()
        self.fake.answers = [500]
        row = self.emit()
        during = []
        real = self.fake.__call__

        def slow_switchboard(url, body, headers):
            # While this try is out, another worker's pass finds nothing to send.
            during.append((delivery.run_due(now=timezone.now() + timedelta(seconds=30)), delivery.attempt(row.pk)))
            return real(url, body, headers)

        with patch('switchboard.delivery._http', slow_switchboard):
            self.retry(row)
        self.assertEqual(during, [(0, 'pending')])
        self.assertEqual((row.status, row.attempts), ('delivered', 2))
        self.assertEqual(delivery.attempt(row.pk), 'delivered')   # finished: never sent again
        self.assertEqual(len(self.fake.requests), 2)

    def test_log_is_pruned_after_two_weeks(self):
        connection, _key = self.connect()
        old = timezone.now() - timedelta(days=15)
        Delivery.objects.create(connection=connection, event_id='evt_old', event_type='volunteer.registered',
                                status='delivered', created_at=old)
        Delivery.objects.create(connection=connection, event_id='evt_new', event_type='volunteer.registered', status='delivered')
        IdempotencyRecord.objects.create(connection=connection, key='old', action='person.invite', status=200, response={}, created_at=old)
        IdempotencyRecord.objects.create(connection=connection, key='week', action='person.invite', status=200, response={},
                                         created_at=timezone.now() - timedelta(days=8))
        call_command('send_switchboard_deliveries', verbosity=0)
        self.assertEqual(list(Delivery.objects.values_list('event_id', flat=True)), ['evt_new'])
        self.assertEqual(list(IdempotencyRecord.objects.values_list('key', flat=True)), ['week'])

    def test_signature_is_hmac_sha256_of_the_body(self):
        self.assertEqual(delivery.sign('secret', b'{}'),
                         'sha256=' + hmac.new(b'secret', b'{}', hashlib.sha256).hexdigest())


class ConsoleTests(SwitchboardTestCase):
    def test_backend_page_lists_connections_and_deliveries(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('console_settings_backend'))
        self.assertContains(response, 'id="switchboard"')
        self.assertContains(response, 'http://testserver')
        self.assertContains(response, 'No Switchboard is connected')

        connection, _key = self.connect()
        with self.captureOnCommitCallbacks(execute=True):
            User.objects.create_user(username='new', email='new@example.org', first_name='Nina')
        response = self.client.get(reverse('console_settings_backend'))
        self.assertContains(response, 'Holy Eucharist workflows')
        self.assertContains(response, 'switchboard.example.org')
        self.assertContains(response, 'Sam Staff')
        self.assertContains(response, 'Nina registered')
        self.assertContains(response, 'volunteer.registered')
        self.assertContains(response, reverse('console_switchboard_disconnect', args=[connection.pk]))
        self.assertNotContains(response, connection.webhook_secret)
        self.assertNotContains(response, 'Failing')

        SwitchboardConnection.objects.filter(pk=connection.pk).update(failures=3, last_error='Switchboard answered 500')
        response = self.client.get(reverse('console_settings_backend'))
        self.assertContains(response, 'Failing')
        self.assertContains(response, 'Switchboard answered 500')
        SwitchboardConnection.objects.filter(pk=connection.pk).update(broken_at=timezone.now())
        self.assertContains(self.client.get(reverse('console_settings_backend')), 'Broken')

    def test_staff_disconnect(self):
        connection, key = self.connect()
        url = reverse('console_switchboard_disconnect', args=[connection.pk])
        self.client.force_login(self.volunteer)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertTrue(SwitchboardConnection.objects.exists())
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].endswith('#switchboard'))
        self.assertFalse(SwitchboardConnection.objects.exists())
        self.assertFalse(Delivery.objects.exists())
        self.assertEqual(self.api('get', '/switchboard/v1/', key).status_code, 401)
        with self.captureOnCommitCallbacks(execute=True):
            User.objects.create_user(username='new', email='new@example.org')
        self.assertEqual(self.fake.requests, [])
