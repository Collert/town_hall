import base64
import hashlib
import json
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone, translation

from base.models import AdminFeedback, Endorsement, Notification, SiteSettings
from console.tests import FakeBellTower
from education.models import Quiz, Skill, TrainingLesson, TrainingModule
from events.models import Event, EventFeedback, EventRoleSlot, EventSlotInvite, EventTaskList
from jobs.models import Role, RoleTrainingRequirement

from .models import AccessToken, OAuthClient, digest
from .tools import REGISTRY

REDIRECT = 'https://claude.ai/api/mcp/auth_callback'


def make_event(title, start, hours=4, **extra):
    # Coordinates keep Event.save() from geocoding over the network.
    return Event.objects.create(title=title, description='A day out.', location='Town Hall', latitude=49.28,
                                longitude=-123.12, start_date=start, end_date=start + timedelta(hours=hours), **extra)


def pkce():
    verifier = 'v' * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


class McpFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Earlier tests can leave another language active; translated fields must land in English.
        with translation.override('en'):
            cls.make_data()

    @classmethod
    def make_data(cls):
        cls.staff = User.objects.create_user('olena', 'olena@example.org', 'pw-123456', first_name='Olena', is_staff=True)
        cls.vol = User.objects.create_user('val', 'val@example.org', 'pw-123456', first_name='Val')
        cls.module = TrainingModule.objects.create(title='Kitchen safety', description='Our kitchen.', icon='restaurant', published=True)
        cls.lesson = TrainingLesson.objects.create(training_module=cls.module, title='Knives',
                                                   content='Knives go in the red drawer after washing.', order=1)
        cls.server = Role.objects.create(name='Server', description='Serves food.', icon='restaurant')
        cls.permanent = Role.objects.create(name='Sexton', icon='church', permanent=True)
        now = timezone.now()
        cls.past = make_event('Harvest Supper 2025', now - timedelta(days=300), attendees=200)
        EventRoleSlot.objects.create(event=cls.past, role=cls.server, start_time=cls.past.start_date,
                                     end_time=cls.past.end_date, required_qty=10).signups.add(cls.vol)
        EventFeedback.objects.create(event=cls.past, user=cls.vol, rating=4, suggestions='More dessert tables.')
        skill = Skill.objects.create(name='Cooking')
        cls.vol.profile.skills.add(skill)
        Endorsement.give(cls.staff, cls.vol, [skill], text='Great with guests', event=cls.past)
        AdminFeedback.objects.create(volunteer=cls.vol, author=cls.staff, rating=5, note='Reliable')

    def setUp(self):
        cache.clear()

    def token_for(self, user):
        client = OAuthClient.objects.create(client_id='test-client', name='Claude', redirect_uris=[REDIRECT])
        AccessToken.objects.create(client=client, user=user, access_hash=digest('access-1'), refresh_hash=digest('refresh-1'),
                                   expires_at=timezone.now() + timedelta(hours=1), refresh_expires_at=timezone.now() + timedelta(days=60))
        return 'access-1'

    def rpc(self, method, params=None, token='access-1', msg_id=1):
        body = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
        if msg_id is not None:
            body['id'] = msg_id
        return self.client.post('/mcp', json.dumps(body), content_type='application/json',
                                HTTP_AUTHORIZATION=f'Bearer {token}')

    def call(self, tool_name, /, **arguments):
        response = self.rpc('tools/call', {'name': tool_name, 'arguments': arguments})
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()['result']
        text = result['content'][0]['text']
        try:
            data = json.loads(text)
        except ValueError:
            data = text
        return result['isError'], data


