"""Web push notifications, the PWA endpoints and the listmonk messenger (base/push.py)."""
import base64
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event

from . import push
from .models import PushSubscription, SiteSettings


def device(user, n=1):
    return PushSubscription.objects.create(
        user=user, endpoint=f'https://push.example.com/send/{user.pk}-{n}', p256dh='key', auth='secret')


def make_event(**extra):
    start = timezone.now() + timedelta(days=5)
    return Event.objects.create(title='Spring Clean-up', description='Bring gloves.', location='Town Hall',
                                latitude=49.28, longitude=-123.12, start_date=start,
                                end_date=start + timedelta(hours=3), **extra)


class FakeResponse:
    def __init__(self, status):
        self.status_code = status


@override_settings(PUSH_RUN_INLINE=True)
class PushSendingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('vera', 'vera@example.com', 'pw', first_name='Vera')
        self.sent = []
        patcher = mock.patch('pywebpush.webpush', side_effect=lambda **kw: self.sent.append(kw))
        patcher.start()
        self.addCleanup(patcher.stop)

    def payloads(self):
        return [json.loads(kw['data']) for kw in self.sent]

    def test_keys_are_made_once(self):
        key = push.public_key()
        self.assertTrue(key)
        self.assertEqual(push.public_key(), key)
        self.assertTrue(SiteSettings.objects.get(pk=1).vapid_private_key.startswith('-----BEGIN'))

    def test_email_notify_also_pushes(self):
        from .email import notify

        device(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            notify('endorsement_received', self.user, {'from_name': 'Ola', 'skills': 'Cooking', 'link': 'https://x.test/p/'})
        self.assertEqual(self.payloads(), [{'title': 'Ola endorsed you', 'body': 'Cooking', 'url': 'https://x.test/p/'}])

    def test_switched_off_category_and_no_device_send_nothing(self):
        from .email import notify

        with self.captureOnCommitCallbacks(execute=True):
            notify('endorsement_received', self.user, {'from_name': 'Ola'})
        device(self.user)
        SiteSettings.objects.filter(pk=1).update(push_categories={'endorsement_received': False})
        cache.clear()
        with self.captureOnCommitCallbacks(execute=True):
            notify('endorsement_received', self.user, {'from_name': 'Ola'})
            notify('training_completed', self.user, {'module': 'Safety'})  # no push category
        self.assertEqual(self.sent, [])

    def test_gone_device_is_forgotten(self):
        from pywebpush import WebPushException

        sub = device(self.user)
        with mock.patch('pywebpush.webpush', side_effect=WebPushException('gone', response=FakeResponse(410))):
            self.assertFalse(push.send(sub, {'title': 't'}))
        self.assertFalse(PushSubscription.objects.exists())

    def test_publishing_an_event_pushes_everyone(self):
        staff = User.objects.create_user('staff', 's@example.com', 'pw', is_staff=True)
        device(self.user)
        device(staff)
        event = make_event(published=False)
        self.client.force_login(staff)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('console_event_publish', args=[event.pk]))
        self.assertEqual(len(self.sent), 2)
        payload = self.payloads()[0]
        self.assertEqual(payload['title'], 'New opportunity: Spring Clean-up')
        self.assertTrue(payload['url'].endswith(reverse('opportunity_detail', args=[event.pk])))

    def test_leadership_change_pushes(self):
        from events import leadership

        device(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            leadership.notify_person(self.user, 'You lead %(role)s at %(event)s.', {'role': 'Ushers', 'event': 'Gala'}, '/en/x/')
        self.assertEqual(self.payloads(), [{'title': 'Gala', 'body': 'You lead Ushers at Gala.', 'url': '/en/x/'}])


class DeviceRegistrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('vera', 'vera@example.com', 'pw')
        self.data = {'endpoint': 'https://fcm.googleapis.com/fcm/send/abc', 'keys': {'p256dh': 'p', 'auth': 'a'}}

    def post(self, name, data):
        return self.client.post(reverse(name), json.dumps(data), content_type='application/json')

    def test_subscribe_needs_sign_in(self):
        self.assertEqual(self.post('push_subscribe', self.data).status_code, 302)
        self.assertFalse(PushSubscription.objects.exists())

    def test_subscribe_moves_device_to_whoever_signed_in(self):
        other = User.objects.create_user('ola', 'ola@example.com', 'pw')
        self.client.force_login(other)
        self.assertEqual(self.post('push_subscribe', self.data).status_code, 200)
        self.client.force_login(self.user)
        self.post('push_subscribe', self.data)
        self.assertEqual(PushSubscription.objects.get().user, self.user)

    def test_rejects_bad_subscription(self):
        self.client.force_login(self.user)
        for endpoint in ('http://fcm.googleapis.com/x', 'https://intranet.test/admin', 'https://fcm.googleapis.com.evil.test/'):
            response = self.post('push_subscribe', {'endpoint': endpoint, 'keys': {'p256dh': 'p', 'auth': 'a'}})
            self.assertEqual(response.status_code, 400)
        self.assertFalse(PushSubscription.objects.exists())

    def test_logout_and_unsubscribe_forget_the_device(self):
        self.client.force_login(self.user)
        self.post('push_subscribe', self.data)
        self.client.get(reverse('logout'))
        self.assertFalse(PushSubscription.objects.exists())

        self.client.force_login(self.user)
        self.post('push_subscribe', self.data)
        self.post('push_unsubscribe', {'endpoint': self.data['endpoint']})
        self.assertFalse(PushSubscription.objects.exists())

    def test_renew_swaps_the_endpoint(self):
        device(self.user)
        old = PushSubscription.objects.get().endpoint
        response = self.post('push_renew', {'old_endpoint': old, 'subscription': self.data})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PushSubscription.objects.get().endpoint, self.data['endpoint'])
        self.assertEqual(self.post('push_renew', {'old_endpoint': old, 'subscription': self.data}).status_code, 404)

    def test_service_worker_offline_page_and_manifest(self):
        response = self.client.get('/sw.js')
        self.assertEqual(response['Content-Type'], 'application/javascript')
        self.assertContains(response, push.public_key())
        self.assertContains(self.client.get('/offline/'), 'offline')
        manifest = self.client.get('/site.webmanifest').json()
        self.assertEqual(manifest['scope'], '/')
        self.assertIn('any', {icon['purpose'] for icon in manifest['icons']})

    def test_prompt_only_for_signed_in_people(self):
        home = reverse('home')
        self.assertNotContains(self.client.get(home), 'id="push-prompt"')
        self.client.force_login(self.user)
        self.assertContains(self.client.get(home), 'id="push-prompt"')

    def test_listmonk_attribute_lists_endpoints(self):
        from .listmonk import subscriber_attribs

        device(self.user)
        self.assertEqual(subscriber_attribs(self.user)[push.ATTRIB], [f'https://push.example.com/send/{self.user.pk}-1'])


class ListmonkMessengerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('vera', 'vera@example.com', 'pw')
        self.sub = device(self.user)
        self.stranger = device(User.objects.create_user('ola', 'ola@example.com', 'pw'))

    def deliver(self, attribs, password=None):
        auth = base64.b64encode(f'{push.MESSENGER_USER}:{password or push.messenger_password()}'.encode()).decode()
        body = {
            'subject': 'New shifts open',
            'body': 'Sign up for the bake sale\nhttps://lists.test/link/1/2/3\n[[town-hall-push]]\n'
                    'link: https://lists.test/link/9/2/3\nview: <img src="https://lists.test/campaign/2/3/px.png" alt="" />',
            'content_type': 'plain',
            'recipients': [{'uuid': 'u', 'email': 'vera@example.com', 'name': 'Vera', 'attribs': attribs, 'status': 'enabled'}],
            'campaign': {'uuid': 'c', 'name': 'Bake sale', 'tags': []},
        }
        with mock.patch.object(push, 'send', return_value=True) as send:
            response = self.client.post(reverse('push_listmonk'), json.dumps(body), content_type='application/json',
                                        HTTP_AUTHORIZATION=f'Basic {auth}')
        return response, send

    def test_needs_basic_auth(self):
        response, send = self.deliver({push.ATTRIB: [self.sub.endpoint]}, password='wrong')
        self.assertEqual(response.status_code, 401)
        send.assert_not_called()

    def test_pushes_only_the_recipients_own_devices(self):
        response, send = self.deliver({push.ATTRIB: [self.sub.endpoint, self.stranger.endpoint], 'town_hall_id': self.user.pk})
        self.assertEqual(response.json(), {'sent': 1})
        subscription, payload = send.call_args.args
        self.assertEqual(subscription, self.sub)
        self.assertEqual(payload, {'title': 'New shifts open', 'body': 'Sign up for the bake sale',
                                   'url': 'https://lists.test/link/1/2/3',
                                   'view': 'https://lists.test/campaign/2/3/px.png'})

    def test_recipients_without_notifications_are_skipped(self):
        response, send = self.deliver({})
        self.assertEqual(response.json(), {'sent': 0})

    def test_campaign_category_can_be_switched_off(self):
        SiteSettings.objects.filter(pk=1).update(push_categories={'campaign': False})
        cache.clear()
        response, send = self.deliver({push.ATTRIB: [self.sub.endpoint]})
        send.assert_not_called()


class ListmonkSetupTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_creates_template_and_registers_messenger_keeping_secrets(self):
        settings_data = {'app.root_url': 'https://lists.test', 'messengers': [
            {'uuid': 'm1', 'name': 'sms', 'enabled': True, 'root_url': 'https://sms.test', 'username': 'x', 'password': '•••'}],
            'smtp': [{'uuid': 's1', 'password': '••••••'}]}
        calls = []

        def api(method, path, payload=None, **kw):
            calls.append((method, path, payload))
            if path == '/settings' and method == 'GET':
                return json.loads(json.dumps(settings_data))
            if path == '/templates' and method == 'POST':
                return {'id': 42}
            return True

        with mock.patch('base.listmonk.api', side_effect=api), \
                mock.patch('base.listmonk.get_templates', return_value={'campaign': []}):
            push.setup_listmonk('https://th.test/push/listmonk/', 'https://th.test/')
        template = next(p for m, path, p in calls if path == '/templates')
        self.assertIn('{{ template "content" . }}', template['body'])
        self.assertIn('TrackLink "https://th.test/"', template['body'])
        self.assertEqual(SiteSettings.objects.get(pk=1).listmonk_push_template_id, 42)
        saved = next(p for m, path, p in calls if m == 'PUT' and path == '/settings')
        self.assertEqual(saved['smtp'][0]['password'], '')  # unchanged in listmonk
        names = {m['name']: m for m in saved['messengers']}
        self.assertEqual(names['sms']['password'], '')
        self.assertEqual(names['townhall']['root_url'], 'https://th.test/push/listmonk/')
        self.assertEqual(names['townhall']['password'], push.messenger_password())


class ConsoleNotificationsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user('staff', 's@example.com', 'pw', is_staff=True)
        self.client.force_login(self.staff)

    def test_page_and_saving_categories(self):
        url = reverse('console_communication_notifications')
        response = self.client.get(url)
        self.assertContains(response, 'name="push_shift_reminder"')
        self.assertContains(response, 'Connect listmonk in Organization')
        self.client.post(url, {'push_shift_reminder': 'on'})
        self.assertTrue(push.is_on('shift_reminder'))
        self.assertFalse(push.is_on('event_published'))
