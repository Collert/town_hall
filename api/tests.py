import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from api import shifts
from api.models import APIKey, ShiftHeartbeat
from events.models import Event, EventRoleSlot
from jobs.models import Role, Shift

User = get_user_model()


class APITestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.librarian = User.objects.create_user(username='librarian', password='pw', first_name='Olena')
        self.other = User.objects.create_user(username='other', password='pw')
        self.role = Role.objects.create(name='Librarian', icon='local_library', permanent=True)
        self.other_role = Role.objects.create(name='Front desk', icon='support_agent', permanent=True)
        self.event_role = Role.objects.create(name='Usher', icon='church')
        for role in (self.role, self.other_role, self.event_role):
            self.librarian.profile.permanent_roles.add(role)
        self.key, self.plaintext = APIKey.create('Library admin')

    def call(self, name, data=None, method='post', key=None, as_json=True, **params):
        data = {'town_hall_id': self.librarian.profile.id_code, 'role_id': self.role.pk, **(data or {})}
        headers = {'HTTP_AUTHORIZATION': f'Bearer {key or self.plaintext}'}
        url = reverse(name)
        if method == 'get':
            return self.client.get(url, data, **headers)
        if as_json:
            return self.client.post(url, json.dumps(data), content_type='application/json', **headers)
        return self.client.post(url, data, **headers)

    def open_shift(self, user=None):
        return Shift.objects.filter(user=user or self.librarian, end_time__isnull=True).first()


class KeyTests(APITestCase):
    def test_key_is_stored_as_hash_and_authenticates(self):
        self.assertTrue(self.plaintext.startswith('th_'))
        self.assertNotIn(self.plaintext, self.key.key_hash)
        self.assertEqual(APIKey.authenticate(self.plaintext), self.key)
        self.assertIsNone(APIKey.authenticate('th_wrong'))

    def test_missing_revoked_and_expired_keys_are_refused(self):
        self.assertEqual(self.client.get(reverse('api_index')).status_code, 401)
        self.assertEqual(self.call('api_shift_start', key='th_nope').status_code, 401)
        self.key.revoke()
        response = self.call('api_shift_start')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'invalid_api_key')
        old, plaintext = APIKey.create('Old', expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.call('api_shift_start', key=plaintext).status_code, 401)

    def test_index_describes_key_and_roles(self):
        response = self.client.get(reverse('api_index'), HTTP_X_TOWNHALL_API_KEY=self.plaintext)
        body = response.json()
        self.assertEqual(body['key']['name'], 'Library admin')
        self.assertEqual({r['id'] for r in body['roles']}, {self.role.pk, self.other_role.pk})
        self.assertEqual(body['heartbeat_timeout_seconds'], 300)
        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.last_used_at)

    def test_key_limited_to_roles(self):
        self.key.roles.set([self.other_role])
        response = self.call('api_shift_start')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'role_not_allowed')
        self.assertEqual(self.call('api_shift_start', {'role_id': self.other_role.pk}).status_code, 201)

    def test_wrong_method(self):
        response = self.call('api_shift_start', method='get')
        self.assertEqual(response.status_code, 405)


class ShiftTests(APITestCase):
    def test_start_is_idempotent(self):
        response = self.call('api_shift_start')
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body['started'])
        self.assertTrue(body['shift']['active'])
        self.assertTrue(body['shift']['tracked_by_heartbeat'])
        self.assertEqual(body['shift']['town_hall_id'], self.librarian.profile.id_code)
        again = self.call('api_shift_start', as_json=False)
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()['started'])
        self.assertEqual(Shift.objects.filter(user=self.librarian).count(), 1)

    def test_validation(self):
        self.assertEqual(self.call('api_shift_start', {'town_hall_id': ''}).json()['error']['code'], 'missing_parameter')
        self.assertEqual(self.call('api_shift_start', {'town_hall_id': '000000'}).status_code, 404)
        self.assertEqual(self.call('api_shift_start', {'role_id': 99999}).status_code, 404)
        response = self.call('api_shift_start', {'role_id': self.event_role.pk})
        self.assertEqual(response.json()['error']['code'], 'role_not_permanent')
        response = self.call('api_shift_start', {'town_hall_id': self.other.profile.id_code})
        self.assertEqual(response.json()['error']['code'], 'not_assigned')
        self.other.is_active = False
        self.other.save()
        self.assertEqual(self.call('api_shift_start', {'town_hall_id': self.other.profile.id_code}).status_code, 404)

    def test_heartbeat_starts_and_refreshes(self):
        response = self.call('api_shift_heartbeat')
        self.assertTrue(response.json()['started'])
        beat = ShiftHeartbeat.objects.get()
        ShiftHeartbeat.objects.filter(pk=beat.pk).update(last_seen=timezone.now() - timedelta(minutes=2))
        response = self.call('api_shift_heartbeat')
        self.assertFalse(response.json()['started'])
        beat.refresh_from_db()
        self.assertGreater(beat.last_seen, timezone.now() - timedelta(seconds=30))

    def test_heartbeat_without_start(self):
        response = self.call('api_shift_heartbeat', {'start': False})
        self.assertIsNone(response.json()['shift'])
        self.assertIsNone(self.open_shift())

    def test_end(self):
        self.call('api_shift_start')
        response = self.call('api_shift_end')
        body = response.json()
        self.assertTrue(body['ended'])
        self.assertFalse(body['shift']['active'])
        self.assertFalse(body['shift']['tracked_by_heartbeat'])
        self.assertIsNone(self.open_shift())
        self.assertFalse(ShiftHeartbeat.objects.exists())
        self.assertFalse(self.call('api_shift_end').json()['ended'])

    def test_status(self):
        self.assertIsNone(self.call('api_shift_status', method='get').json()['shift'])
        self.call('api_shift_start')
        self.assertTrue(self.call('api_shift_status', method='get').json()['shift']['active'])

    def test_other_shift_in_progress(self):
        Shift.objects.create(user=self.librarian, role=self.other_role)
        response = self.call('api_shift_start')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'other_shift_in_progress')

    def test_kiosk_shift_is_reported_but_never_timed_out(self):
        kiosk = Shift.objects.create(user=self.librarian, role=self.role)
        response = self.call('api_shift_start')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['shift']['tracked_by_heartbeat'])
        shifts.end_stale_shifts(now=timezone.now() + timedelta(hours=3))
        kiosk.refresh_from_db()
        self.assertIsNone(kiosk.end_time)