class OAuthTests(McpFixture):
    def test_unauthenticated_request_points_to_metadata(self):
        response = self.client.post('/mcp', '{}', content_type='application/json')
        self.assertEqual(response.status_code, 401)
        self.assertIn('resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"', response['WWW-Authenticate'])
        resource = self.client.get('/.well-known/oauth-protected-resource/mcp').json()
        self.assertEqual(resource['resource'], 'http://testserver/mcp')
        self.assertEqual(resource['authorization_servers'], ['http://testserver'])
        server = self.client.get('/.well-known/oauth-authorization-server').json()
        self.assertEqual(server['issuer'], 'http://testserver')
        self.assertEqual(server['code_challenge_methods_supported'], ['S256'])
        self.assertEqual(server['registration_endpoint'], 'http://testserver/oauth/register')

    def test_discovery_fallback_paths(self):
        for path in ('/.well-known/openid-configuration', '/.well-known/openid-configuration/mcp',
                     '/mcp/.well-known/openid-configuration', '/.well-known/oauth-authorization-server/mcp'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.json()['token_endpoint'], 'http://testserver/oauth/token')

    def test_register_rejects_unsafe_redirects(self):
        for uris in ([], ['http://evil.example/cb'], ['javascript:alert(1)']):
            response = self.client.post('/oauth/register', json.dumps({'redirect_uris': uris}), content_type='application/json')
            self.assertEqual(response.status_code, 400)
        ok = self.client.post('/oauth/register', json.dumps({'redirect_uris': ['http://localhost:6274/cb']}),
                              content_type='application/json')
        self.assertEqual(ok.status_code, 201)

    def test_full_connect_flow(self):
        reg = self.client.post('/oauth/register', json.dumps({'client_name': 'Claude', 'redirect_uris': [REDIRECT]}),
                               content_type='application/json').json()
        client_id = reg['client_id']
        self.assertNotIn('client_secret', reg)
        verifier, challenge = pkce()
        params = {'response_type': 'code', 'client_id': client_id, 'redirect_uri': REDIRECT, 'state': 'xyz',
                  'code_challenge': challenge, 'code_challenge_method': 'S256', 'scope': 'townhall'}

        # Signed out: sent to the login page, then back here.
        response = self.client.get('/oauth/authorize', params)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])
        self.assertIn('next=/oauth/authorize', response['Location'])

        self.client.force_login(self.staff)
        page = self.client.get('/oauth/authorize', params)
        self.assertContains(page, 'Connect Claude to')
        self.assertContains(page, "Delete anything")

        denied = self.client.post('/oauth/authorize', {**params, 'decision': 'deny'})
        self.assertEqual(parse_qs(urlsplit(denied['Location']).query)['error'], ['access_denied'])

        allowed = self.client.post('/oauth/authorize', {**params, 'decision': 'allow'})
        location = urlsplit(allowed['Location'])
        self.assertEqual(f'{location.scheme}://{location.netloc}{location.path}', REDIRECT)
        query = parse_qs(location.query)
        self.assertEqual(query['state'], ['xyz'])
        code = query['code'][0]

        self.client.logout()
        bad = self.client.post('/oauth/token', {'grant_type': 'authorization_code', 'code': code, 'client_id': client_id,
                                                'redirect_uri': REDIRECT, 'code_verifier': 'wrong' * 10})
        self.assertEqual(bad.json()['error'], 'invalid_grant')
        # A failed attempt burns the code too.
        again = self.client.post('/oauth/token', {'grant_type': 'authorization_code', 'code': code, 'client_id': client_id,
                                                  'redirect_uri': REDIRECT, 'code_verifier': verifier})
        self.assertEqual(again.json()['error'], 'invalid_grant')

        self.client.force_login(self.staff)
        code = parse_qs(urlsplit(self.client.post('/oauth/authorize', {**params, 'decision': 'allow'})['Location']).query)['code'][0]
        self.client.logout()
        tokens = self.client.post('/oauth/token', {'grant_type': 'authorization_code', 'code': code, 'client_id': client_id,
                                                   'redirect_uri': REDIRECT, 'code_verifier': verifier}).json()
        self.assertEqual(tokens['token_type'], 'Bearer')
        self.assertEqual(self.rpc('ping', token=tokens['access_token']).json()['result'], {})

        refreshed = self.client.post('/oauth/token', {'grant_type': 'refresh_token', 'client_id': client_id,
                                                      'refresh_token': tokens['refresh_token']}).json()
        self.assertNotEqual(refreshed['access_token'], tokens['access_token'])
        self.assertEqual(self.rpc('ping', token=tokens['access_token']).status_code, 401)  # rotated away
        reused = self.client.post('/oauth/token', {'grant_type': 'refresh_token', 'client_id': client_id,
                                                   'refresh_token': tokens['refresh_token']})
        self.assertEqual(reused.json()['error'], 'invalid_grant')

        self.client.post('/oauth/revoke', {'token': refreshed['access_token']})
        self.assertEqual(self.rpc('ping', token=refreshed['access_token']).status_code, 401)

    def test_confidential_client_needs_its_secret(self):
        reg = self.client.post('/oauth/register', json.dumps({'redirect_uris': [REDIRECT], 'token_endpoint_auth_method': 'client_secret_post'}),
                               content_type='application/json').json()
        response = self.client.post('/oauth/token', {'grant_type': 'refresh_token', 'client_id': reg['client_id'],
                                                     'client_secret': 'nope', 'refresh_token': 'x'})
        self.assertEqual(response.status_code, 401)
        basic = base64.b64encode(f'{reg["client_id"]}:{reg["client_secret"]}'.encode()).decode()
        response = self.client.post('/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': 'x'},
                                    HTTP_AUTHORIZATION=f'Basic {basic}')
        self.assertEqual(response.json()['error'], 'invalid_grant')  # authenticated; just a bad refresh token

    def test_only_staff_can_connect_or_stay_connected(self):
        client = OAuthClient.objects.create(client_id='c1', name='Claude', redirect_uris=[REDIRECT])
        _verifier, challenge = pkce()
        self.client.force_login(self.vol)
        response = self.client.get('/oauth/authorize', {'response_type': 'code', 'client_id': client.client_id, 'redirect_uri': REDIRECT,
                                                        'code_challenge': challenge, 'code_challenge_method': 'S256'})
        self.assertContains(response, 'Only staff accounts', status_code=403)

        # A redirect address the client didn't register is never followed.
        response = self.client.get('/oauth/authorize', {'response_type': 'code', 'client_id': client.client_id,
                                                        'redirect_uri': 'https://evil.example/cb'})
        self.assertEqual(response.status_code, 400)

        token = self.token_for(self.staff)
        self.assertEqual(self.rpc('ping', token=token).status_code, 200)
        User.objects.filter(pk=self.staff.pk).update(is_staff=False)
        self.assertEqual(self.rpc('ping', token=token).status_code, 401)

    def test_console_lists_and_disconnects_apps(self):
        self.token_for(self.staff)
        self.client.force_login(self.staff)
        page = self.client.get(reverse('console_settings_backend'))
        self.assertContains(page, 'http://testserver/mcp')
        self.assertContains(page, 'Connected apps')
        token = AccessToken.objects.get()
        self.client.post(reverse('console_mcp_disconnect', args=[token.pk]))
        self.assertTrue(AccessToken.objects.get().revoked)
        self.assertEqual(self.rpc('ping').status_code, 401)


