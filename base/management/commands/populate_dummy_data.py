"""Seed a believable demo: the volunteer programme of a Ukrainian Catholic parish in New Westminster, BC.

Run it on an empty, migrated database (delete db.sqlite3, migrate, then run this). Every date is
relative to today, so past, live and upcoming events keep their meaning whenever it runs.

Covers every screen: past events with shifts, feedback, endorsements and reports; an event that is
live right now (check-in monitor); a multi-day event in progress; upcoming events that are
understaffed, full, invite-only or unpublished; permanent roles with kiosk shifts; training in
progress, finished and expired; certificates verified, pending, rejected and expired; points
adjustments, staff notes, notifications, venue notices and API keys.

With --belltower URL --belltower-token TOKEN (a Bell Tower staff account's API token) it also
connects Bell Tower, links every demo person to a Bell Tower account, and gives each event that
hasn't ended a planning list, a list per role with its volunteers, and realistic tasks.
"""
import io
import os
import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone, translation

from api.models import APIKey, ShiftHeartbeat
from base import belltower, points
from base.models import (AdminFeedback, Endorsement, HeroSection, Level, Notification, OperatingHour,
                         PointsEntry, PointsRules, SiteSettings, Venue, VenueFeature, VenueNote)
from education.models import (ExternalCertificate, Quiz, QuizQuestion, Skill, TrainingLesson, TrainingModule,
                              TrainingModuleCompletion, TrainingTopic, UserCertification, UserCertificationFile)
from events import belltower_sync
from events.models import Event, EventCategory, EventFeedback, EventRoleSlot, EventSlotInvite, EventTaskList, SlotSignup
from jobs.models import Role, RoleTrainingRequirement, Shift

User = get_user_model()

# ---------------------------------------------------------------- content

SKILLS = ['First Aid', 'Food Handling', 'Cooking', 'Ukrainian Language', 'Interpreting', 'Childcare',
          'Event Setup', 'Cash Handling', 'Photography', 'Driving', 'Customer Service', 'Settlement Support']

CATEGORIES = ['Cultural Heritage', 'Food Security', 'Newcomer Support', 'Youth & Family', 'Fundraiser',
              'Faith & Community', 'Remembrance']

LEVELS = [
    (1, 'Newcomer', 0, 'Welcome kit, Parish volunteer lanyard'),
    (2, 'Helper', 100, 'Volunteer t-shirt, Invitation to the annual appreciation dinner'),
    (3, 'Contributor', 300, 'Priority sign-up for festival roles, Name on the volunteer wall'),
    (4, 'Pillar', 750, 'Reserved seat at Sviat Vechir, Mentor new volunteers'),
    (5, 'Champion', 1500, 'Recognition at the Feast of the Holy Eucharist, Reference letter from the parish'),
]

VENUES = {
    'cathedral': dict(
        name='Holy Eucharist Cathedral', address='501 Fourth Avenue, New Westminster, BC',
        latitude=49.2117, longitude=-122.9136, capacity=350, phone_number='604-555-0140',
        description='The heart of the parish. Divine Liturgy on Sundays at 9:30 and 11:30, with the iconography '
                    'tour after the late Liturgy. Volunteers enter through the side door on Fourth Avenue.',
        features=['Wheelchair accessible', 'Heating', 'Sound system', 'On-site restrooms', 'Free parking',
                  'Public transit nearby'],
        hours={6: ('08:00', '14:00'), 1: ('09:00', '17:00'), 2: ('09:00', '17:00'), 3: ('09:00', '17:00'),
               4: ('09:00', '17:00'), 5: ('09:00', '17:00')},
    ),
    'hall': dict(
        name='Cathedral Parish Hall', address='501 Fourth Avenue (lower level), New Westminster, BC',
        latitude=49.2115, longitude=-122.9132, capacity=220, phone_number='604-555-0141',
        description='Our gathering space under the cathedral: a commercial kitchen, a small stage and seating '
                    'for 220. Home of the perogy bees, Sviat Vechir supper and the weekly food pantry.',
        features=['Full kitchen', 'Stage', 'Tables & chairs', 'Storage room', 'On-site restrooms',
                  'Accessible restrooms', 'Elevator', 'Wi-Fi', 'Projector & screen', 'Sound system',
                  'Loading dock', 'Free parking', 'Heating'],
        hours={0: ('09:00', '21:00'), 1: ('09:00', '21:00'), 2: ('09:00', '21:00'), 3: ('09:00', '21:00'),
               4: ('09:00', '21:00'), 5: ('08:00', '18:00'), 6: ('08:00', '16:00')},
        staff_after_hours=True,
    ),
    'centre': dict(
        name='Royal City Newcomer Welcome Centre', address='625 Agnes Street, New Westminster, BC',
        latitude=49.2069, longitude=-122.9118, capacity=60, phone_number='604-555-0187',
        description='Drop-in help for newcomers from Ukraine: settlement questions, forms, English practice and '
                    'a quiet place to talk. Run by the parish with volunteer interpreters and navigators.',
        features=['Wheelchair accessible', 'Elevator', 'Accessible restrooms', 'Kids area', 'Quiet room',
                  'Kitchenette', 'Wi-Fi', 'Projector & screen', 'Hearing loop', 'Public transit nearby'],
        hours={0: ('09:30', '16:30'), 1: ('09:30', '16:30'), 2: ('09:30', '16:30'), 3: ('09:30', '16:30'),
               4: ('09:30', '16:30'), 5: ('10:00', '14:00')},
        staff_after_hours=True,
    ),
    'park': dict(
        name="Queen's Park Bandshell", address="Queen's Park, First Street & Third Avenue, New Westminster, BC",
        latitude=49.2173, longitude=-122.9087, capacity=2000, phone_number='',
        description='Outdoor stage and lawn used for the Independence Day festival and summer events. '
                    'Volunteer check-in is at the white tent beside the bandshell.',
        features=['Outdoor space', 'Stage', 'Sound system', 'On-site restrooms', 'Wheelchair accessible',
                  'First aid station', 'Kids area', 'Free parking', 'Bike racks'],
        hours={},
    ),
}

ONE_OFF = {
    'vag': ('Vancouver Art Gallery North Plaza, 750 Hornby Street, Vancouver, BC', 49.2829, -123.1204),
    'anvil': ('Anvil Centre, 777 Columbia Street, New Westminster, BC', 49.2008, -122.9116),
    'quay': ('Inn at the Quay Ballroom, 900 Quayside Drive, New Westminster, BC', 49.2005, -122.9099),
}