class StaleShiftTests(APITestCase):
    def start_shift_ago(self, minutes_since_beat):
        self.call('api_shift_start')
        shift = self.open_shift()
        last = timezone.now() - timedelta(minutes=minutes_since_beat)
        Shift.objects.filter(pk=shift.pk).update(start_time=last - timedelta(hours=2))
        ShiftHeartbeat.objects.filter(shift=shift).update(last_seen=last)
        return shift, last

    def test_shift_ends_at_last_heartbeat_after_timeout(self):
        shift, last = self.start_shift_ago(6)
        self.assertEqual(shifts.end_stale_shifts(), 1)
        shift.refresh_from_db()
        self.assertEqual(shift.end_time, last)
        self.assertAlmostEqual(shift.duration(), 2, places=2)
        self.assertIsNotNone(shift.end_impact_points)
        self.assertFalse(ShiftHeartbeat.objects.exists())

    def test_recent_heartbeat_keeps_shift_open(self):
        shift, _last = self.start_shift_ago(4)
        self.assertEqual(shifts.end_stale_shifts(), 0)
        self.assertIsNotNone(self.open_shift())

    def test_heartbeat_after_timeout_starts_a_new_shift(self):
        old, _last = self.start_shift_ago(10)
        response = self.call('api_shift_heartbeat')
        self.assertTrue(response.json()['started'])
        old.refresh_from_db()
        self.assertIsNotNone(old.end_time)
        self.assertNotEqual(self.open_shift().pk, old.pk)

    def test_middleware_and_command(self):
        self.start_shift_ago(6)
        cache.clear()  # the API call above already ran this minute's pass
        self.client.get('/en/')
        self.assertIsNone(self.open_shift())
        self.start_shift_ago(6)
        call_command('end_stale_shifts', stdout=open('/dev/null', 'w'))
        self.assertIsNone(self.open_shift())

    def test_ended_elsewhere_heartbeat_is_cleaned_up(self):
        shift, _last = self.start_shift_ago(6)
        shift.end_shift()  # e.g. at the kiosk
        end = Shift.objects.get(pk=shift.pk).end_time
        shifts.end_stale_shifts()
        self.assertEqual(Shift.objects.get(pk=shift.pk).end_time, end)
        self.assertFalse(ShiftHeartbeat.objects.exists())


class ConsoleAPIPageTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(username='staff', password='pw', is_staff=True)
        self.client.force_login(self.staff)

    def test_create_shows_key_once(self):
        response = self.client.post(reverse('console_settings_api'), {'name': 'Library', 'roles': [self.role.pk]}, follow=True)
        key = APIKey.objects.get(name='Library')
        self.assertEqual(list(key.roles.all()), [self.role])
        self.assertEqual(key.created_by, self.staff)
        plaintext = response.context['new_key']['key']
        self.assertEqual(APIKey.authenticate(plaintext), key)
        self.assertContains(response, plaintext)
        self.assertNotContains(self.client.get(reverse('console_settings_api')), plaintext)

    def test_create_needs_name(self):
        self.client.post(reverse('console_settings_api'), {'name': ' '})
        self.assertEqual(APIKey.objects.count(), 1)

    def test_revoke(self):
        self.client.post(reverse('console_settings_api_revoke', args=[self.key.pk]))
        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.revoked_at)
        self.assertIsNone(APIKey.authenticate(self.plaintext))

    def test_page_lists_tracked_shifts_and_role_ids(self):
        self.call('api_shift_start')
        response = self.client.get(reverse('console_settings_api'))
        self.assertContains(response, 'Olena')
        self.assertContains(response, f'<code class="api-mono">{self.role.pk}</code>', html=True)

    def test_volunteers_cannot_open_it(self):
        self.client.force_login(self.librarian)
        self.assertNotEqual(self.client.get(reverse('console_settings_api')).status_code, 200)


class ShiftModelTests(TestCase):
    def test_shift_creation_rebuilds_missing_profile_and_uses_slot_role(self):
        user = User.objects.create_user(username='shift-user', password='password123')
        user.profile.delete()

        role = Role.objects.create(name='Event Helper', icon='volunteer_activism')
        event = Event.objects.create(
            title='Community Day',
            description='Community event',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(hours=2),
            location='Town Hall',
            latitude=1, longitude=1,
        )
        slot = EventRoleSlot.objects.create(
            event=event,
            role=role,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=1),
        )

        shift = Shift.objects.create(user=user, event_role_slot=slot)

        self.assertEqual(shift.role, role)
        self.assertTrue(hasattr(user, 'profile'))
        self.assertEqual(user.profile.impact_points, 0)