class ProtocolTests(McpFixture):
    def setUp(self):
        super().setUp()
        self.token_for(self.staff)

    def test_initialize_and_list_tools(self):
        result = self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                         'clientInfo': {'name': 'claude-ai', 'version': '1'}}).json()['result']
        self.assertEqual(result['protocolVersion'], '2025-06-18')
        self.assertIn('tools', result['capabilities'])
        self.assertIn('Olena', result['instructions'])
        self.assertEqual(self.rpc('initialize', {'protocolVersion': '1999-01-01'}).json()['result']['protocolVersion'], '2025-11-25')

        self.assertEqual(self.rpc('notifications/initialized', msg_id=None).status_code, 202)
        tools = self.rpc('tools/list').json()['result']['tools']
        names = {t['name'] for t in tools}
        self.assertEqual(names, set(REGISTRY))
        self.assertFalse([n for n in names if 'delete' in n or 'remove' in n])
        by_name = {t['name']: t for t in tools}
        self.assertTrue(by_name['get_event']['annotations']['readOnlyHint'])
        self.assertFalse(by_name['create_event']['annotations']['readOnlyHint'])
        self.assertTrue(by_name['invite_volunteers']['annotations']['openWorldHint'])
        for tool in tools:
            self.assertFalse(tool['annotations']['destructiveHint'])
            self.assertEqual(tool['inputSchema']['type'], 'object')

    def test_errors(self):
        self.assertEqual(self.rpc('resources/list').json()['error']['code'], -32601)
        self.assertEqual(self.rpc('tools/call', {'name': 'delete_event'}).json()['error']['code'], -32602)
        bad = self.client.post('/mcp', 'not json', content_type='application/json', HTTP_AUTHORIZATION='Bearer access-1')
        self.assertEqual(bad.json()['error']['code'], -32700)
        self.assertEqual(self.client.get('/mcp', HTTP_AUTHORIZATION='Bearer access-1').status_code, 405)
        is_error, text = self.call('get_event', event_id=99999)
        self.assertTrue(is_error)
        self.assertIn('No event with id 99999', text)
        is_error, text = self.call('get_event', event_id='seven')
        self.assertTrue(is_error)
        self.assertIn('must be a whole number', text)
        is_error, text = self.call('list_events', colour='red')
        self.assertIn('unknown field "colour"', text)