# key: (title, topic, icon, skills, expires_after_days, published, lessons [(title, markdown)], quiz questions)
# quiz question: (text, a, b, c, d, correct) or (text, True/False) for true/false
MODULES = {
    'orient': ('Volunteer Orientation', 'Onboarding', 'waving_hand', ['Customer Service'], None, True,
        'Start here. How our parish volunteer programme works, what we expect of each other and how to sign up '
        'for your first shift.',
        [
            ('Welcome to the parish volunteer team', """
Thank you for offering your time. Our volunteers cook for Sviat Vechir, welcome newcomers from Ukraine,
set up the Independence Day festival and keep the food pantry running every week. None of it happens
without you.

## What we ask of every volunteer

- **Show up when you sign up.** If something changes, cancel from *My events* at least a day ahead so
  a coordinator can find cover.
- **Treat everyone with dignity.** Many of the families we serve have lost their homes. Be patient,
  speak plainly and never ask people to explain their situation.
- **Respect privacy.** What you hear at the Welcome Centre stays at the Welcome Centre.
- **Ask for help.** Every event has a coordinator listed on its page. They would rather answer a
  question than fix a mistake.

## Who we serve

Parishioners, families who arrived under the CUAET program, seniors in the Royal City and anyone who
walks through our doors. You don't need to speak Ukrainian to volunteer here.
"""),
            ('How shifts, sign-ups and the kiosk work', """
Every event is split into **role slots**: a role (Kitchen Volunteer, Greeter, Setup Crew...), a time
window and the number of people needed. You sign up for a slot, not for the whole event.

1. Open **Opportunities** and pick an event.
2. Choose a role. If it says *training required*, finish the listed modules first.
3. On the day, check in at the **kiosk** with your 6-digit volunteer code. It's on your profile page.
4. Check out at the kiosk when you leave. Your hours and impact points are recorded automatically.

### Walk-ins welcome

If you're at an event and see a slot that's short-staffed, you can pick it at the kiosk without
signing up beforehand. Covering a gap like that earns a bonus.

### Impact points and levels

Points come from hours worked, how demanding the role is, how many people the event served and
the extra effort of early, late or last-minute shifts. Training and endorsements from teammates
add a little more. Your level unlocks small perks, like priority sign-up for festival roles.
"""),
            ('Safety, accessibility and who to call', """
- In an emergency call **911** first, then tell the event coordinator.
- First aid kits are in the parish hall kitchen (by the sink) and at the Welcome Centre front desk.
  At outdoor events look for the first aid tent.
- Lift with your legs. Tables in the hall are a two-person carry, always.
- Keep fire exits and the ramp at the north entrance clear.
- Report every injury, however small, on an incident form. The coordinator has them.

**Accessibility:** both the hall and the Welcome Centre have elevators and accessible washrooms. If you
need an accommodation to volunteer, tell us. We will make it work.

**Contacts:** Olena Kovalchuk (volunteer programme) and Mykola Petrenko (events). Find both under
*Contact* in the footer.
"""),
        ],
        [
            ('You can no longer make a shift you signed up for. What should you do?',
             'Just not show up', 'Cancel from My events at least a day ahead', 'Send a friend in your place without telling anyone', None, 'B'),
            ('Where do you check in when you arrive at an event?', 'At the kiosk, with your 6-digit code', 'By emailing the office', 'You don\'t need to', None, 'A'),
            ('It is fine to ask newcomers why they left Ukraine so you can help them better.', False),
            ('Tables in the parish hall should always be carried by two people.', True),
        ]),
    'food': ('Food Safety Basics', 'Food Service', 'restaurant', ['Food Handling', 'Cooking'], 730, True,
        'Required for anyone working in the parish hall kitchen. Covers temperature control, cross-contamination, '
        'allergens and how a perogy bee runs. Prepares you for FOODSAFE Level 1.',
        [
            ('Why food safety matters', """
We serve food to hundreds of people a year, including seniors, small children and people with weakened
immune systems. A single mistake in the kitchen can make dozens of them sick.

Most foodborne illness comes from three things: **food held at the wrong temperature**, **dirty hands
and surfaces**, and **raw food touching ready-to-eat food**. Everything in this module is about those three.

Before every kitchen shift:

- Wash your hands for 20 seconds with soap and warm water.
- Tie back long hair and wear the hairnets by the kitchen door.
- Remove rings and watches.
- Tell the kitchen lead if you have had vomiting or diarrhea in the last 48 hours. You'll be thanked and
  sent home, no questions asked.
"""),
            ('Temperature control and the danger zone', """
Bacteria grow fastest between **4 °C and 60 °C**. That range is the *danger zone*.

| Food | Safe temperature |
| --- | --- |
| Cold holding (salads, sour cream) | 4 °C or colder |
| Hot holding (holubtsi, borshch) | 60 °C or hotter |
| Reheating leftovers | 74 °C within two hours |
| Cooked chicken | 74 °C internal |

Use the probe thermometers in the top drawer and write readings on the clipboard every hour.
Cooked food must cool from 60 °C to 20 °C within two hours, then to 4 °C within four more, so spread
large batches into shallow pans before they go in the walk-in.
"""),
            ('Cleaning, sanitizing and allergens', """
**Cleaning** removes dirt; **sanitizing** kills what is left. Do both, in that order.

- Sanitizer buckets are mixed at 200 ppm quat. Test with the strips and remix every two hours.
- Green cutting boards for vegetables, red for raw meat. Never swap them.

### Allergens

Our most common allergens are **wheat** (perogy dough), **milk** (cheese and sour cream fillings) and
**eggs**. Every tray that leaves the kitchen gets a label listing its filling. If a guest asks
whether something is safe for them and you are not sure, say so and fetch the kitchen lead. Never guess.
"""),
            ('How a perogy bee runs', """
A perogy bee turns 40 volunteers and 300 kg of potatoes into about 6,000 varenyky in a morning.

1. **Dough station** mixes and rests the dough. Only trained bakers work the mixer.
2. **Filling station** scoops potato-cheddar, sauerkraut and cottage cheese fillings into labelled bins.
3. **Pinching tables** roll, cut, fill and pinch. Count by the dozen onto floured trays.
4. **Freezer runners** carry full trays to the walk-in freezer and log the count.
5. **Packing** bags frozen perogies by the dozen for Saturday sales.

Wash your hands every time you change stations, and always before you go back to pinching.
"""),
        ],
        [
            ('What is the temperature danger zone?', 'Below 0 °C', 'Between 4 °C and 60 °C', 'Above 74 °C', 'Between 20 °C and 40 °C', 'B'),
            ('Hot food on the serving line must be held at...', '40 °C or hotter', '50 °C or hotter', '60 °C or hotter', None, 'C'),
            ('Which cutting board colour is for raw meat?', 'Green', 'Red', 'White', 'Any', 'B'),
            ('You can sanitize a surface without cleaning it first.', False),
            ('A guest asks if the perogies contain egg and you are not sure. You should...', 'Say probably not',
             'Fetch the kitchen lead', 'Suggest they try a small piece', None, 'B'),
        ]),
    'child': ('Child & Youth Safeguarding', 'Safety', 'child_care', ['Childcare'], 365, True,
        'Required for volunteers who work with children at concerts, the kids\' zone and Ukrainian school. '
        'Renew it every year.',
        [
            ('Our safeguarding commitments', """
Every child who comes to a parish event should be safe and feel safe. These commitments apply to all
staff and volunteers, with no exceptions:

- A current criminal record check is on file before you work with children.
- You are never alone with a child out of sight of other adults.
- You don't contact children privately by phone, text or social media.
- You don't take photos of children for personal use. The event photographer works only with consent
  forms on file.
- Physical contact is limited to what is appropriate and in public: a high five, helping a small child
  with a coat.
"""),
            ('The rule of three and supervision ratios', """
**Rule of three:** there are always at least two adults with any group of children, or one adult in
plain view of others. That protects children and it protects you.

| Age | Adults to children |
| --- | --- |
| Under 3 | 1 : 3 |
| 3 to 5 | 1 : 6 |
| 6 to 12 | 1 : 10 |

At sign-in, every child is signed in and out by a parent or guardian on the clipboard. Never release a
child to anyone not listed.
"""),
            ('Recognizing and reporting concerns', """
You are not expected to investigate. You are expected to **notice and report**.

If a child tells you something worrying: stay calm, listen, don't promise to keep a secret, and write
down what they said in their own words as soon as you can.

Report it the same day to the safeguarding lead (Halyna Boyko). In British Columbia, anyone who believes
a child needs protection must report it to the Ministry of Children and Family Development at
**1-800-663-9122**, at any hour.
"""),
        ],
        [
            ('What is the minimum number of adults with a group of children?', 'One', 'Two, or one in plain view of others', 'Three', None, 'B'),
            ('A child asks you to keep a secret before telling you something. You should...',
             'Promise to keep it', 'Explain you may need to tell someone who can help', 'Refuse to listen', None, 'B'),
            ('It is fine to text a child volunteer directly about shift times.', False),
            ('Anyone in BC who believes a child needs protection must report it.', True),
        ]),
    'firstaid': ('Event First Aid Refresher', 'Safety', 'medical_services', ['First Aid'], 365, True,
        'For volunteers holding a current first aid certificate who staff the first aid tent. Covers our kit, '
        'outdoor-event hazards and incident reports.',
        [
            ('Your role as a first aid attendant', """
At parish events you are the first responder until paramedics arrive. Your job is to **assess, treat
within your training and escalate early**. You are not expected to diagnose.

- Introduce yourself and ask consent before helping.
- Gloves on before you touch anyone.
- If in doubt, call 911. Nobody will ever be upset that you called.
- Stay at the tent or tell the coordinator where you are going.

Kit inventory is checked against the list taped inside each kit lid at the start of every shift.
"""),
            ('Heat, dehydration and outdoor events', """
The Independence Day festival runs eight hours on an open lawn in late August. Most of what you'll
treat is heat-related.

- **Heat exhaustion:** heavy sweating, dizziness, nausea. Move to shade, loosen clothing, small sips of
  water, cool cloths.
- **Heat stroke:** confusion, hot dry skin, collapse. This is an emergency. Call 911 and cool them
  aggressively while you wait.

Watch seniors and dancers between performances. Keep the cooler of water stocked and remind people to drink.
"""),
            ('Incident reports', """
Every treatment gets an incident report, however small. A plaster for a paper cut is still a report.

Write down: the time, the person's name and contact, what happened, what you saw, what you did and
whether they left on their own, with family or by ambulance. Hand completed forms to the event
coordinator before you leave. They're kept for seven years.
"""),
        ],
        [
            ('A dancer is confused, with hot dry skin. What should you do first?', 'Give them water and let them rest',
             'Call 911 and start cooling them', 'Send them home', None, 'B'),
            ('You should ask for consent before giving first aid to a conscious person.', True),
            ('Which treatments need an incident report?', 'Only serious injuries', 'Only ambulance calls', 'Every treatment', None, 'C'),
        ]),
    'cash': ('Cash Handling & Point of Sale', 'Operations', 'point_of_sale', ['Cash Handling'], None, True,
        'For cashiers at the bazaar, perogy sales and Easter paska sale.',
        [
            ('Floats, counts and the two-person rule', """
Each cash box starts with a **$200 float**: counted, signed for and sealed by two people. Two people are
present every time the box is opened for a count, and at the end of the shift.

- Count large bills back to the customer as you give change.
- Bills of $50 or more go under the tray, not in it.
- Never leave the cash box unattended, even for a minute. Ask someone to cover.
- If a count is off, write the difference on the sheet. Don't make it up from your own pocket.
"""),
            ('Using the card reader', """
Most guests pay by card or tap. The Square readers are charged overnight in the parish office.

1. Enter the amount on the tablet and tap **Charge**.
2. Let the customer tap, insert or swipe. Never handle their card yourself.
3. Offer an emailed or texted receipt.

If the reader goes offline it stores payments and uploads them later, so keep selling and tell the
coordinator. Refunds can only be issued by the coordinator.
"""),
        ],
        [
            ('How many people must be present when a cash box is counted?', 'One', 'Two', 'Three', None, 'B'),
            ('If the cash count is $5 short, you should cover it from your own pocket.', False),
            ('Who can issue card refunds?', 'Any cashier', 'The event coordinator', 'The customer', None, 'B'),
        ]),
    'trauma': ('Trauma-Informed Support for Newcomers', 'Community Support', 'diversity_3', ['Settlement Support'], None, True,
        'Required for front desk and settlement roles at the Welcome Centre. How displacement affects people, and '
        'how to help without causing harm.',
        [
            ('Understanding displacement and trauma', """
Since 2022, more than 300,000 Ukrainians have come to Canada under the CUAET program. Many of the people
who visit the Welcome Centre left in a hurry, spent weeks in transit and still have family in danger.

Trauma is not a weakness or a diagnosis. It is a normal response to abnormal events, and it shows up in
ordinary ways:

- difficulty concentrating, or forgetting appointments and documents
- irritability, or sudden tears over small things
- exhaustion, trouble sleeping
- strong reactions to loud noises, sirens or uniforms
- reluctance to trust institutions, forms and officials

When someone seems rude, disorganized or "difficult," assume they are carrying more than you can see.
Our job is not to treat trauma. It is to **avoid adding to it**, and to make the Welcome Centre a place
where people feel safe enough to ask for what they need.

### The four Rs

Trauma-informed services **realize** how common trauma is, **recognize** its signs, **respond** by
adapting how they work and **resist re-traumatizing** people. That last one is mostly about small
things: explaining what happens next, offering choices, and never making someone retell their story
just to get help.
"""),
            ('Creating a safe, predictable space', """
Predictability calms the nervous system. Simple habits make a real difference:

1. **Introduce yourself and your role** every time: "I'm Taras, I volunteer at the front desk. I can
   help with forms but I'm not a lawyer."
2. **Explain what will happen** before it happens: how long the wait is, what the navigator will ask,
   what they'll need to bring next time.
3. **Offer choices** wherever you can: which room, which language, whether a child stays with them.
4. **Ask only what you need.** "Which form do you need help with?" not "What happened to you?"
5. **Follow through.** If you promise a call back on Thursday, call on Thursday, even just to say you
   don't have an answer yet.

The quiet room is always available. Offer it to anyone who becomes upset, and let them decide whether
they want company.

### Language

Speak plainly and slowly, avoid idioms and check understanding by asking people to tell you the next
step in their own words. Use an interpreter for anything about immigration status, health or money,
even if the person's English seems good.
"""),
            ('Boundaries and self-care', """
Hearing hard stories, week after week, takes a toll. That is called **vicarious trauma**, and it happens
to caring, competent people.

Healthy boundaries protect both you and the people you serve:

- Don't give out your personal phone number or connect on social media.
- Don't lend money or offer housing. Refer to the navigator, who knows what support exists.
- Don't promise outcomes you don't control, like "you'll definitely get the work permit."
- It's fine to say "I don't know, but I'll find out who does."

Look after yourself, too. Take your breaks, debrief with the shift lead after a hard conversation and
step back for a few weeks if you need to. Volunteers can book free, confidential sessions through the
parish counselling partner. Ask Halyna for the number.
"""),
            ('Referral pathways in Metro Vancouver', """
You are not expected to know everything. You are expected to know **where to send people**.

| Need | Refer to |
| --- | --- |
| Immigration status, work permits, PR | Settlement navigator (Tue and Thu), then a licensed consultant or legal clinic |
| Housing | Navigator, BC Housing registry |
| Medical | MSP enrollment help at the desk; walk-in clinics list in the blue binder |
| Mental health crisis | 9-8-8 suicide crisis line, or 911 |
| Children's school registration | New Westminster Schools welcome centre |
| Food | Parish food pantry (Wednesdays), Food Bank list in the binder |
| English classes | LINC classes; our conversation circle on Monday evenings |

Write every referral in the visitor log, without personal details beyond first name, so the navigator
can follow up.
"""),
        ],
        [
            ('Someone at the front desk is short with you and keeps losing their papers. A trauma-informed response is to...',
             'Tell them to come back when they are organized', 'Stay patient, explain the next step and offer help with the papers',
             'Ask them what happened in Ukraine', None, 'B'),
            ('Which question fits a trauma-informed approach?', 'What happened to you?', 'Which form do you need help with today?',
             'Why didn\'t you bring your documents?', None, 'B'),
            ('It is a good idea to give a family your phone number so they can reach you any time.', False),
            ('Who should handle a question about work-permit eligibility?', 'Any front desk volunteer',
             'The settlement navigator or a licensed consultant', 'The family\'s neighbours', None, 'B'),
            ('Vicarious trauma can affect caring, competent volunteers.', True),
        ]),
    'interp': ('Community Interpreting Basics', 'Community Support', 'translate', ['Interpreting', 'Ukrainian Language'], None, True,
        'For bilingual volunteers interpreting Ukrainian or Russian at the Welcome Centre, clinics and job fairs.',
        [
            ('The interpreter\'s role', """
A community interpreter makes communication possible. You are a **conduit**, not a helper, adviser or
advocate.

- Speak in the first person: "I need help with my rent," not "She says she needs help with her rent."
- Interpret everything that is said, including small talk, frustration and side comments.
- Position yourself so the two parties look at each other, not at you.
- If you don't know a term, say so and ask. Never guess at medical or legal words.
"""),
            ('Accuracy, confidentiality and impartiality', """
**Accuracy:** keep the meaning, tone and register. Don't soften bad news or tidy up rudeness.

**Confidentiality:** everything you interpret is private. Don't discuss it outside the session, even
anonymously with other volunteers.

**Impartiality:** if you know the family personally, or you have a strong opinion about the situation,
tell the navigator before the session starts so they can find someone else.
"""),
            ('Sight translation of forms', """
Newcomers often need help understanding a form, such as an MSP application or a school registration.
Sight translation means reading a document aloud in the other language.

Read it through silently first and flag unfamiliar terms. Translate what the form *says*, not what you
think the person should answer. Never fill in a form for someone. Point to where their answer goes and
let them write it.
"""),
        ],
        [
            ('When interpreting, you should speak...', 'In the third person ("she says...")', 'In the first person ("I need...")', 'In a summary', None, 'B'),
            ('You can skip an insult when interpreting to keep things calm.', False),
            ('You know the family personally. What should you do?', 'Interpret anyway', 'Tell the navigator before the session',
             'Give them extra advice', None, 'B'),
        ]),
    'kiosk': ('Kiosk & Check-in Procedures', 'Onboarding', 'tablet_mac', ['Customer Service'], None, False,
        'Draft: how coordinators set up the check-in tablet and fix missed check-outs.',
        [
            ('Setting up the kiosk tablet', """
*Draft. Being written by Mykola, not yet published.*

1. Charge the tablet the night before and connect it to the venue Wi-Fi.
2. Open the console, choose **Kiosk** and pick the event or venue.
3. Lock the tablet in guided-access mode so volunteers can't leave the kiosk screen.
"""),
        ],
        []),
}

