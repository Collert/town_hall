import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from api.models import APIKey
from api.views import register_heartbeat
from events.models import Event, EventRoleSlot
from jobs.models import Role, Shift


class RegisterHeartbeatTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(username='heartbeat-user', password='password123')
        self.role = Role.objects.create(name='Support', icon='support_agent')
        self.api_key = APIKey.objects.create(
            key='test-api-key',
            name='Test API Key',
            expires_at=timezone.now() + timedelta(days=1),
        )

    def test_register_heartbeat_creates_shift_for_authenticated_user(self):
        request = self.factory.post(
            '/api/register-heartbeat/',
            {'role_id': self.role.id},
            HTTP_X_TOWNHALL_API_KEY=self.api_key.key,
        )
        request.user = self.user

        response = register_heartbeat(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['status'], 'success')
        self.assertTrue(
            Shift.objects.filter(user=self.user, role=self.role, end_time__isnull=True).exists()
        )


class ShiftModelTests(TestCase):
    def test_shift_creation_rebuilds_missing_profile_and_uses_slot_role(self):
        user = get_user_model().objects.create_user(username='shift-user', password='password123')
        user.profile.delete()

        role = Role.objects.create(name='Event Helper', icon='volunteer_activism')
        event = Event.objects.create(
            title='Community Day',
            description='Community event',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(hours=2),
            location='Town Hall',
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