class EventToolTests(McpFixture):
    def setUp(self):
        super().setUp()
        self.token_for(self.staff)
        self.start = (timezone.localtime() + timedelta(days=40)).replace(hour=17, minute=0, second=0, microsecond=0)

    def fmt(self, dt):
        return timezone.localtime(dt).strftime('%Y-%m-%dT%H:%M')

    def test_create_edit_and_read_an_event(self):
        is_error, created = self.call('create_event', title='Harvest Supper 2026', start=self.fmt(self.start),
                                      end=self.fmt(self.start + timedelta(hours=4)), expected_attendees=250)
        self.assertFalse(is_error, created)
        event = Event.objects.get(pk=created['created']['event_id'])
        self.assertEqual(event.title, 'Harvest Supper 2026')
        self.assertFalse(event.published)
        self.assertEqual(list(event.coordinators.all()), [self.staff])
        self.assertIn('description (none yet)', created['missing'])

        is_error, _ = self.call('update_event', event_id=event.pk, description='Our yearly supper.', published=True,
                                end=self.fmt(self.start - timedelta(hours=1)))
        self.assertTrue(is_error)  # ending before it starts; nothing saved
        self.assertFalse(Event.objects.get(pk=event.pk).published)
        is_error, updated = self.call('update_event', event_id=event.pk, description='Our yearly supper.', published=True)
        self.assertEqual(updated['updated'], ['description', 'published'])

        _, details = self.call('get_event', event_id=event.pk)
        self.assertEqual(details['description'], 'Our yearly supper.')
        self.assertIn('volunteer roles and shifts', details['missing'])
        self.assertTrue(details['console_url'].endswith(f'/console/events/{event.pk}/'))

        _, listed = self.call('list_events', when='upcoming', query='harvest')
        self.assertEqual([e['event_id'] for e in listed], [event.pk])

    def test_staffing_reference_and_role_slots(self):
        _, roles = self.call('list_roles')
        server = next(r for r in roles if r['name'] == 'Server')
        self.assertEqual(server['history']['average_needed_per_100_attendees'], 5.0)
        self.assertNotIn('Sexton', [r['name'] for r in roles])  # permanent positions aren't event roles

        _, similar = self.call('find_similar_events', query='harvest supper')
        self.assertEqual(similar[0]['roles']['Server']['required_per_100_attendees'], 5.0)
        self.assertEqual(similar[0]['feedback']['suggestions'], ['More dessert tables.'])

        event = make_event('Supper', self.start)
        is_error, added = self.call('add_role_slots', event_id=event.pk, role_id=self.server.pk, slots=[
            {'start': self.fmt(self.start), 'end': self.fmt(self.start + timedelta(hours=2)), 'required': 6},
            {'start': self.fmt(self.start + timedelta(hours=2)), 'end': self.fmt(self.start + timedelta(hours=4)), 'required': 6, 'extra': 2},
        ])
        self.assertFalse(is_error, added)
        self.assertEqual(event.role_slots.count(), 2)
        is_error, _ = self.call('add_role_slots', event_id=event.pk, role_id=self.permanent.pk,
                                slots=[{'start': self.fmt(self.start), 'end': self.fmt(self.start + timedelta(hours=1)), 'required': 1}])
        self.assertTrue(is_error)

        slot_id = added['added'][0]['slot_id']
        _, updated = self.call('update_role_slot', slot_id=slot_id, required=8)
        self.assertEqual(updated['updated']['required'], 8)

        is_error, text = self.call('create_area', event_id=event.pk, name='Kitchen')
        self.assertTrue(is_error)
        self.call('enable_chain_of_command', event_id=event.pk)
        _, area = self.call('create_area', event_id=event.pk, name='Kitchen', icon='restaurant')
        _, moved = self.call('update_role_slot', slot_id=slot_id, area_id=area['area_id'])
        self.assertEqual(moved['updated']['area'], 'Kitchen')

    def test_invitations(self):
        event = make_event('Supper', self.start)
        slot = EventRoleSlot.objects.create(event=event, role=self.server, start_time=self.start,
                                            end_time=self.start + timedelta(hours=2), required_qty=2)
        _, found = self.call('find_volunteers_for_slot', slot_id=slot.pk)
        self.assertIn(self.vol.pk, [p['user_id'] for p in found])
        _, sent = self.call('invite_volunteers', slot_id=slot.pk, user_ids=[self.vol.pk])
        self.assertEqual(sent['count'], 1)
        invite = EventSlotInvite.objects.get(event_role_slot=slot, user=self.vol)
        link = 'http://testserver' + reverse('respond_to_invite', args=[invite.token])
        self.assertTrue(Notification.objects.filter(user=self.vol, link=link).exists())
        self.assertFalse(slot.signups.exists())  # invited, not signed up