# key: (name, icon, description, permanent, venue keys, beneficiaries/h, [(module, mandatory)], preferred skills)
ROLES = {
    'kitchen': ('Kitchen Volunteer', 'restaurant', 'Prep, cook and serve under the kitchen lead. Includes pinching perogies, '
                'portioning holubtsi and keeping the line moving. Food safety training required.',
                False, [], None, [('orient', True), ('food', True)], ['Cooking', 'Food Handling']),
    'setup': ('Setup & Teardown Crew', 'chair', 'Tables, chairs, tents and signage before the doors open, and everything '
              'back in storage afterwards. Some lifting.',
              False, [], None, [('orient', True)], ['Event Setup']),
    'greeter': ('Greeter & Registration', 'waving_hand', 'First face guests see. Welcome people, hand out programs, answer '
                'questions and direct them to where they need to go.',
                False, [], None, [('orient', True), ('interp', False)], ['Customer Service', 'Ukrainian Language']),
    'kids': ("Children's Program Helper", 'child_care', "Run crafts and games in the kids' zone alongside a lead. Safeguarding "
             'training and a criminal record check required.',
             False, [], None, [('orient', True), ('child', True)], ['Childcare']),
    'firstaid': ('First Aid Attendant', 'medical_services', 'Staff the first aid tent. Requires a current first aid '
                 'certificate on file and the event refresher.',
                 False, [], None, [('orient', True), ('firstaid', True)], ['First Aid']),
    'interpreter': ('Interpreter (Ukrainian / English)', 'translate', 'Interpret for newcomers at clinics, orientations '
                    'and job fairs.',
                    False, [], None, [('orient', True), ('interp', True), ('trauma', False)], ['Interpreting', 'Ukrainian Language']),
    'cashier': ('Cashier', 'point_of_sale', 'Take payments at bazaar tables, perogy sales and the paska sale. Two-person '
                'cash rule applies.',
                False, [], None, [('orient', True), ('cash', True)], ['Cash Handling', 'Customer Service']),
    'photographer': ('Event Photographer', 'photo_camera', 'Capture the day for the parish bulletin and social media. '
                     'Only photograph children with consent stickers.',
                     False, [], None, [], ['Photography']),
    'parking': ('Parking & Traffic Marshal', 'traffic', 'Direct cars in the lot and on Fourth Avenue, keep the accessible '
                'spots and fire lane clear. High-visibility vest provided.',
                False, [], None, [('orient', True)], ['Driving']),
    'packer': ('Food Hamper Packer', 'inventory_2', 'Sort donations and pack hampers for families from the pantry list.',
               False, [], None, [('orient', True), ('food', False)], ['Food Handling']),
    'frontdesk': ('Welcome Centre Front Desk', 'support_agent', 'Greet drop-in visitors, book navigator appointments and '
                  'help with simple forms. Regular weekly shifts.',
                  True, ['centre'], 6, [('orient', True), ('trauma', True)], ['Customer Service', 'Ukrainian Language']),
    'navigator': ('Settlement Navigator', 'explore', 'One-to-one appointments helping newcomer families with housing, MSP, '
                  'school registration and work permits. Refers on for legal advice.',
                  True, ['centre'], 2, [('orient', True), ('trauma', True), ('interp', True)], ['Settlement Support', 'Interpreting']),
    'tourguide': ('Cathedral Tour Guide', 'church', 'Lead the 30-minute iconography tour after Sunday Liturgy and for '
                  'school groups.',
                  True, ['cathedral'], 10, [('orient', True)], ['Customer Service', 'Ukrainian Language']),
    'pantry': ('Food Pantry Sorter', 'shelves', 'Wednesday evening pantry: sort donations, stock shelves and hand out '
               'grocery bags.',
               True, ['hall'], 12, [('orient', True), ('food', True)], ['Food Handling']),
}

CERTIFICATES = {
    'firstaid': ('Standard First Aid & CPR-C', 'Canadian Red Cross', 'medical_services', 1095, 'Certificate (front),Wallet card',
                 'Two-day course covering first aid, CPR level C and AED use. Required for first aid attendants.'),
    'foodsafe': ('FOODSAFE Level 1', 'FOODSAFE Secretariat of BC', 'restaurant', 1825, 'Certificate',
                 'Provincial food handler certification. Kitchen leads must hold it.'),
    'crc': ('Criminal Record Check (Vulnerable Sector)', 'BC Ministry of Public Safety', 'verified_user', 1825, 'Clearance letter',
            'Required before working with children or vulnerable adults. Apply online through the Criminal Records Review Program.'),
    'licence': ("Class 5 Driver's Licence", 'ICBC', 'directions_car', None, 'Licence (front)',
                'Needed to drive the parish van for deliveries and pickups.'),
}

# username: (first, last, city, language, skills, joined days ago, weight for being picked, modules, extra)
# modules: {key: days ago completed, or None for "soon after joining", or 'started' / ('started', lessons done)}
PEOPLE = {
    'admin': ('Olena', 'Kovalchuk', 'New Westminster, BC', 'en', ['Customer Service', 'Ukrainian Language'], 900, 0,
              {'orient': None}, dict(superuser=True, bio='Volunteer programme manager at Holy Eucharist Cathedral. Ask me anything about getting involved.')),
    'mykola.petrenko': ('Mykola', 'Petrenko', 'Burnaby, BC', 'uk', ['Event Setup', 'First Aid', 'Driving'], 820, 0,
                        {'orient': None, 'food': None, 'cash': None, 'firstaid': 150}, dict(staff=True, bio='Events coordinator. If it involves a tent, a stage or 300 kg of potatoes, it probably went through me.')),
    'halyna.boyko': ('Halyna', 'Boyko', 'New Westminster, BC', 'uk', ['Settlement Support', 'Interpreting', 'Ukrainian Language', 'Childcare'], 760, 0,
                     {'orient': None, 'trauma': None, 'interp': None, 'child': 80}, dict(staff=True, perm=['navigator'], bio='Settlement program lead and safeguarding lead. Former social worker in Lviv.')),
    'sofia.melnyk': ('Sofia', 'Melnyk', 'New Westminster, BC', 'en', ['Cooking', 'Food Handling', 'Event Setup', 'Ukrainian Language', 'Customer Service'], 720, 9,
                     {'orient': None, 'food': 300, 'child': 200, 'firstaid': 160, 'cash': None, 'interp': None}, dict(perm=['tourguide'], bio='Third-generation parishioner. Perogy bee regular since I could reach the table. I lead the iconography tours most Sundays.')),
    'andriy.kovalenko': ('Andriy', 'Kovalenko', 'Burnaby, BC', 'en', ['Cooking', 'Food Handling', 'Cash Handling'], 650, 7,
                         {'orient': None, 'food': 420, 'cash': None}, dict(bio='Kitchen lead for Sviat Vechir. Retired chef, 30 years in hotel kitchens.')),
    'taras.shevchuk': ('Taras', 'Shevchuk', 'New Westminster, BC', 'uk', ['Ukrainian Language', 'Interpreting', 'Customer Service'], 400, 4,
                       {'orient': None, 'trauma': None, 'interp': None, 'cash': None}, dict(perm=['frontdesk'], code='204816', bio='Came from Kharkiv in 2022. Now I help other families at the Welcome Centre front desk three mornings a week.')),
    'iryna.tkachenko': ('Iryna', 'Tkachenko', 'Coquitlam, BC', 'en', ['Childcare', 'First Aid', 'Ukrainian Language'], 540, 5,
                        {'orient': None, 'child': 120, 'firstaid': 500}, dict(bio='Kindergarten teacher. I run the kids\' zone at most festivals.')),
    'maria.santos': ('Maria', 'Santos', 'New Westminster, BC', 'es', ['First Aid', 'Driving'], 300, 4,
                     {'orient': None, 'firstaid': 90}, dict(bio='ER nurse at Royal Columbian. Happy to staff the first aid tent.')),
    'james.wilson': ('James', 'Wilson', 'Burnaby, BC', 'en', ['Event Setup', 'Driving', 'Photography'], 480, 6,
                     {'orient': None, 'cash': None}, dict(bio='Married into a Ukrainian family and got drafted for setup crew. Never looked back.')),
    'oksana.lysenko': ('Oksana', 'Lysenko', 'New Westminster, BC', 'uk', ['Interpreting', 'Ukrainian Language', 'Settlement Support'], 210, 5,
                       {'orient': None, 'interp': None, 'trauma': None, 'child': None}, dict(bio='Translator from Odesa. I interpret at clinics and the job fair.')),
    'ivan.bondarenko': ('Ivan', 'Bondarenko', 'New Westminster, BC', 'uk', ['Event Setup', 'Driving'], 120, 4,
                        {'orient': None, 'food': None}, dict(bio='Arrived last year with my wife and two kids. Volunteering to give back and practise my English.')),
    'emily.thompson': ('Emily', 'Thompson', 'Vancouver, BC', 'en', ['Customer Service'], 45, 3,
                       {'orient': None, 'food': ('started', 2)}, dict(code='731905', bio='SFU student, studying social work.')),
    'yuriy.hnatyshyn': ('Yuriy', 'Hnatyshyn', 'Surrey, BC', 'en', ['Photography', 'Event Setup'], 600, 3,
                        {'orient': None}, dict(bio='Amateur photographer. Most of the festival photos on the parish site are mine.')),
    'kateryna.moroz': ('Kateryna', 'Moroz', 'Burnaby, BC', 'en', ['Cooking', 'Childcare'], 350, 5,
                       {'orient': None, 'food': None, 'child': 250}, dict(bio='Mom of three, baker of too many paska.')),
    'liam.oconnor': ("Liam", "O'Connor", 'New Westminster, BC', 'en', ['Event Setup', 'Customer Service'], 260, 3,
                     {'orient': None, 'cash': None}, dict(bio='Live two blocks from the cathedral. Came for the perogies, stayed for the people.')),
    'anastasia.romanyuk': ('Anastasia', 'Romanyuk', 'Coquitlam, BC', 'uk', ['Childcare', 'Photography'], 90, 3,
                           {'orient': None, 'child': None}, dict(bio='Grade 12, Ukrainian dance group. Earning volunteer hours for my scholarship application.')),
    'david.kim': ('David', 'Kim', 'Burnaby, BC', 'en', ['Cash Handling', 'Customer Service'], 500, 3,
                  {'orient': None, 'cash': None, 'food': None}, dict(bio='Accountant. I count the bazaar floats.')),
    'roman.savchuk': ('Roman', 'Savchuk', 'New Westminster, BC', 'en', ['Event Setup', 'Driving'], 700, 6,
                      {'orient': None, 'food': None}, dict(perm=['pantry'], bio='Retired machinist. I drive the parish van and fix whatever is broken.')),
    'nadia.kravets': ('Nadia', 'Kravets', 'Burnaby, BC', 'uk', ['Cooking', 'Food Handling'], 430, 5,
                      {'orient': None, 'food': None, 'cash': None}, dict(perm=['pantry'])),
    'chloe.martin': ('Chloe', 'Martin', 'Vancouver, BC', 'fr', ['Customer Service'], 150, 2,
                     {'orient': None, 'cash': None}, dict(bio='Originally from Montréal. Volunteering on weekends while I look for work.')),
    'petro.zaitsev': ('Petro', 'Zaitsev', 'Surrey, BC', 'uk', ['Driving', 'Event Setup'], 380, 3,
                      {'orient': None, 'food': None}, dict(perm=['pantry'])),
    'lesia.fedorenko': ('Lesia', 'Fedorenko', 'New Westminster, BC', 'uk', ['Interpreting', 'Ukrainian Language', 'Childcare'], 280, 4,
                        {'orient': None, 'interp': None, 'child': 260}, dict()),
    'michael.brown': ('Michael', 'Brown', 'Coquitlam, BC', 'en', ['First Aid', 'Driving'], 200, 2,
                      {'orient': None, 'firstaid': 60}, dict(bio='Paramedic with BC Emergency Health Services.')),
    'vira.danylyuk': ('Vira', 'Danylyuk', 'New Westminster, BC', 'uk', ['Cooking'], 760, 5,
                      {'orient': None, 'food': 755}, dict(bio='Making varenyky at this parish since 1987.')),
    'olha.marchenko': ('Olha', 'Marchenko', 'Burnaby, BC', 'uk', ['Settlement Support', 'Interpreting'], 75, 2,
                       {'orient': None, 'interp': None, 'trauma': ('started', 2)}, dict()),
    'sarah.nguyen': ('Sarah', 'Nguyen', 'Vancouver, BC', 'en', ['Photography', 'Customer Service'], 330, 2,
                     {'orient': None}, dict()),
    'bohdan.kuzmenko': ('Bohdan', 'Kuzmenko', 'Surrey, BC', 'uk', ['Event Setup'], 160, 3,
                        {'orient': None, 'food': None}, dict()),
    'tetiana.pavlenko': ('Tetiana', 'Pavlenko', 'New Westminster, BC', 'uk', ['Customer Service', 'Ukrainian Language'], 60, 2,
                         {'orient': None, 'trauma': None}, dict(perm=['frontdesk'])),
    'markian.hrynyk': ('Markian', 'Hrynyk', 'Burnaby, BC', 'en', ['Event Setup', 'Photography'], 110, 2,
                       {'orient': None}, dict(bio='Plast scout. Strong back, will carry tables.')),
    'kevin.oneill': ("Kevin", "O'Neill", 'Calgary, AB', 'en', ['Event Setup', 'Food Handling'], 520, 4,
                     {'orient': None, 'food': None}, dict(left=60, notes='Moved to Calgary in August. Account deactivated at his request; happy to have him back if he returns.')),
    'daniel.chen': ('Daniel', 'Chen', 'New Westminster, BC', 'en', ['Event Setup'], 4, 0,
                    {'orient': ('started', 1)}, dict(code='560342', bio='New to the neighbourhood, looking to help out.')),
    'priya.sharma': ('Priya', 'Sharma', 'Burnaby, BC', 'en', [], 1, 0, {}, dict()),
}

FEEDBACK_ENJOYED = [
    'Great team, everyone was friendly and knew what they were doing.',
    'Seeing the families\' faces when they got their hampers.',
    'The kitchen crew was so welcoming to a first-timer.',
    'Well organized. I knew exactly where to go when I arrived.',
    'Working alongside newcomers who are now volunteering themselves.',
    'The music! And the food at the end of the shift.',
    'Felt like family. I learned to pinch perogies properly at last.',
    'Coordinator checked in on us regularly and made sure we took breaks.',
    '',
]
FEEDBACK_SUGGESTIONS = [
    'More water at the volunteer tent, it got hot after 2pm.',
    'Setup started late because the keys to the storage room were missing.',
    'A printed map of the stations would help new volunteers.',
    'Could we get the shift schedule a few days earlier?',
    'Parking was hard to find. Maybe reserve a few spots for volunteers.',
    'Nothing. Keep doing what you are doing!',
    'Name tags with the languages we speak would help guests.',
    '',
    '',
]
ENDORSEMENT_TEXT = [
    'Kept the whole line moving when we were two people short. Calm under pressure.',
    'Showed me the ropes on my first shift. Patient and kind.',
    'Incredible with the kids, they didn\'t want to leave.',
    'Jumped in wherever needed without being asked.',
    'Interpreted for a family who were really struggling. Made all the difference.',
    'Always first to arrive and last to leave.',
    'Super organized, had the float counted and balanced in minutes.',
    '',
]

ADMIN_NOTES = [
    ('sofia.melnyk', 5, 'Natural leader. Ask her to mentor the new kitchen volunteers at the next perogy bee.'),
    ('andriy.kovalenko', 5, 'Ran the Sviat Vechir kitchen flawlessly. Consider him for a kitchen lead role year-round.'),
    ('taras.shevchuk', 5, 'Visitors ask for him by name at the front desk.'),
    ('liam.oconnor', 3, 'Reliable on setup but left two shifts early without telling the coordinator. Had a friendly chat about it.'),
    ('anastasia.romanyuk', 4, 'Great with kids. Under 19, so always pair her with an adult lead.'),
    ('ivan.bondarenko', 4, 'Very willing, English improving quickly. Would make a good interpreter once he finishes the module.'),
    ('chloe.martin', 4, 'Speaks French; useful for the bilingual Canada Day events.'),
    ('kevin.oneill', 4, 'Solid setup crew member before he moved.'),
]


# ---------------------------------------------------------------- Bell Tower tasks (--belltower)

# Planning list for every event that hasn't ended: (title, details, days before the start).
# Deadlines are 09:00 that day; most tasks whose deadline has passed are done.
PLANNING_TASKS = [
    ('Confirm the booking with the parish office', 'Check the date against the parish calendar and ask for the side door key.', 21),
    ('Post the event in the Sunday bulletin', 'Send the blurb and a photo to the bulletin editor by Wednesday noon.', 14),
    ('Fill the open volunteer roles', 'Check Roles & Staffing and personally invite people for anything under half full.', 7),
    ('Order food for the volunteers', 'Pizza or sandwiches from Royal Deli; order for the sign-up count plus 10%.', 3),
    ('Print sign-in sheets and name tags', '', 2),
    ('Send the reminder email to volunteers', 'Include parking, the side entrance, and the check-in time.', 1),
]
EVENT_PLANNING_TASKS = {
    'Fall Perogy Bee': [
        ("Buy 50 kg of potatoes and the farmer's cheese", 'Costco business centre; the parish card is in the office safe.', 4),
        ('Book the walk-in freezer for the finished perogies', 'Ask Bohdan for the Saturday afternoon slot.', 6),
    ],
    'Thanksgiving Community Dinner': [
        ('Pick up the turkeys', 'Twelve birds, prepaid at Save-On Foods on 6th Street. Bring the cooler bags.', 2),
        ('Call the seniors who need a ride', 'List is in the shared folder; two drivers have offered.', 3),
        ('Print bilingual menu cards', 'Ukrainian and English, one per table.', 2),
    ],
    'Christmas Bazaar': [
        ('Collect consignment from the embroidery group', 'Count and price every piece with Halyna.', 10),
        ('Get the cash floats from the treasurer', 'Two boxes, $200 each in small bills.', 2),
        ('Rent 20 extra tables', 'Party Rentals on Columbia; delivery the evening before.', 14),
    ],
    'Holodomor Remembrance Vigil': [
        ('Order 300 candles in jars', 'Same supplier as last year; the invoice is in the office.', 12),
        ('Confirm the reader for the list of names', 'Ask Father Taras who is reading this year.', 7),
    ],
    'Newcomer Orientation & Settlement Clinic': [
        ('Print the MSP and SIN forms in Ukrainian', 'Thirty of each, plus the school registration checklist.', 2),
        ('Confirm the bank representative', 'Coast Capital said they could send someone for two hours.', 5),
    ],
    'Thanksgiving Hamper Packing Night': [
        ('Confirm the turkey voucher count with Superstore', '140 vouchers; pick up at the customer service desk.', 2),
    ],
}

# Tasks for each role's list: (title, details, minutes after the event starts it's due, or None).
ROLE_TASKS = {
    'kitchen': [
        ('Hairnets and aprons on, hands washed', '', 0),
        ('Set up the dough station', 'Flour bins on the left, rolling pins and cutters on the long table.', 15),
        ('Label and date every tray before it goes in the freezer', 'Use the masking tape and black marker by the fridge.', None),
        ('Wipe down the counters and run the dishwasher', 'Last load on the sanitize cycle.', 'end'),
    ],
    'setup': [
        ('Set out the tables in rows of six', 'Leave a wide aisle down the middle for wheelchairs.', -30),
        ('Hang the welcome banner at the front door', 'It is in the storage room, top shelf.', -15),
        ('Stack the chairs and sweep the hall', '', 'end'),
    ],
    'greeter': [
        ('Open the registration table', 'Sign-in sheets, pens and name tags are in the blue bin.', -10),
        ('Count attendees for the impact report', 'Tally at the door; enter the number in Town Hall afterwards.', 'end'),
    ],
    'cashier': [
        ('Get the cash float from the office', 'Sign it out in the binder.', -15),
        ('Count the float with a second person', '', 0),
        ('Reconcile the till and seal the deposit bag', 'Two signatures on the slip, then into the office safe.', 'end'),
    ],
    'interpreter': [
        ("Check in with the settlement navigator for today's families", '', 0),
        ('Help families with the MSP application', 'Bring them to table 3; the navigator reviews before it is mailed.', None),
    ],
    'packer': [
        ('Set up the packing stations', 'Five stations: canned goods, vegetables, protein, treats, card.', 0),
        ('Tape and label the finished hampers', 'Family size goes on the label; large hampers by the door.', None),
        ('Break down the empty boxes for recycling', '', 'end'),
    ],
    'kids': [
        ('Set up the craft table', 'Paper, glue sticks and crayons; no scissors for the little ones.', -15),
        ('Head count every 30 minutes', 'Write it on the clipboard by the door.', None),
    ],
    'photographer': [
        ('Shoot the opening and a wide shot of the hall', 'Check the no-photo list at registration first.', 15),
        ('Upload the photos to the shared drive', 'Folder for this event, by Monday noon.', None),
    ],
}
GENERIC_ROLE_TASKS = [('Check in with the event lead', 'Find out where you are needed first.', 0)]

# The live event also gets urgent tasks (due in minutes from now) and a closing checklist.
LIVE_URGENT_TASKS = {
    'packer': [('Restock the carrots at station 2', 'Two crates are by the loading door.', 15)],
    'greeter': [('Bring more name tags to the door', 'The spare box is in the office.', 60)],
}
CLOSING_CHECKLIST = [
    ('Turn off the urn and the hall lights', ''),
    ('Take the garbage and recycling to the bins', 'Recycling goes in the blue bins behind the hall.'),
    ('Lock the side door and return the key', 'Key goes in the drop box at the parish office.'),
]


def at(day, clock):
    """Aware datetime `day` (a date) at 'HH:MM' local time."""
    hours, minutes = map(int, clock.split(':'))
    return timezone.make_aware(datetime.combine(day, time(hours, minutes)))