class RoleAndTrainingToolTests(McpFixture):
    def setUp(self):
        super().setUp()
        self.token_for(self.staff)

    def test_role_templates_with_training(self):
        is_error, created = self.call('create_role', name='Dishwasher', description='Keeps the kitchen moving.', icon='wash',
                                      training=[{'module_id': self.module.pk}])
        self.assertFalse(is_error, created)
        role = Role.objects.get(name='Dishwasher')
        self.assertTrue(RoleTrainingRequirement.objects.get(role=role).mandatory)
        is_error, _ = self.call('create_role', name='server', description='x', icon='x')
        self.assertTrue(is_error)  # duplicate name

        _, updated = self.call('update_role', role_id=role.pk, training=[{'module_id': self.module.pk, 'mandatory': False}])
        self.assertEqual(updated['updated']['training'][0]['mandatory'], False)
        is_error, _ = self.call('update_role', role_id=role.pk, training=[{'module_id': 9999}])
        self.assertTrue(is_error)
        is_error, _ = self.call('update_role', role_id=self.permanent.pk, description='x')
        self.assertTrue(is_error)

    def test_quizzes_must_come_from_lessons(self):
        _, module = self.call('get_training_module', module_id=self.module.pk)
        self.assertIn('red drawer', module['lessons'][0]['content'])

        other = TrainingModule.objects.create(title='Other', description='x', icon='x')
        stray = TrainingLesson.objects.create(training_module=other, title='Elsewhere', content='Nope.')
        is_error, text = self.call('create_quiz', module_id=self.module.pk, title='Knife check', questions=[
            {'question': 'Where do knives go?', 'lesson_id': stray.pk, 'options': ['Red drawer', 'Sink'], 'correct_option': 'A'},
        ])
        self.assertTrue(is_error)
        self.assertIn('not part of this module', text)
        self.assertFalse(Quiz.objects.filter(training_module=self.module).exists())

        is_error, text = self.call('create_quiz', module_id=self.module.pk, title='Knife check', questions=[
            {'question': 'Where do knives go?', 'lesson_id': self.lesson.pk, 'options': ['Sink'], 'correct_option': 'A'},
        ])
        self.assertTrue(is_error)

        is_error, quiz = self.call('create_quiz', module_id=self.module.pk, title='Knife check', pass_percentage=80, questions=[
            {'question': 'Where do knives go after washing?', 'lesson_id': self.lesson.pk,
             'options': ['The sink', 'The red drawer', 'The counter'], 'correct_option': 'B'},
            {'question': 'Knives are left on the counter.', 'lesson_id': self.lesson.pk, 'true_false_answer': False},
        ])
        self.assertFalse(is_error, quiz)
        saved = Quiz.objects.get(pk=quiz['quiz_id'])
        self.assertEqual(saved.percentage_to_pass, 80)
        questions = list(saved.questions.order_by('pk'))
        self.assertEqual((questions[0].option_b, questions[0].correct_option), ('The red drawer', 'B'))
        self.assertTrue(questions[1].is_true_false)
        self.assertEqual(questions[1].correct_option, 'B')

        _, more = self.call('add_quiz_questions', quiz_id=saved.pk, questions=[
            {'question': 'Knives go in the red drawer.', 'lesson_id': self.lesson.pk, 'true_false_answer': True},
        ])
        self.assertEqual(more['total_questions'], 3)

    def test_people_are_read_only(self):
        _, found = self.call('search_people', query='cooking')
        self.assertEqual([p['user_id'] for p in found], [self.vol.pk])
        _, profile = self.call('get_person', user_id=self.vol.pk)
        self.assertEqual(profile['endorsements_received'][0]['text'], 'Great with guests')
        self.assertEqual(profile['event_feedback_given'][0]['suggestions'], 'More dessert tables.')
        self.assertEqual(profile['staff_ratings'][0]['note'], 'Reliable')
        _, feedback = self.call('get_event_feedback', event_id=self.past.pk)
        self.assertEqual(feedback['average_rating'], 4)
        self.assertFalse([t for t in REGISTRY if 'person' in t and not REGISTRY[t].read_only])