class Command(BaseCommand):
    help = 'Fills an empty database with a believable demo: a parish volunteer programme in New Westminster, BC'

    def add_arguments(self, parser):
        parser.add_argument('--password', default='password123', help='Password for every demo volunteer (default password123; admin is admin/admin)')
        parser.add_argument('--belltower', default=os.environ.get('BELLTOWER_URL', ''), metavar='URL',
                            help='Also seed task lists in this Bell Tower server (or set BELLTOWER_URL). '
                                 'Creates new lists there on every run.')
        parser.add_argument('--belltower-token', default=os.environ.get('BELLTOWER_TOKEN', ''), metavar='TOKEN',
                            help="API token of a Bell Tower staff account, from its account page (or set BELLTOWER_TOKEN). "
                                 'Staff, so demo people can be given Bell Tower accounts.')

    def handle(self, *args, **options):
        if User.objects.exists():
            raise CommandError('The database already has users. Run this on a fresh database: delete db.sqlite3, '
                               'run "python manage.py migrate", then run this command again.')
        self.password = options['password']
        random.seed(2026)
        translation.activate('en')
        self.now = timezone.now()
        self.today = timezone.localdate()

        # Demo people must never reach a real mailing list, and shift points are worked out once at
        # the end (when every sign-up and attendee count is final) instead of after every save.
        with mock.patch('base.listmonk.queue_sync'), mock.patch('base.points.award_shift'):
            with transaction.atomic():
                self.seed()
        self.stdout.write('Scoring shifts...')
        with transaction.atomic():
            points.rebuild()
        if options['belltower'] and options['belltower_token']:
            self.seed_tasks(options['belltower'], options['belltower_token'])
        else:
            self.stdout.write('Skipping task lists: pass --belltower URL --belltower-token TOKEN to seed them in Bell Tower.')
        self.summary()

    def day(self, offset):
        return self.today + timedelta(days=offset)

    # ------------------------------------------------------------ seeding

    def seed(self):
        self.busy = defaultdict(list)       # user pk -> [(start, end)] of signed-up slots and shifts
        self.trained = {}                   # (user pk, module key) -> completed_at
        self.attended = defaultdict(list)   # event pk -> users with a shift

        self.stdout.write('Organization settings...')
        self.seed_settings()
        self.stdout.write('Skills, venues and training...')
        self.skills = {name: Skill.objects.create(name=name) for name in SKILLS}
        self.categories = {name: EventCategory.objects.create(name=name) for name in CATEGORIES}
        self.seed_venues()
        self.seed_training()
        self.seed_roles()
        self.stdout.write('People...')
        self.seed_people()
        self.seed_training_progress()
        self.stdout.write('Events, sign-ups and shifts...')
        self.seed_events()
        self.stdout.write('Permanent roles and kiosk shifts...')
        self.seed_permanent_shifts()
        self.stdout.write('Certificates, feedback and notifications...')
        self.seed_certificates()
        self.seed_staff_notes()
        self.seed_notifications()
        self.seed_api_keys()

    def seed_settings(self):
        for number, name, min_points, benefits in LEVELS:
            Level.objects.create(numeric_name=number, name=name, min_points=min_points, benefits=benefits)
        PointsRules.get()
        HeroSection.objects.update_or_create(pk=1, defaults=dict(
            title='Serve your community with us',
            subtitle='Cook for Sviat Vechir, welcome newcomer families, run the festival. '
                     'Find a role that fits your week.',
            button_1_text='Find a role', button_1_url='/en/opportunities/',
            button_2_text='Start training', button_2_url='/en/training/',
        ))
        site = SiteSettings.get_settings()
        if site.company_name == 'Company Name':
            site.company_name = 'Holy Eucharist Cathedral'
        if not site.terms_of_service:
            site.terms_of_service = (
                '## Volunteer agreement\n\n'
                'By volunteering with the parish you agree to follow the volunteer code of conduct, complete any '
                'training required for the roles you sign up for, and keep confidential anything you learn about '
                'the people we serve.\n\n'
                '## Your information\n\n'
                'We keep your name, contact details, availability, skills and volunteer history to schedule '
                'shifts and recognize your contribution. Staff can see your profile; other volunteers see only '
                'your name, level and endorsements. Ask the volunteer programme manager to export or delete '
                'your data at any time.\n\n'
                '## Photos\n\n'
                'Events may be photographed for the parish bulletin and social media. Tell the event photographer '
                'if you would rather not appear.'
            )
        site.save()

    def seed_venues(self):
        features = {f.name: f for f in VenueFeature.objects.all()}
        self.venues = {}
        for key, data in VENUES.items():
            data = dict(data)
            wanted, hours = data.pop('features'), data.pop('hours')
            after_hours = data.pop('staff_after_hours', False)
            venue = Venue.objects.create(**data)
            venue.features.set([features[name] for name in wanted if name in features])
            for weekday, (opens, closes) in hours.items():
                OperatingHour.objects.create(venue=venue, day_of_week=weekday, open_time=opens, close_time=closes,
                                             open_to_staff_outside_hours=after_hours)
            self.venues[key] = venue

        notes = [
            ('centre', 'The elevator is out of service until Thursday. Please use the ramp at the north entrance '
                       'and help visitors with strollers.', 1, 4),
            ('hall', 'The walk-in cooler is being serviced Wednesday morning. Store pantry donations in the '
                     'hallway fridges until noon.', 0, 3),
            ('hall', 'Thanksgiving food drive: drop-off bins are by the loading dock. Do not leave boxes in '
                     'the stairwell.', 3, 10),
            ('cathedral', 'Iconography tour cancelled this Sunday for the parish feast.', 20, 7),
        ]
        for key, text, days_ago, lasts in notes:
            note = VenueNote.objects.create(venue=self.venues[key], text=text, expires_after=timedelta(days=lasts))
            VenueNote.objects.filter(pk=note.pk).update(created_at=self.now - timedelta(days=days_ago, hours=3))

    def seed_training(self):
        topics = {}
        self.modules = {}
        for key, (title, topic, icon, skills, expires, published, description, lessons, questions) in MODULES.items():
            if topic not in topics:
                topics[topic] = TrainingTopic.objects.create(name=topic)
            module = TrainingModule.objects.create(
                title=title, description=description, topic=topics[topic], icon=icon,
                expires_after_days=expires, published=published,
            )
            module.skills.set([self.skills[name] for name in skills])
            for order, (lesson_title, content) in enumerate(lessons, start=1):
                TrainingLesson.objects.create(training_module=module, title=lesson_title,
                                              content=content.strip(), order=order)
            if questions:
                quiz = Quiz.objects.create(training_module=module, title=f'{title}: check your understanding',
                                           percentage_to_pass=75 if len(questions) >= 4 else 66, order=len(lessons) + 1)
                for question in questions:
                    if len(question) == 2:
                        text, answer = question
                        q = QuizQuestion.objects.create(question_text=text, option_a='True', option_b='False',
                                                        correct_option='A' if answer else 'B', is_true_false=True)
                    else:
                        text, a, b, c, d, correct = question
                        q = QuizQuestion.objects.create(question_text=text, option_a=a, option_b=b,
                                                        option_c=c, option_d=d, correct_option=correct)
                    quiz.questions.add(q)
            self.modules[key] = module
        levels = {'orient': 0, 'cash': 1, 'interp': 2, 'firstaid': 2, 'child': 2, 'food': 3, 'trauma': 4, 'kiosk': 0}
        for key, level in levels.items():
            TrainingModule.objects.filter(pk=self.modules[key].pk).update(complexity_level=level)

        self.certificates = {}
        for key, (name, issuer, icon, expires, docs, description) in CERTIFICATES.items():
            self.certificates[key] = ExternalCertificate.objects.create(
                name=name, issuer=issuer, icon=icon, expires_after_days=expires, docs_list=docs, description=description)

    def seed_roles(self):
        self.roles = {}
        self.required = {}
        for key, (name, icon, description, permanent, venues, served, modules, preferred) in ROLES.items():
            role = Role.objects.create(name=name, icon=icon, description=description, permanent=permanent,
                                       regular_number_of_beneficiaries=served)
            role.venue.set([self.venues[v] for v in venues])
            role.preferred_skills.set([self.skills[s] for s in preferred])
            for module, mandatory in modules:
                RoleTrainingRequirement.objects.create(role=role, training_module=self.modules[module], mandatory=mandatory)
            self.roles[key] = role
            self.required[role.pk] = [m for m, mandatory in modules if mandatory]
        # The navigator role's weight is set by hand rather than from training length
        Role.objects.filter(pk=self.roles['navigator'].pk).update(points_weight=2.0)

    def seed_people(self):
        self.people = {}
        self.weights = {}
        self.left = {}
        for index, (username, (first, last, city, language, skills, joined, weight, _modules, extra)) in enumerate(PEOPLE.items()):
            joined_at = self.now - timedelta(days=joined, hours=random.randint(0, 10))
            if username == 'admin':
                user = User.objects.create_superuser('admin', 'admin@example.org', 'admin')
            else:
                user = User.objects.create_user(username, f'{username}@example.org', self.password)
            user.first_name, user.last_name = first, last
            user.date_joined = joined_at
            user.is_staff = extra.get('staff', False) or extra.get('superuser', False)
            if 'left' in extra:
                user.is_active = False
                self.left[user.pk] = self.now - timedelta(days=extra['left'])
            if joined > 2:
                user.last_login = self.now - timedelta(days=random.randint(0, min(joined, 20)), hours=random.randint(1, 20))
            user.save()

            profile = user.profile
            profile.location = city
            profile.language = language
            profile.bio = extra.get('bio', '')
            profile.admin_notes = extra.get('notes', '')
            profile.phone = f'604-555-{1100 + index * 7:04d}'
            if 'code' in extra:
                profile.id_code = extra['code']
            profile.save()
            profile.skills.set([self.skills[s] for s in skills])
            profile.permanent_roles.set([self.roles[r] for r in extra.get('perm', [])])
            self.people[username] = user
            self.weights[user.pk] = weight

    def complete_module(self, user, key, when):
        module = self.modules[key]
        module.started_by.add(user)
        for lesson in module.lessons.all():
            lesson.completed_by.add(user)
        for quiz in module.quizzes.all():
            quiz.completed_by.add(user)
        completion = TrainingModuleCompletion.objects.create(training_module=module, user=user)
        TrainingModuleCompletion.objects.filter(pk=completion.pk).update(completed_at=when)
        PointsEntry.objects.filter(user=user, source=PointsEntry.TRAINING, training_module=module).update(created_at=when)
        self.trained[(user.pk, key)] = when

    def seed_training_progress(self):
        for username, spec in PEOPLE.items():
            user = self.people[username]
            step = user.date_joined
            last_module = None
            for key, when in spec[7].items():
                module = self.modules[key]
                if isinstance(when, tuple):  # started, part way through
                    module.started_by.add(user)
                    for lesson in module.lessons.order_by('order')[:when[1]]:
                        lesson.completed_by.add(user)
                    last_module = module
                    continue
                if when is None:
                    step = step + timedelta(days=random.randint(2, 12), hours=random.randint(1, 8))
                    completed = min(step, self.now - timedelta(hours=6))
                else:
                    completed = self.now - timedelta(days=when, hours=random.randint(1, 8))
                self.complete_module(user, key, completed)
                last_module = last_module or module
            if last_module:
                user.profile.last_viewed_training_module = last_module
                user.profile.save(update_fields=['last_viewed_training_module'])

    # ------------------------------------------------------------ events

    def eligible(self, user, slot):
        if not self.weights.get(user.pk):
            return False
        if user.date_joined > slot.start_time - timedelta(days=1):
            return False
        if user.pk in self.left and slot.start_time > self.left[user.pk]:
            return False
        if not user.is_active and user.pk not in self.left:
            return False
        for module in self.required[slot.role_id]:
            done = self.trained.get((user.pk, module))
            if not done or done > slot.start_time:
                return False
        return not any(slot.start_time < end and slot.end_time > start for start, end in self.busy[user.pk])

    def pick(self, slot, count, exclude=()):
        candidates = [u for u in self.people.values() if u not in exclude and self.eligible(u, slot)]
        # Weighted sample without replacement: veterans turn up more often
        candidates.sort(key=lambda u: random.random() ** (1 / self.weights[u.pk]), reverse=True)
        return candidates[:count]

    def sign_up(self, slot, user, when=None, last_minute=False):
        slot.signups.add(user)
        if when is None:
            if last_minute:
                when = slot.start_time - timedelta(hours=random.randint(2, 20))
            else:
                when = slot.start_time - timedelta(days=random.randint(3, 30), hours=random.randint(0, 12))
        when = max(min(when, self.now - timedelta(minutes=random.randint(20, 300))), user.date_joined + timedelta(hours=2))
        log = SlotSignup.objects.create(slot=slot, user=user, last_minute=last_minute)
        SlotSignup.objects.filter(pk=log.pk).update(created_at=when)
        self.busy[user.pk].append((slot.start_time, slot.end_time))

    def make_shift(self, user, start, end=None, slot=None, role=None, clutched=False):
        shift = Shift(user=user, event_role_slot=slot, role=role, clutched=clutched)
        shift.save()
        Shift.objects.filter(pk=shift.pk).update(start_time=start)
        shift.start_time = start
        if end:
            shift.end_time = end
            shift.save()
        if not slot:
            self.busy[user.pk].append((start, end or self.now))
        return shift

    def create_event(self, title, start, end, place, description, categories=(), coordinators=('mykola.petrenko',),
                     attendees=0, featured=False, published=True, statement=None, report_to=None):
        kwargs = {}
        if place in self.venues:
            kwargs['venue'] = self.venues[place]
        else:
            kwargs['location'], kwargs['latitude'], kwargs['longitude'] = ONE_OFF[place]
        event = Event.objects.create(
            title=title, description=description, start_date=start, end_date=end, attendees=attendees,
            featured=featured, published=published, post_event_statement=statement, report_to_location=report_to, **kwargs)
        event.category.set([self.categories[c] for c in categories])
        event.coordinators.set([self.people[c] for c in coordinators])
        return event

    def add_slot(self, event, role, start, end, required, extra=0, public=True):
        return EventRoleSlot.objects.create(event=event, role=self.roles[role], start_time=start, end_time=end,
                                            required_qty=required, allowed_overstaffing_qty=extra, is_public=public)

    def staff_past(self, event, slots, walk_ins=1):
        """Sign people up for finished slots, record who turned up, and add a walk-in or two."""
        for slot, fill in slots:
            people = self.pick(slot, fill)
            for user in people:
                self.sign_up(slot, user, last_minute=random.random() < 0.1)
            for user in people:
                if random.random() < 0.08 and user.username != 'sofia.melnyk':
                    continue  # no-show
                start = slot.start_time + timedelta(minutes=random.randint(-12, 8))
                end = slot.end_time + timedelta(minutes=random.randint(-20, 15))
                if random.random() < 0.06:
                    end = slot.end_time - timedelta(minutes=random.randint(45, 90))  # left early
                self.make_shift(user, start, end, slot=slot)
                if user not in self.attended[event.pk]:
                    self.attended[event.pk].append(user)
        open_slots = [slot for slot, _fill in slots]
        for _ in range(walk_ins):
            slot = random.choice(open_slots)
            for user in self.pick(slot, 1):
                self.sign_up(slot, user, when=slot.start_time + timedelta(minutes=random.randint(5, 40)))
                start = slot.start_time + timedelta(minutes=random.randint(15, 60))
                self.make_shift(user, start, slot.end_time + timedelta(minutes=random.randint(-10, 10)), slot=slot, clutched=True)
                if user not in self.attended[event.pk]:  # may already be on another of the event's slots
                    self.attended[event.pk].append(user)

    def staff_future(self, slots):
        for slot, fill, *named in slots:
            people = [self.people[name] for name in (named[0] if named else [])]
            people += self.pick(slot, max(0, fill - len(people)), exclude=people)
            for user in people:
                self.sign_up(slot, user)

    def wrap_up(self, event, endorsements=3, feedback_share=0.55):
        team = self.attended[event.pk]
        for user in team:
            if random.random() < feedback_share:
                rating = random.choices([5, 4, 3, 2], weights=[55, 32, 10, 3])[0]
                fb = EventFeedback.objects.create(
                    event=event, user=user, rating=rating, enjoyed=random.choice(FEEDBACK_ENJOYED),
                    suggestions=random.choice(FEEDBACK_SUGGESTIONS) if rating < 5 or random.random() < 0.4 else '')
                EventFeedback.objects.filter(pk=fb.pk).update(created_at=event.end_date + timedelta(hours=random.randint(2, 60)))
        for _ in range(endorsements):
            if len(team) < 2:
                break
            endorser, endorsed = random.sample(team, 2)
            skills = list(endorsed.profile.skills.all()[:3])
            if not skills:
                continue
            given = Endorsement.give(endorser, endorsed, random.sample(skills, min(len(skills), random.randint(1, 2))),
                                     text=random.choice(ENDORSEMENT_TEXT), event=event)
            when = event.end_date + timedelta(hours=random.randint(3, 72))
            Endorsement.objects.filter(pk=given.pk).update(timestamp=when)
            PointsEntry.objects.filter(endorsement=given).update(created_at=when)

    def seed_events(self):
        d = self.day

        # ---------- past
        e = self.create_event(
            'Holodomor Remembrance Vigil', at(d(-317), '17:00'), at(d(-317), '19:00'), 'vag',
            'Join us on the fourth Saturday of November to remember the millions who died in the man-made famine of '
            '1932-33. Candle lighting, a short memorial service with clergy from across the Lower Mainland, and the '
            'reading of names. Bring a candle in a jar; we will have extras.',
            ['Remembrance', 'Cultural Heritage'], ['mykola.petrenko', 'admin'], attendees=300,
            statement='Around 300 people stood with us in the rain at the art gallery. Thank you to the volunteers '
                      'who handed out 400 candles and kept the sound system dry.')
        self.staff_past(e, [(self.add_slot(e, 'greeter', at(d(-317), '16:15'), at(d(-317), '19:00'), 3), 3),
                            (self.add_slot(e, 'setup', at(d(-317), '15:30'), at(d(-317), '19:30'), 2), 2)], walk_ins=0)
        self.wrap_up(e, endorsements=1)

        e = self.create_event(
            'Sviat Vechir: Ukrainian Christmas Eve Supper', at(d(-272), '16:00'), at(d(-272), '21:00'), 'hall',
            'The traditional Holy Supper of twelve meatless dishes: kutia, borshch with vushka, varenyky, holubtsi, '
            'pampushky and more. Carolling with the parish choir after the meal. All are welcome; tickets are '
            'pay-what-you-can at the door.',
            ['Faith & Community', 'Cultural Heritage'], ['mykola.petrenko', 'andriy.kovalenko'], attendees=220, featured=True,
            statement='220 guests, twelve dishes and not a single varenyk left over. Special thanks to the kitchen '
                      'crew, who started cooking at 9 a.m.', report_to='Kitchen entrance at the back of the hall')
        self.staff_past(e, [(self.add_slot(e, 'kitchen', at(d(-272), '09:00'), at(d(-272), '15:00'), 6, 2), 7),
                            (self.add_slot(e, 'kitchen', at(d(-272), '15:00'), at(d(-272), '21:30'), 6, 1), 6),
                            (self.add_slot(e, 'setup', at(d(-272), '13:00'), at(d(-272), '16:00'), 4), 4),
                            (self.add_slot(e, 'greeter', at(d(-272), '15:30'), at(d(-272), '18:00'), 2), 2),
                            (self.add_slot(e, 'kids', at(d(-272), '16:00'), at(d(-272), '19:00'), 2), 2)])
        self.wrap_up(e, endorsements=4)

        e = self.create_event(
            'Pysanka Writing Workshop', at(d(-198), '10:00'), at(d(-198), '14:00'), 'hall',
            'Learn to write pysanky, Ukrainian Easter eggs, with wax and kistka. Beginners welcome; all supplies '
            'provided. Children 8 and up with a parent.',
            ['Cultural Heritage', 'Youth & Family'], ['mykola.petrenko'], attendees=45,
            statement='45 artists, 140 finished eggs and only three dropped. See the photo album on the parish page.')
        self.staff_past(e, [(self.add_slot(e, 'greeter', at(d(-198), '09:30'), at(d(-198), '12:00'), 1), 1),
                            (self.add_slot(e, 'kids', at(d(-198), '10:00'), at(d(-198), '14:00'), 2), 2),
                            (self.add_slot(e, 'setup', at(d(-198), '08:30'), at(d(-198), '10:00'), 2), 2)], walk_ins=0)
        self.wrap_up(e, endorsements=1)

        e = self.create_event(
            'Easter Basket Blessing & Paska Sale', at(d(-177), '09:00'), at(d(-177), '15:00'), 'cathedral',
            'Bring your Easter basket for the traditional blessing on Holy Saturday at 10:00, 12:00 and 14:00. '
            'Paska, babka and hand-painted pysanky for sale in the hall; proceeds support the Welcome Centre.',
            ['Faith & Community', 'Fundraiser'], ['mykola.petrenko', 'admin'], attendees=400,
            statement='Over 400 baskets blessed and $6,850 raised for the Welcome Centre. Thank you!')
        self.staff_past(e, [(self.add_slot(e, 'cashier', at(d(-177), '08:45'), at(d(-177), '15:00'), 3), 3),
                            (self.add_slot(e, 'setup', at(d(-177), '07:00'), at(d(-177), '09:00'), 3), 3),
                            (self.add_slot(e, 'parking', at(d(-177), '09:30'), at(d(-177), '14:30'), 2), 2),
                            (self.add_slot(e, 'greeter', at(d(-177), '09:30'), at(d(-177), '14:30'), 2), 2)])
        self.wrap_up(e, endorsements=2)

        e = self.create_event(
            'Spring Perogy Bee', at(d(-149), '08:00'), at(d(-149), '13:00'), 'hall',
            'Our twice-yearly varenyky marathon. Roll, fill and pinch 6,000 perogies for the parish freezer and '
            'Saturday sales. No experience needed; Vira will teach you the pinch.',
            ['Fundraiser', 'Cultural Heritage'], ['sofia.melnyk', 'mykola.petrenko'], attendees=0,
            statement='6,240 perogies pinched by 14 volunteers. They are in the freezer and on sale Saturdays.')
        self.staff_past(e, [(self.add_slot(e, 'kitchen', at(d(-149), '08:00'), at(d(-149), '13:00'), 12, 4), 14),
                            (self.add_slot(e, 'cashier', at(d(-149), '11:00'), at(d(-149), '13:00'), 1), 1)])
        self.wrap_up(e, endorsements=3)

        e = self.create_event(
            'Canada Day Pancake Breakfast', at(d(-96), '08:00'), at(d(-96), '11:30'), 'park',
            'Pancakes, sausages and coffee by donation before the Canada Day parade. Proceeds go to the parish '
            'food pantry.',
            ['Food Security', 'Fundraiser'], ['mykola.petrenko'], attendees=600,
            statement='600 breakfasts served and $3,120 raised for the food pantry.')
        self.staff_past(e, [(self.add_slot(e, 'setup', at(d(-96), '06:00'), at(d(-96), '08:00'), 3), 3),
                            (self.add_slot(e, 'kitchen', at(d(-96), '07:30'), at(d(-96), '11:30'), 5, 1), 5),
                            (self.add_slot(e, 'cashier', at(d(-96), '08:00'), at(d(-96), '11:30'), 2), 2),
                            (self.add_slot(e, 'greeter', at(d(-96), '08:00'), at(d(-96), '11:00'), 2), 1)])
        self.wrap_up(e, endorsements=2)

        e = self.create_event(
            'Ukrainian Independence Day Festival', at(d(-44), '11:00'), at(d(-44), '19:00'), 'park',
            'A free family festival celebrating Ukrainian Independence Day: dance ensembles, a bandura concert, '
            'a kids\' zone with face painting and crafts, and a food tent with varenyky, kovbasa and holubtsi. '
            'Bring a blanket and wear your vyshyvanka!',
            ['Cultural Heritage', 'Youth & Family', 'Fundraiser'], ['mykola.petrenko', 'admin', 'sofia.melnyk'],
            attendees=1200, featured=True,
            statement='Our biggest festival yet: about 1,200 guests, eight dance groups and a food tent that sold '
                      'out by 5 p.m. Thank you to the 40 volunteers who made it happen.',
            report_to='Volunteer tent beside the bandshell')
        self.staff_past(e, [(self.add_slot(e, 'setup', at(d(-44), '06:30'), at(d(-44), '11:00'), 6), 6),
                            (self.add_slot(e, 'kitchen', at(d(-44), '10:30'), at(d(-44), '15:00'), 6, 2), 6),
                            (self.add_slot(e, 'kitchen', at(d(-44), '15:00'), at(d(-44), '19:00'), 5, 1), 5),
                            (self.add_slot(e, 'cashier', at(d(-44), '11:00'), at(d(-44), '19:00'), 3), 3),
                            (self.add_slot(e, 'kids', at(d(-44), '11:00'), at(d(-44), '16:00'), 3), 3),
                            (self.add_slot(e, 'firstaid', at(d(-44), '11:00'), at(d(-44), '19:00'), 2), 2),
                            (self.add_slot(e, 'photographer', at(d(-44), '11:00'), at(d(-44), '17:00'), 1), 1),
                            (self.add_slot(e, 'parking', at(d(-44), '10:00'), at(d(-44), '15:00'), 3), 2),
                            (self.add_slot(e, 'setup', at(d(-44), '19:00'), at(d(-44), '22:30'), 4), 4)], walk_ins=2)
        self.wrap_up(e, endorsements=6)

        e = self.create_event(
            'Back-to-School Backpack Drive', at(d(-37), '10:00'), at(d(-37), '14:00'), 'centre',
            'Free backpacks filled with school supplies for newcomer children starting school in September. '
            'Families registered in advance; interpreters on hand to help with school registration questions.',
            ['Newcomer Support', 'Youth & Family'], ['halyna.boyko'], attendees=140,
            statement='140 backpacks handed out to kids from kindergarten to grade 12. Thank you to London Drugs '
                      'for the donated supplies.')
        self.staff_past(e, [(self.add_slot(e, 'packer', at(d(-37), '09:00'), at(d(-37), '12:00'), 6), 6),
                            (self.add_slot(e, 'greeter', at(d(-37), '10:00'), at(d(-37), '14:00'), 2), 2),
                            (self.add_slot(e, 'interpreter', at(d(-37), '10:00'), at(d(-37), '14:00'), 2), 2)])
        self.wrap_up(e, endorsements=2)

        e = self.create_event(
            'Newcomer Job Fair', at(d(-18), '13:00'), at(d(-18), '18:00'), 'anvil',
            'Meet employers hiring in construction, health care, hospitality and trades, plus WorkBC advisers and '
            'credential-recognition experts. Interpreters available at every table.',
            ['Newcomer Support'], ['halyna.boyko', 'mykola.petrenko'], attendees=210,
            statement='210 job seekers met 24 employers. At least 11 people have told us they have interviews.')
        self.staff_past(e, [(self.add_slot(e, 'greeter', at(d(-18), '12:30'), at(d(-18), '18:00'), 3), 3),
                            (self.add_slot(e, 'interpreter', at(d(-18), '13:00'), at(d(-18), '18:00'), 4), 4),
                            (self.add_slot(e, 'setup', at(d(-18), '10:30'), at(d(-18), '13:00'), 2), 2)])
        self.wrap_up(e, endorsements=3)

        e = self.create_event(
            'Parish Fall Fair Brunch', at(d(-8), '10:30'), at(d(-8), '14:00'), 'hall',
            'Brunch after Divine Liturgy with a silent auction, a bake table and the parish choir. All proceeds '
            'go to the cathedral roof fund.',
            ['Faith & Community', 'Fundraiser'], ['mykola.petrenko', 'sofia.melnyk'], attendees=180,
            statement='180 brunches served and $4,300 raised for the roof. The silent auction alone brought in $1,900.')
        self.staff_past(e, [(self.add_slot(e, 'kitchen', at(d(-8), '08:30'), at(d(-8), '14:00'), 5, 1), 5),
                            (self.add_slot(e, 'cashier', at(d(-8), '10:30'), at(d(-8), '14:00'), 2), 2),
                            (self.add_slot(e, 'setup', at(d(-8), '08:00'), at(d(-8), '10:30'), 2), 2)])
        self.wrap_up(e, endorsements=3)

        # ---------- in progress: a ten-day food drive with one slot a day
        drive = self.create_event(
            'Thanksgiving Food Drive', at(d(-4), '11:00'), at(d(6), '17:00'), 'hall',
            'Help us fill 150 Thanksgiving hampers for families in New Westminster and Burnaby. Drop off '
            'non-perishables at the loading dock any day 11:00 to 17:00, or volunteer for a shift sorting and '
            'stocking donations.',
            ['Food Security'], ['mykola.petrenko', 'admin'], featured=True, report_to='Loading dock, lane behind the hall')
        for offset in range(-4, 7):
            slot = self.add_slot(drive, 'packer', at(d(offset), '11:00'), at(d(offset), '17:00'), 3, 1)
            if slot.end_time < self.now:
                self.staff_past(drive, [(slot, 3)], walk_ins=0)
            elif slot.start_time <= self.now:
                for user in self.pick(slot, 3, exclude=[self.people['taras.shevchuk']]):
                    self.sign_up(slot, user)
                    start = min(slot.start_time + timedelta(minutes=random.randint(-10, 15)), self.now - timedelta(minutes=10))
                    self.make_shift(user, start, slot=slot)
            else:
                self.staff_future([(slot, 3 if offset <= 2 else random.randint(0, 2))])

        # ---------- live right now: check-in monitor demo
        start = (self.now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
        live = self.create_event(
            'Thanksgiving Hamper Packing Night', start, start + timedelta(hours=5), 'centre',
            'Packing night for the Thanksgiving hampers: turkey vouchers, fresh vegetables, cranberry sauce and '
            'a handwritten card for every family. Pizza for volunteers at the break.',
            ['Food Security', 'Newcomer Support'], ['mykola.petrenko', 'halyna.boyko'],
            report_to='Main room on the ground floor')
        packers = self.add_slot(live, 'packer', start, start + timedelta(hours=5), 8, 2)
        greeters = self.add_slot(live, 'greeter', start, start + timedelta(hours=5), 2)
        taras = self.people['taras.shevchuk']  # already checked in at the front desk
        team = self.pick(packers, 7, exclude=[taras])
        for user in team:
            self.sign_up(packers, user)
        hosts = self.pick(greeters, 2, exclude=[taras])
        for user in hosts:
            self.sign_up(greeters, user)
        for user in team[:5] + hosts[:1]:
            slot = greeters if user in hosts else packers
            self.make_shift(user, start + timedelta(minutes=random.randint(-10, 25)), slot=slot)
        if len(team) > 5:
            self.make_shift(team[5], start + timedelta(minutes=5), self.now - timedelta(minutes=35), slot=packers)  # left early
        for user in self.pick(packers, 1, exclude=[taras]):  # walk-in at the kiosk
            self.sign_up(packers, user, when=start + timedelta(minutes=50))
            self.make_shift(user, start + timedelta(minutes=50), slot=packers, clutched=True)

        # ---------- upcoming
        evening = at(d(1), '18:30')
        e = self.create_event(
            'English Conversation Circle', evening, evening + timedelta(minutes=90), 'centre',
            'Relaxed English practice for newcomers, all levels. This week\'s theme: talking to your child\'s '
            'teacher. Tea and cookies provided.',
            ['Newcomer Support'], ['halyna.boyko'])
        self.staff_future([(self.add_slot(e, 'greeter', evening, evening + timedelta(minutes=90), 2), 1),
                           (self.add_slot(e, 'interpreter', evening, evening + timedelta(minutes=90), 2), 2)])

        e = self.create_event(
            'Newcomer Orientation & Settlement Clinic', at(d(1), '10:00'), at(d(1), '14:00'), 'centre',
            'For families who arrived in the last six months: MSP and SIN applications, school registration, '
            'opening a bank account and finding a family doctor. Settlement navigators and interpreters on site.',
            ['Newcomer Support'], ['halyna.boyko'])
        self.staff_future([(self.add_slot(e, 'interpreter', at(d(1), '09:45'), at(d(1), '14:00'), 3), 1),
                           (self.add_slot(e, 'greeter', at(d(1), '09:45'), at(d(1), '14:00'), 2), 0),
                           (self.add_slot(e, 'kids', at(d(1), '10:00'), at(d(1), '14:00'), 2), 1)])

        e = self.create_event(
            'Fall Perogy Bee', at(d(5), '08:00'), at(d(5), '13:00'), 'hall',
            'Our fall varenyky marathon. We are making 7,000 perogies for the Christmas bazaar. First-timers '
            'welcome; aprons, hairnets and coffee provided.',
            ['Fundraiser', 'Cultural Heritage'], ['sofia.melnyk', 'mykola.petrenko'], featured=True,
            report_to='Kitchen entrance at the back of the hall')
        self.staff_future([(self.add_slot(e, 'kitchen', at(d(5), '08:00'), at(d(5), '13:00'), 12, 4), 13, ['sofia.melnyk', 'vira.danylyuk']),
                           (self.add_slot(e, 'cashier', at(d(5), '11:00'), at(d(5), '13:00'), 1), 1),
                           (self.add_slot(e, 'setup', at(d(5), '06:30'), at(d(5), '08:00'), 2), 2)])

        e = self.create_event(
            'Thanksgiving Community Dinner', at(d(7), '16:00'), at(d(7), '20:00'), 'hall',
            'A free sit-down Thanksgiving dinner for newcomer families, seniors and anyone who would otherwise '
            'eat alone. Turkey with all the trimmings, plus varenyky of course.',
            ['Food Security', 'Faith & Community', 'Newcomer Support'], ['mykola.petrenko', 'andriy.kovalenko'])
        self.staff_future([(self.add_slot(e, 'kitchen', at(d(7), '12:00'), at(d(7), '20:00'), 6, 2), 4, ['andriy.kovalenko']),
                           (self.add_slot(e, 'greeter', at(d(7), '15:30'), at(d(7), '18:00'), 2), 2, ['emily.thompson']),
                           (self.add_slot(e, 'setup', at(d(7), '13:00'), at(d(7), '16:00'), 3), 1),
                           (self.add_slot(e, 'kids', at(d(7), '16:00'), at(d(7), '19:00'), 2), 0),
                           (self.add_slot(e, 'interpreter', at(d(7), '16:00'), at(d(7), '20:00'), 1), 1)])

        e = self.create_event(
            "Bishop's Pastoral Visit & Reception", at(d(19), '11:00'), at(d(19), '14:00'), 'cathedral',
            'Bishop Ken will celebrate Divine Liturgy and meet parishioners at a reception in the hall afterwards.',
            ['Faith & Community'], ['admin'])
        self.staff_future([(self.add_slot(e, 'greeter', at(d(19), '10:30'), at(d(19), '12:00'), 2), 1)])
        hospitality = self.add_slot(e, 'kitchen', at(d(19), '09:00'), at(d(19), '14:00'), 4, public=False)
        self.invite(hospitality, ['andriy.kovalenko', 'nadia.kravets'], accepted=['andriy.kovalenko'])

        e = self.create_event(
            'Christmas Bazaar', at(d(47), '09:00'), at(d(48), '15:00'), 'hall',
            'Two days of frozen perogies, holubtsi, kolach, embroidery, books and Christmas ornaments made by '
            'parishioners. Lunch counter open both days.',
            ['Fundraiser', 'Cultural Heritage'], ['mykola.petrenko', 'sofia.melnyk'], featured=True)
        self.staff_future([(self.add_slot(e, 'setup', at(d(46), '17:00'), at(d(46), '20:00'), 6), 3),
                           (self.add_slot(e, 'kitchen', at(d(47), '09:00'), at(d(47), '15:00'), 4, 1), 2),
                           (self.add_slot(e, 'kitchen', at(d(48), '10:00'), at(d(48), '15:00'), 4, 1), 1),
                           (self.add_slot(e, 'greeter', at(d(47), '09:00'), at(d(47), '15:00'), 2), 0)])
        sat_cash = self.add_slot(e, 'cashier', at(d(47), '08:45'), at(d(47), '15:00'), 2, public=False)
        sun_cash = self.add_slot(e, 'cashier', at(d(48), '09:45'), at(d(48), '15:00'), 2, public=False)
        self.invite(sat_cash, ['david.kim', 'liam.oconnor', 'chloe.martin'], accepted=['david.kim'])
        self.invite(sun_cash, ['james.wilson', 'nadia.kravets'], accepted=[])

        e = self.create_event(
            'Holodomor Remembrance Vigil', at(d(54), '17:00'), at(d(54), '19:00'), 'vag',
            'Join us on the fourth Saturday of November to remember the victims of the 1932-33 famine-genocide. '
            'Candle lighting, memorial prayers and the reading of names. Bring a candle in a jar.',
            ['Remembrance', 'Cultural Heritage'], ['mykola.petrenko', 'admin'])
        self.staff_future([(self.add_slot(e, 'greeter', at(d(54), '16:15'), at(d(54), '19:00'), 3), 1),
                           (self.add_slot(e, 'setup', at(d(54), '15:30'), at(d(54), '19:30'), 2), 1),
                           (self.add_slot(e, 'photographer', at(d(54), '16:30'), at(d(54), '19:00'), 1), 1)])

        e = self.create_event(
            "St. Nicholas Day Children's Concert", at(d(62), '13:00'), at(d(62), '16:00'), 'cathedral',
            'The Sunday school presents carols and a nativity play, followed by a visit from St. Nicholas with '
            'gifts for every child. Register children in advance so St. Nicholas has a gift for everyone.',
            ['Youth & Family', 'Faith & Community'], ['iryna.tkachenko', 'mykola.petrenko'])
        self.staff_future([(self.add_slot(e, 'kids', at(d(62), '12:30'), at(d(62), '16:00'), 4), 2, ['iryna.tkachenko']),
                           (self.add_slot(e, 'greeter', at(d(62), '12:30'), at(d(62), '14:00'), 2), 1),
                           (self.add_slot(e, 'firstaid', at(d(62), '12:30'), at(d(62), '16:00'), 1), 1),
                           (self.add_slot(e, 'photographer', at(d(62), '13:00'), at(d(62), '16:00'), 1), 0)])

        e = self.create_event(
            'Sviat Vechir 2027: Christmas Eve Supper', at(d(93), '16:00'), at(d(93), '21:00'), 'hall',
            'The Holy Supper of twelve meatless dishes, carolling with the choir and a visit from the Vertep. '
            'Kitchen volunteers start at 9 a.m.',
            ['Faith & Community', 'Cultural Heritage'], ['andriy.kovalenko', 'mykola.petrenko'])
        self.staff_future([(self.add_slot(e, 'kitchen', at(d(93), '09:00'), at(d(93), '15:00'), 6, 2), 2, ['andriy.kovalenko']),
                           (self.add_slot(e, 'kitchen', at(d(93), '15:00'), at(d(93), '21:30'), 6, 1), 0),
                           (self.add_slot(e, 'setup', at(d(93), '13:00'), at(d(93), '16:00'), 4), 0),
                           (self.add_slot(e, 'greeter', at(d(93), '15:30'), at(d(93), '18:00'), 2), 0)])

        e = self.create_event(
            "Malanka New Year's Gala", at(d(103), '18:00'), at(d(104), '00:30'), 'quay',
            'Ukrainian New Year\'s Eve (Old Calendar) gala with dinner, a live band and the traditional Malanka '
            'procession. Draft: ticket prices and band still to be confirmed.',
            ['Cultural Heritage', 'Fundraiser'], ['mykola.petrenko'], published=False)
        self.add_slot(e, 'greeter', at(d(103), '17:30'), at(d(103), '20:00'), 3)
        self.add_slot(e, 'setup', at(d(103), '14:00'), at(d(103), '18:00'), 6)
        self.add_slot(e, 'setup', at(d(103), '23:30'), at(d(104), '01:30'), 4)

    def invite(self, slot, usernames, accepted):
        for username in usernames:
            user = self.people[username]
            invite = EventSlotInvite.objects.create(event_role_slot=slot, user=user, accepted=username in accepted)
            EventSlotInvite.objects.filter(pk=invite.pk).update(sent_at=self.now - timedelta(days=random.randint(2, 9)))
            if username in accepted:
                self.sign_up(slot, user, when=self.now - timedelta(days=1, hours=random.randint(1, 20)))

    def seed_permanent_shifts(self):
        """Ten weeks of regular shifts for permanent roles, plus people checked in at the kiosk right now."""
        schedule = [
            ('taras.shevchuk', 'frontdesk', 'centre', [0, 2, 4], '09:30', '13:30'),
            ('tetiana.pavlenko', 'frontdesk', 'centre', [5], '10:00', '14:00'),
            ('halyna.boyko', 'navigator', 'centre', [1, 3], '10:00', '16:00'),
            ('sofia.melnyk', 'tourguide', 'cathedral', [6], '12:45', '13:45'),
            ('roman.savchuk', 'pantry', 'hall', [2], '17:00', '20:00'),
            ('nadia.kravets', 'pantry', 'hall', [2], '17:00', '20:00'),
            ('petro.zaitsev', 'pantry', 'hall', [2], '17:00', '20:00'),
        ]
        for username, role, _venue, weekdays, opens, closes in schedule:
            user = self.people[username]
            joined = max(user.date_joined, self.now - timedelta(days=70))
            for offset in range(-70, 0):
                day = self.day(offset)
                if day.weekday() not in weekdays or random.random() < 0.12:  # holidays and sick days
                    continue
                start = at(day, opens) + timedelta(minutes=random.randint(-10, 10))
                end = at(day, closes) + timedelta(minutes=random.randint(-15, 20))
                if start < joined or any(start < e and end > s for s, e in self.busy[user.pk]):
                    continue
                self.make_shift(user, start, end, role=self.roles[role])

        # Checked in at the Welcome Centre kiosk this morning, still working
        taras = self.people['taras.shevchuk']
        open_shift = self.make_shift(taras, self.now - timedelta(hours=1, minutes=35), role=self.roles['frontdesk'])
        ShiftHeartbeat(shift=open_shift, role=self.roles['frontdesk']).save()

    # ------------------------------------------------------------ certificates and the rest

    def certificate_scan(self, user, certificate, issued):
        """A plain image standing in for an uploaded scan."""
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new('RGB', (1000, 640), (250, 248, 242))
        draw = ImageDraw.Draw(image)
        draw.rectangle([24, 24, 976, 616], outline=(150, 40, 40), width=6)
        try:
            big, small = ImageFont.load_default(size=44), ImageFont.load_default(size=26)
        except TypeError:
            big = small = ImageFont.load_default()
        draw.text((70, 70), certificate.issuer.upper(), fill=(150, 40, 40), font=small)
        draw.text((70, 150), certificate.name, fill=(30, 30, 30), font=big)
        draw.text((70, 260), 'This certifies that', fill=(90, 90, 90), font=small)
        draw.text((70, 310), user.get_full_name(), fill=(30, 30, 30), font=big)
        draw.text((70, 430), f'Issued {issued:%B %d, %Y}', fill=(90, 90, 90), font=small)
        draw.text((70, 480), f'Certificate no. {random.randint(10**7, 10**8 - 1)}', fill=(90, 90, 90), font=small)
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        return ContentFile(buffer.getvalue(), name=f'demo_{user.username}_{certificate.pk}.png')

    def seed_certificates(self):
        reviewer = self.people['admin']
        # (username, certificate, issued days ago, status, note)
        entries = [
            ('sofia.melnyk', 'foodsafe', 420, 'verified', ''),
            ('sofia.melnyk', 'firstaid', 170, 'verified', ''),
            ('andriy.kovalenko', 'foodsafe', 600, 'verified', ''),
            ('maria.santos', 'firstaid', 95, 'verified', 'RN, also holds BLS for health care providers.'),
            ('maria.santos', 'crc', 280, 'verified', ''),
            ('michael.brown', 'firstaid', 65, 'verified', 'Paramedic; certificate exceeds requirement.'),
            ('iryna.tkachenko', 'firstaid', 1150, 'verified', 'Expired. Reminded her to renew before the St. Nicholas concert.'),
            ('iryna.tkachenko', 'crc', 500, 'verified', ''),
            ('kateryna.moroz', 'crc', 330, 'verified', ''),
            ('kateryna.moroz', 'foodsafe', 6, 'pending', ''),
            ('oksana.lysenko', 'crc', 190, 'verified', ''),
            ('lesia.fedorenko', 'crc', 250, 'verified', ''),
            ('anastasia.romanyuk', 'crc', 5, 'pending', ''),
            ('emily.thompson', 'crc', 3, 'pending', ''),
            ('daniel.chen', 'firstaid', 2, 'pending', ''),
            ('ivan.bondarenko', 'foodsafe', 30, 'rejected', 'Photo is blurry; the name and certificate number are not readable. Asked him to upload a clearer scan.'),
            ('roman.savchuk', 'licence', 650, 'verified', ''),
            ('petro.zaitsev', 'licence', 360, 'verified', ''),
            ('mykola.petrenko', 'firstaid', 160, 'verified', ''),
        ]
        for username, cert_key, days_ago, status, note in entries:
            user, certificate = self.people[username], self.certificates[cert_key]
            issued = self.day(-days_ago - random.randint(1, 10))
            submitted = self.now - timedelta(days=days_ago, hours=random.randint(1, 8))
            expires = (at(issued, '00:00') + timedelta(days=certificate.expires_after_days)) if certificate.expires_after_days else None
            record = UserCertification.objects.create(
                user=user, certificate=certificate, issue_date=issued, expiration_date=expires,
                verified=status == 'verified', rejected=status == 'rejected', admin_notes=note,
                reviewed_by=reviewer if status != 'pending' else None,
                reviewed_at=submitted + timedelta(days=random.randint(1, 3)) if status != 'pending' else None)
            UserCertification.objects.filter(pk=record.pk).update(issued_at=submitted)
            for _doc in certificate.required_docs:
                upload = UserCertificationFile.objects.create(certification=record, file=self.certificate_scan(user, certificate, issued))
                UserCertificationFile.objects.filter(pk=upload.pk).update(uploaded_at=submitted)

    def seed_staff_notes(self):
        admin = self.people['admin']
        for username, rating, note in ADMIN_NOTES:
            feedback = AdminFeedback.objects.create(volunteer=self.people[username], author=admin, rating=rating, note=note)
            AdminFeedback.objects.filter(pk=feedback.pk).update(timestamp=self.now - timedelta(days=random.randint(5, 120)))

        adjustments = [
            ('sofia.melnyk', 60, 'Organized the volunteer schedule for the Independence Day Festival', 40),
            ('roman.savchuk', 30, 'Picked up donated tables from Burnaby with the parish van (not logged as a shift)', 75),
            ('liam.oconnor', -15, 'Duplicate check-in at the Fall Fair Brunch removed', 7),
            ('yuriy.hnatyshyn', 25, 'Edited and published the festival photo album', 38),
        ]
        for username, amount, reason, days_ago in adjustments:
            entry = points.adjust(self.people[username], amount, reason, by=admin)
            PointsEntry.objects.filter(pk=entry.pk).update(created_at=self.now - timedelta(days=days_ago))

        # An endorsement from staff, not tied to an event (no points, just recognition)
        given = Endorsement.give(self.people['halyna.boyko'], self.people['oksana.lysenko'],
                                 [self.skills['Interpreting'], self.skills['Settlement Support']],
                                 text='Oksana has interpreted at more than twenty navigator appointments this year. '
                                      'Families trust her completely.')
        Endorsement.objects.filter(pk=given.pk).update(timestamp=self.now - timedelta(days=12))

    def notify(self, user, message, link, days_ago, read=None):
        n = Notification.objects.create(user=user, message=message[:255], link=link,
                                        read=read if read is not None else days_ago > 3)
        Notification.objects.filter(pk=n.pk).update(timestamp=self.now - timedelta(days=days_ago, hours=random.randint(0, 10)))

    def seed_notifications(self):
        upcoming = {}
        for log in SlotSignup.objects.filter(slot__start_time__gt=self.now).select_related('slot__event', 'slot__role').order_by('slot__start_time'):
            upcoming.setdefault(log.user_id, log.slot)
        for user in self.people.values():
            slot = upcoming.get(user.pk)
            if slot:
                starts = timezone.localtime(slot.start_time)
                self.notify(user, f'Reminder: you\'re signed up as {slot.role.name} at {slot.event.title} on '
                                  f'{starts:%a %b} {starts.day}.',
                            reverse('opportunity_detail', args=[slot.event_id]), days_ago=1, read=False)
            last = Shift.objects.filter(user=user, event_role_slot__isnull=False, end_time__isnull=False).select_related('event_role_slot__event').order_by('-end_time').first()
            if last:
                event = last.event_role_slot.event
                self.notify(user, f'Thank you for volunteering at {event.title}! Your hours have been added to your impact record.',
                            reverse('impact_record', args=[user.username]), days_ago=max(0, (self.now - last.end_time).days))
            for endorsement in Endorsement.objects.filter(endorsed=user).select_related('endorser').order_by('-timestamp')[:2]:
                self.notify(user, f'{endorsement.endorser.get_full_name()} endorsed you for '
                                  f'{", ".join(s.name for s in endorsement.skills.all())}.',
                            reverse('profile_view', args=[user.username]), days_ago=(self.now - endorsement.timestamp).days)
            for record in UserCertification.objects.filter(user=user).exclude(reviewed_at=None):
                verb = 'was approved' if record.verified else 'needs another look: please upload a clearer copy'
                self.notify(user, f'Your {record.certificate.name} certificate {verb}.',
                            reverse('verify_certificates'), days_ago=(self.now - record.reviewed_at).days)

        news = [
            ('Fall Perogy Bee', 'New event: Fall Perogy Bee needs 12 kitchen volunteers. First-timers welcome!', 9),
            ('Thanksgiving Community Dinner', 'Volunteers needed for the Thanksgiving Community Dinner, especially kids\' zone helpers.', 4),
        ]
        for title, message, days_ago in news:
            event = Event.objects.get(title=title)
            for user in random.sample([u for u in self.people.values() if u.is_active], 12):
                self.notify(user, message, reverse('opportunity_detail', args=[event.pk]), days_ago=days_ago)

        trauma = reverse('module_overview', args=[self.modules['trauma'].pk])
        self.notify(self.people['olha.marchenko'], 'You\'re halfway through Trauma-Informed Support for Newcomers. Finish it to join the front desk rota.', trauma, 2, read=False)
        food = reverse('module_overview', args=[self.modules['food'].pk])
        self.notify(self.people['vira.danylyuk'], 'Your Food Safety Basics training has expired. Renew it before the Fall Perogy Bee.', food, 6, read=False)
        self.notify(self.people['iryna.tkachenko'], 'Your Standard First Aid & CPR-C certificate has expired. Upload your renewed certificate.', reverse('verify_certificates'), 10)
        self.notify(self.people['daniel.chen'], 'Welcome to the parish volunteer team! Start with Volunteer Orientation to unlock your first shift.',
                    reverse('module_overview', args=[self.modules['orient'].pk]), 4, read=True)
        self.notify(self.people['priya.sharma'], 'Welcome to the parish volunteer team! Start with Volunteer Orientation to unlock your first shift.',
                    reverse('module_overview', args=[self.modules['orient'].pk]), 1, read=False)
        admin = self.people['admin']
        pending = UserCertification.objects.filter(verified=False, rejected=False).count()
        self.notify(admin, f'{pending} certificates are waiting for review.', reverse('console_verifications'), 1, read=False)

    def seed_api_keys(self):
        APIKey.objects.create(name='Welcome Centre front desk tablet')
        APIKey.objects.create(name='Parish hall kiosk iPad', expires_at=self.now + timedelta(days=300))
        old = APIKey.objects.create(name='2025 festival tablet (retired)', expires_at=self.now - timedelta(days=30))
        APIKey.objects.filter(pk=old.pk).update(created_at=self.now - timedelta(days=420))

    # ------------------------------------------------------------ Bell Tower tasks

    def seed_tasks(self, url, token):
        """Connect to Bell Tower with a staff token, link every demo person to an account, and
        give each event that hasn't ended a planning list, its role lists and their tasks."""
        self.stdout.write('Bell Tower task lists...')
        try:
            base_url = belltower.normalize_url(url)
            endpoints = belltower.discover(base_url)
            me = belltower.api('GET', 'me/', cfg={'url': base_url, 'api_key': token, 'username': '', 'endpoints': endpoints})
        except (ValueError, belltower.BellTowerError) as exc:
            self.stderr.write(self.style.ERROR(f'Skipping task lists: {exc}'))
            return
        belltower.save_connection(base_url, endpoints, token, me['username'])
        self.belltower_user = me['username']
        self.task_count = 0
        if me['is_staff']:
            self.stdout.write('  Linking people to Bell Tower accounts...')
            belltower.link_all_users()
            add_people = mock.patch.object(belltower_sync, '_add_person', belltower_sync._add_person)
        else:
            self.stderr.write(self.style.WARNING(
                f'  {me["username"]} is not Bell Tower staff, so people are not linked or added to lists.'))
            add_people = mock.patch.object(belltower_sync, '_add_person', lambda task_list, user: None)
        role_keys = {role.pk: key for key, role in self.roles.items()}
        events = list(Event.objects.filter(end_date__gte=self.now).order_by('start_date'))
        try:
            with add_people:
                for event in events:
                    self.seed_event_tasks(event, role_keys)
        except belltower.BellTowerError as exc:
            self.stderr.write(self.style.ERROR(f'  Stopped seeding task lists: {exc}'))
            return
        self.stdout.write(f'  {EventTaskList.objects.count()} lists and {self.task_count} tasks for {len(events)} events.')

    def add_task(self, task_list, title, details='', due=None, done=False, assignee=None):
        task = belltower.create_task(task_list.belltower_id, title, details, expires_at=due, assignee=assignee)
        if done:
            belltower.update_task(task['id'], completed=True)
        self.task_count += 1

    def seed_event_tasks(self, event, role_keys):
        started = event.start_date <= self.now

        # Planning: deadlines count back from the start; most of the ones already past are done.
        remote = belltower.create_list(belltower_sync.remote_name(event, 'Planning'))
        planning = EventTaskList.objects.create(event=event, kind=EventTaskList.PLANNING, name='Planning',
                                                belltower_url=belltower.config()['url'], belltower_id=remote['id'])
        start_day = timezone.localdate(event.start_date)
        for title, details, days_before in PLANNING_TASKS + EVENT_PLANNING_TASKS.get(event.title, []):
            due = at(start_day - timedelta(days=days_before), '09:00')
            self.add_task(planning, title, details, due, done=due < self.now and random.random() < 0.85)

        # A list per role with the signed-up volunteers on it; every other task is assigned.
        belltower_sync.sync_event(event)
        for task_list in event.task_lists.filter(roles__isnull=False).prefetch_related('roles').distinct():
            key = role_keys.get(task_list.roles.all()[0].pk)
            members = [m['username'] for m in belltower.get_list(task_list.belltower_id)['members']
                       if m['username'] != self.belltower_user]
            for index, (title, details, offset) in enumerate(ROLE_TASKS.get(key, GENERIC_ROLE_TASKS)):
                if offset == 'end':
                    due = event.end_date
                else:
                    due = event.start_date + timedelta(minutes=offset) if offset is not None else None
                assignee = members[index // 2 % len(members)] if members and index % 2 == 0 else None
                self.add_task(task_list, title, details, due, done=started and due is not None and due < self.now,
                              assignee=assignee)
            if started:
                for title, details, minutes in LIVE_URGENT_TASKS.get(key, []):
                    self.add_task(task_list, title, details, self.now + timedelta(minutes=minutes),
                                  assignee=members[0] if members else None)

        # Events under way also have a list of their own for closing up, shared with the coordinators.
        if started:
            name = 'Closing checklist'
            remote = belltower.create_list(belltower_sync.remote_name(event, name))
            closing = EventTaskList.objects.create(event=event, kind=EventTaskList.EVENT_DAY, name=name,
                                                   belltower_url=belltower.config()['url'], belltower_id=remote['id'])
            for title, details in CLOSING_CHECKLIST:
                self.add_task(closing, title, details, event.end_date)
            for coordinator in event.coordinators.all():
                username = belltower.linked_username(coordinator, create=False)
                if username:
                    belltower.add_member(closing.belltower_id, username=username, admin=True)

    # ------------------------------------------------------------ report

    def summary(self):
        self.stdout.write(self.style.SUCCESS('Demo data ready.'))
        rows = [
            ('admin / admin', 'Olena Kovalchuk, superuser: console, approvals, everything'),
            ('mykola.petrenko', 'Events coordinator (staff, not superuser)'),
            ('sofia.melnyk', 'Veteran volunteer: top level, endorsements, many shifts'),
            ('taras.shevchuk', 'Permanent front desk, checked in at the Welcome Centre kiosk right now'),
            ('emily.thompson', 'New-ish: Food Safety half done, signed up for Thanksgiving dinner'),
            ('olha.marchenko', 'Trauma-informed module in progress'),
            ('daniel.chen', 'Brand new: orientation started, cannot sign up for most roles yet'),
            ('priya.sharma', 'Signed up yesterday, nothing done yet'),
            ('vira.danylyuk / iryna.tkachenko', 'Expired training and certificates'),
            ('kevin.oneill', 'Deactivated account'),
        ]
        self.stdout.write(f'\nAll volunteers use the password "{self.password}".')
        for login, what in rows:
            self.stdout.write(f'  {login:<34} {what}')
        codes = ', '.join(f'{u.username} {u.profile.id_code}' for u in User.objects.filter(
            username__in=['taras.shevchuk', 'emily.thompson', 'daniel.chen', 'sofia.melnyk']).select_related('profile'))
        self.stdout.write(f'\nKiosk codes: {codes}')
        live = Event.objects.filter(start_date__lte=self.now, end_date__gte=self.now).values_list('pk', 'title')
        self.stdout.write('Live now: ' + '; '.join(f'#{pk} {title}' for pk, title in live))