class TaskToolTests(McpFixture):
    def setUp(self):
        super().setUp()
        self.token_for(self.staff)
        self.tower = FakeBellTower()
        patcher = patch('base.belltower._http', self.tower)
        patcher.start()
        self.addCleanup(patcher.stop)
        site = SiteSettings.get_settings()
        site.belltower_url, site.belltower_api_key, site.belltower_username = FakeBellTower.BASE, 'bt_secret', 'townhall'
        site.save()
        start = timezone.now() + timedelta(days=20)
        self.event = make_event('Supper', start)
        EventRoleSlot.objects.create(event=self.event, role=self.server, start_time=start, end_time=start + timedelta(hours=2), required_qty=4)

    def tearDown(self):
        cache.clear()

    def test_not_connected(self):
        site = SiteSettings.get_settings()
        site.belltower_api_key = ''
        site.save()
        is_error, text = self.call('add_tasks', event_id=self.event.pk, tasks=[{'title': 'Book hall'}])
        self.assertTrue(is_error)
        self.assertIn('Bell Tower is not connected', text)

    def test_planning_and_station_tasks(self):
        due = timezone.localtime(timezone.now() + timedelta(days=5)).strftime('%Y-%m-%dT09:00')
        is_error, added = self.call('add_tasks', event_id=self.event.pk, tasks=[
            {'title': 'Book the hall', 'due': due, 'subtasks': ['Call the office', 'Pay deposit']},
            {'title': 'Order turkeys'},
        ])
        self.assertFalse(is_error, added)
        planning = EventTaskList.objects.get(event=self.event, kind='planning')
        self.assertEqual(added['list_id'], planning.pk)

        _, station = self.call('create_task_list', event_id=self.event.pk, name='Registration desk')
        _, desk = self.call('add_tasks', event_id=self.event.pk, list_id=station['list_id'],
                            tasks=[{'title': 'Hand out name tags'}])
        self.assertEqual(desk['list'], 'Registration desk')

        _, lists = self.call('get_event_tasks', event_id=self.event.pk)
        by_name = {l['name']: l for l in lists}
        self.assertEqual([t['title'] for t in by_name['Planning']['tasks']], ['Book the hall', 'Order turkeys'])
        self.assertEqual([t['title'] for t in by_name['Planning']['tasks'][0]['subtasks']], ['Call the office', 'Pay deposit'])
        self.assertIn('Server tasks', by_name)  # role lists are made as on the Tasks tab
        self.assertEqual(by_name['Registration desk']['kind'], 'event day')

        task_id = added['added'][1]['task_id']
        _, done = self.call('update_task', event_id=self.event.pk, task_id=task_id, completed=True)
        self.assertTrue(self.tower.tasks[task_id]['completed'])

        other = make_event('Other', timezone.now() + timedelta(days=9))
        is_error, text = self.call('update_task', event_id=other.pk, task_id=task_id, title='Hijack')
        self.assertTrue(is_error)
        self.assertIn('not on any of this event', text)
