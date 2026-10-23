"""First-run onboarding: the admin guide training module.

The first person to open a fresh install creates the superuser on the setup page
(base.views.first_run_setup); they're then sent to this guide. It's a normal training module
(lessons and a sample quiz) marked staff_only, so volunteers never see it.
``python manage.py create_admin_guide`` adds it to an existing install.
"""
from django.conf import settings
from django.db import transaction
from django.utils import translation

from education.models import Quiz, QuizQuestion, TrainingLesson, TrainingModule

ADMIN_GUIDE_KEY = 'admin_guide'

TITLE = 'Town Hall Admin Guide'

DESCRIPTION = (
    'Everything you need to run your organization on Town Hall: setting up your identity and venues, '
    'building roles and training, planning and staffing events, running check-in on the day, '
    'recognizing volunteers afterwards, and connecting email, task lists and AI apps. '
    'Only staff can see this module.'
)

LESSONS = [
    ('Welcome to Town Hall', """
Welcome! You are the first administrator of this Town Hall site. This guide walks through every part
of the app, in the order you will most likely need it. It is written as a regular training module, so
it also shows you what volunteers experience when they take training. Mark each lesson complete as you
go; you can come back any time from **Training > My Training**.

## What Town Hall does

Town Hall helps an organization run volunteer events from start to finish:

1. **Events** are the things you organize: a festival, a food drive, a Sunday service.
2. Each event has **role slots**: a role (for example *Greeter*), a time window, and how many people
   you need. Volunteers sign up for slots.
3. **Roles** are reusable templates kept in the **Role Directory**. A role can require **training
   modules**, and a volunteer can only sign up once they have finished the mandatory ones.
4. When volunteers show up, they check in at a **kiosk**. The time they work is recorded as a
   **shift**.
5. Shifts earn **impact points**, and impact points move volunteers up through **levels**.

## The two sides of the app

- **The public site** is what volunteers use: the home page, **Opportunities** (event search),
  **Training**, **My Events**, their profile and impact record.
- **The Admin Portal** (also called the console) is where staff manage everything. Open it from the
  account menu (your avatar, top right) with **Admin portal**. Every page in it is staff-only.

The console sidebar has these sections, each covered in its own lesson:

- **Admin Home**: today's numbers, upcoming events and unfilled roles.
- **Event Planner**: create and run events.
- **Role Directory**: reusable role templates.
- **Volunteer Pool**: everyone with an account.
- **Venues**: the places you hold events.
- **Training Modules** and **Certificates**: what volunteers learn and prove.
- **Communication**: which emails the app sends.
- **Organization**: your name, colors, home page, levels, skills, points, languages and integrations.
- **Kiosk Mode**: turns a device into a check-in station.

## A good order to set things up

1. Fill in your **Organization** identity (name, logo, colors).
2. Choose your languages and time zone under **Organization > Region**.
3. Add your **Venues**.
4. Write your **Training Modules**.
5. Create **Roles** and attach the training they need.
6. Create your first **Event**, add role slots, and publish it.
7. Optionally connect email (listmonk), task lists (Bell Tower) and the Claude connector.

The rest of this guide follows that order.
"""),

    ('Your organization: identity, home page and region', """
Everything about how your organization presents itself lives under **Organization** in the console.
The page has tabs along the top: **Identity**, **Home Page**, **Levels**, **Skills**, **Points**,
**Region** and **Backend**.

## Identity

- **Company name** and **Logo** appear in the header, emails and certificates.
- **Contact email** and **Careers page URL** show in the site footer.
- **Terms of service** is the text volunteers agree to.
- **Max skills per user** limits how many skills a volunteer can list on their profile.
- **Kiosk idle timeout** is how many seconds a kiosk waits before signing an idle volunteer out.
- **Brand colors**: pick a primary and an accent color, the backgrounds and text colors, and a
  separate set for **Dark Mode**. Changes preview live on the page before you save.

The **Setup Progress** card on this page shows what is still missing.

## Home Page

The banner at the top of the public home page: headline, intro text, an image and up to two buttons.
Button links can be a full address (`https://...`) or a page on this site such as `/en/opportunities/`.
Events marked **Highlight on the home page** in the event editor are featured there too.

## Skills

**Organization > Skills** is your organization's skill list. Volunteers pick from it on their
profile, roles list **Preferred Skills** from it, training modules say which skills they teach, and
teammates endorse each other for them. Each skill has a name in every language you use; fill in the
first one and use **Auto-translate** for the rest. A badge marks skills with missing translations.
While you have fewer than five skills, **Fill with default skills** adds a ready-made, translated set
of common volunteering skills. Deleting a skill removes it from everyone who had it.

## Region

- **Languages**: tick every language your volunteers use. The first one (the **Default language**)
  is the only one you must fill in; any missing translation falls back to it.
- **Time zone**: event times are entered and shown in this zone.

Language and time zone changes apply when the app restarts. The page shows a **Restart Now** button
when a restart is needed; it waits until the app is back up.

## Event categories

Categories (such as *Food Security* or *Youth & Family*) let volunteers filter events. You create them
as you go: the event editor's **Categories** panel has an **Add category** box under the existing
ones (see the Creating an event lesson).

Levels, Points and Backend have their own lessons later in this guide.
"""),

    ('Venues', """
A **venue** is a place you use more than once: a hall, a church, a community centre. Describing it
once means events and permanent staff positions can simply pick it instead of typing an address.

## Adding a venue

Open **Venues > Add Venue** and fill in:

- **Name**, **Description** and a **Photo**: shown on the public venue page.
- **Address**: Town Hall looks it up on the map so events show up in distance searches. If it
  can't be found, a warning tells you to add the city and postal code.
- **Phone number** and **Capacity**.
- **Features & Amenities**: tick what the venue offers (parking, accessible entrance, kitchen...).
  You can add your own features from the editor.
- **Operating Hours**: opening and closing times per day. Leave both times empty on days the venue is
  closed. **Staff access** lets permanent staff check in at the kiosk outside those hours.

## Notices

**Notices** are short-lived notes such as *"The elevator is out of service until Thursday"*. Choose
how many days each one shows. They appear on the public venue page and on that venue's kiosk.

## Where venues are used

- In the event editor, pick a venue instead of typing an address. If you choose
  **Somewhere else (type an address)**, the event uses a one-off address instead.
- Permanent roles can be limited to certain venues (see the Roles lesson).
- Each venue has its own kiosk for permanent staff (**Open venue kiosk** on the venue page).

If you delete a venue, its events keep the name and address as plain text, so nothing is lost.
"""),

    ('Roles and the Role Directory', """
A **role** is a reusable job description: *Greeter*, *Kitchen Helper*, *First Aid Attendant*. You
create it once in the **Role Directory** and then add it to as many events as you like.

## Creating a role

Open **Role Directory > Create New Role Template**:

- **Name**, **Description** and an **Icon** (a Google Material Symbols name such as `restaurant`).
- **Required Training Modules**: search for a module and add it. Mark it **mandatory** if volunteers
  must finish it before they can sign up; leave it optional to simply recommend it.
- **Preferred Skills**: skills that make someone a good fit. They are not required, but they help
  match volunteers when you send invitations.
- **Beneficiaries served per hour**: how many people one volunteer in this role typically helps. It
  feeds the impact points "reach" bonus.
- **Points weight**: multiplies the impact points for every hour in this role (for example 1.5 for a
  demanding role). Leave it empty and Town Hall sets it from how long the role's training is.

## Event roles and permanent positions

Most roles are **event roles**: people sign up for them as part of an event.

Tick **Ongoing position not tied to events** to make a **permanent** role instead, such as a
weekly front-desk shift. Permanent roles:

- are assigned to specific people from their page in the **Volunteer Pool** (the *Roles* section);
- can be limited to certain **Venues** (leave them all unticked to allow any venue);
- are checked in at the venue kiosk, and only assigned people can start a shift in them.

## Good to know

- The **Role Directory** has filters for event roles, permanent positions, difficulty and
  **Roles Without Training**.
- A role's difficulty is worked out from the training it requires.
- The built-in **Area lead** role is used by the chain of command. It is managed for you and is hidden
  from the directory.
"""),

    ('Training modules and certificates', """
Training makes sure volunteers are ready for a role. A **training module** (like this one) is a
sequence of **lessons** and **quizzes**. When a volunteer finishes every item they get a certificate
they can print or share, and they become eligible for roles that require the module.

## Building a module

Open **Training Modules > New Module**:

- **Title**, **Description**, **Icon**, **Topic** (type a new topic to create one) and a **Cover photo**.
- **Published**: only published modules appear in the training directory.
- **Staff only**: hides the module from volunteers. This guide is staff only.
- **Certificate valid for (days)**: leave empty if completion never expires. When it expires the
  volunteer must take it again to stay eligible for roles that require it.
- **Complexity (0-4)**: a rough difficulty rating shown to volunteers.

Save the overview first; then use the **Module Structure** panel to **Add Lesson** and **Add Quiz**.
The arrows next to each item change the order.

## Lessons

A lesson has a title and **content** written in Markdown: `## Headings`, `**bold**`, lists, links and tables.
Switch between **Write** and **Preview** to check how it looks. Paste a YouTube link into
**Add Lesson Video** to embed a video; its length is detected automatically. **Save & Add Another**
speeds up writing a series of lessons.

## Quizzes

A quiz has a title and a **Passing score (%)**. Add questions with up to four options (A-D) and pick
the **Correct answer**, or tick **True / False** for a simple true/false question. Volunteers can retake
a quiz until they pass.

## Attaching training to roles

Modules do nothing on their own until a role requires them. Add them in the role's
**Required Training Modules** section (previous lesson). The module editor shows how many roles
require it, how many people started it, and how many certificates were issued.

## External certificates

Some roles need credentials from outside, such as a first aid card or a background check. Under
**Certificates**, create a certificate template with its **name**, **issuer**, **description**,
**badge icon**, how long it stays valid, and the list of **Required Supporting Documents**.

Volunteers upload their documents from **Training > Verify certificates**. Their submissions land in
**Certificates > Verifications**, where you open each one, check the files, set the **Issue Date** and
**Expiry Date**, keep private **Admin Notes**, and **Approve** or **Reject** it. A rejected volunteer
can upload new documents and go back into the queue.
"""),

    ('Creating an event', """
Events are created and run from the **Event Planner** in the console. Click **Create New Event**
(it is also on **Admin Home**).

## The event editor

- **Title** and **Description**: what the event is and why it matters.
- **Venue**: pick one of your venues, or **Somewhere else (type an address)** and enter an address.
- **Check-in point**: where volunteers should report, such as *"Loading dock, lane behind the hall"*.
- **Start date** and **End date**.
- **Event header image**: recommended size 1200 x 400 px.
- **Categories**: tick the categories that fit. To make a new one, type its name in the
  **Add category** box below them; it's created when you save and ticked for this event. Categories
  you create are available to every event from then on.
- **General coordinators**: the staff members in charge. They sit at the top of the chain of
  command and are the only ones who assign area and shift leads.
- **Expected attendees**: how many people the event will serve. It feeds the impact points reach
  bonus, so volunteers at a big event earn a bit more.
- **Highlight on the home page**: features the event on the public home page.
- **Published** ("Visible to all community members"): until an event is published, volunteers
  can't see it. Leave it unpublished while you set it up.

Saving takes you to the event's tabs: **Overview**, **Roles & Staffing**, **Tasks**,
**Check-in Monitor** and **Impact Report**. The following lessons cover each one.

## Finding events

The **Event Planner** lists upcoming events with their staffing progress, and has a search for past
events. **Admin Home** highlights **Unfilled Roles** that need attention.
"""),

    ('Roles, staffing and invitations', """
The **Roles & Staffing** tab is where an event gets its volunteers.

## Adding role slots

Click **Add Role to Event**:

1. **Select Role** from your Role Directory, or **Create a new role template** if it doesn't exist yet.
2. **Define Time Slots**: for each slot, the **Date**, **Time Range**, **Volunteers Needed** and
   **Extra allowed** (how many more than needed may sign up). Use **Add Another Slot** for more shifts
   of the same role, such as a morning and an afternoon shift.
3. **Open sign-up**: leave it on so any eligible volunteer can sign up. Turn it off to make the slots
   **invite-only**.
4. Save, or choose **Add & Invite Volunteers** to go straight to invitations.

Click any slot later to edit its times, headcount, area or shift lead, or to delete it.

## Staffing Health

The tab shows how full each role is and flags shortages, so you can see at a glance where to recruit.
**Quick Actions** let you **Publish Event** / **Unpublish Event**, **Message All Volunteers**
(an in-app notification to everyone signed up) and **View Map**.

## Invitations

Choose **Invite** to open **Craft Invitations**:

- Pick the **Role & time slot** you are inviting for.
- Choose recipients. Volunteers whose skills match the role's preferred skills are good candidates.
- Write the **Subject Line** and **Message Body**. `[Name]` is replaced with each volunteer's first
  name, and a sign-up link is added automatically. The **Preview** shows exactly what they get.

Invitations are the only way into invite-only slots. Volunteers see them under **My Events >
Invitations** and can accept or decline.

## How volunteers sign up

On the public event page volunteers pick an open role and a time slot. If the role has mandatory
training they haven't finished, the page tells them which modules to complete first. Once signed up,
the event appears under **My Events** with directions, the check-in point, an **Add to Calendar**
button and their team.
"""),

    ('Chain of command, areas and leads', """
Large events need structure. Any event can turn on a **chain of command**; the **Roles & Staffing**
tab suggests it once an event needs more than 35 volunteers. Choose **Set Up Chain of Command**.

## The three levels

1. **General coordinators**: set in the event editor. They run the event and assign the leads below.
2. **Area leads**: each **area** (a place at the event, like *Kitchen*, or a responsibility, like
   *Parking*) gets one or more leads. An area lead is signed up to their own shift in the built-in
   *Area lead* role, so they check in and earn points like anyone else. Their shift should cover the
   area's working hours; if one person can't cover it all, add another lead for the rest.
3. **Shift leads**: each role slot gets a shift lead, who is always one of the people signed up for
   it.

## Setting it up

- Create **areas** with **New Area**, then place each slot in an area (edit the slot and pick an
  **Area**). The same role can sit in several areas.
- Assign area leads with **Assign Lead**: choose the person and their shift times.
- Pick a slot's **Shift lead** in the slot editor, or leave it on **Pick automatically 48 h before**.

## Automatic deadlines

- Lead positions stay open until **48 hours** before the event. Then every open position is filled
  with the signed-up volunteer who has the most impact points, and the coordinators get a
  notification to review the picks.
- **24 hours** before the event, the leads that are set **lock**.

Even without a chain of command, any slot that more than 5 people can sign up for gets a shift lead.

## What volunteers see

On the event page, volunteers see **My Team**: their shift lead, their area lead and the
coordinators. If they try to contact someone other than their own area lead, they're asked to
**try their lead first**, which keeps questions flowing up the chain instead of all landing on you.

Removing the chain of command deletes the areas and area leads; roles and sign-ups stay.
"""),

    ('Event day: kiosk check-in and the monitor', """
On the day, volunteers check in and out at a **kiosk**: any tablet, laptop or phone running the
kiosk screen. The time between check-in and check-out becomes their **shift**.

## Setting up a kiosk

1. On the device, sign in with a staff account and choose **Kiosk Mode** in the console sidebar.
2. Pick **Events today** for an event, or a venue under **Venues (permanent positions)**.
3. Bookmark the screen it opens so the device can go straight there next time.

The kiosk never signs volunteers into the website; it only records shifts.

## How volunteers check in

- They type their 6-digit **kiosk ID** (shown in their account menu under **Your kiosk ID**), or
  sign in with their email and password.
- If they are signed up for a shift starting within the next hour, it starts immediately.
- If they signed up for more than one, they choose which.
- If they aren't signed up at all, the kiosk offers roles that still need people and that they're
  trained for. Stepping in this way is a **clutch cover** and earns bonus points.
- At a venue kiosk, staff can only start shifts in permanent roles assigned to them.
- When they leave, they sign in again and choose **Check Out**.

After the idle timeout set under **Organization > Identity**, the kiosk signs the volunteer out on
its own, ready for the next person.

## The Check-in Monitor

The event's **Check-in Monitor** tab is your live dashboard:

- **Checked In** right now and **Expected Next 1hr**.
- **Recent Activity**: every check-in and check-out as it happens.
- **Staffing Overview** per role, with **Critical Shortage** warnings so you can **call in backup**.
- **Manual Check-in**: start the shift for a registered volunteer who can't use the kiosk.
- **Open Kiosk** and **Export Log** (a spreadsheet of every shift).
"""),

    ('After the event: reports and recognition', """
Once an event ends, the **Impact Report** tab sums it up and helps you thank the people who made it
happen.

## The Impact Report

- **Volunteer Turnout**, **Total Volunteers** and **Hours Logged**.
- **Role-Specific Breakdown**, including **Efficiency**: hours actually logged compared with hours
  scheduled for signed-up volunteers.
- **Post-Event Statement**: a short message about what the event achieved. Volunteers see it on their
  thank-you page and on their event certificates, so it's worth writing.
- **Top Performers** and **Team Endorsements**: **Endorse** volunteers to recognize their work.
- **What Volunteers Said**: ratings and comments from volunteer feedback.
- **Share** and **Export** the report.

## What volunteers get

After an event, each volunteer has a **thank-you page** with their hours, impact points and the
event's overall numbers. From there they can:

- download **My Certificate** for the event;
- rate the event and leave suggestions (**Share Your Experience**), which feeds your report;
- endorse teammates they worked with in one tap.

Everything they've done is collected in their **Impact Record** and printable volunteer resume, which
are handy for school volunteer hours or job applications.

## Endorsements

An **endorsement** is public recognition of someone's skills. Volunteers and staff can endorse each
other; endorsements show on profiles and in the **Community Endorsements** feed. Being endorsed by a
teammate from the same event earns a small number of impact points.
"""),

    ('Managing volunteers', """
The **Volunteer Pool** lists everyone with an account, staff included (but not you), with search, status filters, totals and the
**Top Skills in Pool**. **Export List** downloads it as a spreadsheet.

## Adding people

Volunteers normally create their own accounts with **Sign up** on the public site. To add someone
yourself, choose **Add Volunteer**, enter their name, email and a **temporary password**, and share the
password with them; they can change it from their account settings. A 6-digit kiosk ID is created
automatically.

## A volunteer's page

Click anyone to open their page:

- **Service History**, **Total Hours**, **Events Attended** and **Impact Points**.
- **Training Modules** they started or finished, and their **Skills & Certs**.
- **Points Ledger**: every award that makes up their points. **Adjust points** to add or remove
  points with a reason the volunteer can see.
- **Private Feedback** and an internal **Rating** that only staff can see, plus **Admin notes**.
- **Reliability**: the share of their shifts they turned up for. Everyone starts at 100%. A
  **no-show** (signed up, but never checked in, or checked in after more than half of the shift had
  passed; arriving a little late is fine) and a **late
  cancellation** (cancelling less than 24 hours before the shift starts) both lower it. Volunteers
  cancel from the event page with **Cancel a Shift**, and the event's coordinators get a
  notification when a cancellation is late.
- **Message**: send them an in-app notification.
- **Edit Profile**, and **Deactivate** / **Reactivate** the account.
- **Roles**: assign **permanent roles** (see the Roles lesson).

## Staff access

The **Staff access** section controls what someone can do:

- **Staff status**: lets them open this Admin Portal and the Django admin.
- **Superuser**: every permission without being assigned it. You are a superuser. Give this sparingly.
- **Permission groups**: bundles of Django permissions, created in the Django admin.

To bring in a co-organizer, have them sign up, then turn on **Staff status** on their page.
"""),

    ('Impact points and levels', """
Impact points reward the work volunteers do, and levels give them something to grow into.

## How points are earned

Every award is a line in the volunteer's **Points Ledger**:

- **Shifts**: points per hour x hours x the role's weight x a capped **reach** bonus (more people
  served per volunteer, from the event's expected attendees or the role's beneficiaries per hour)
  x **clutch** bonuses.
- **Clutch bonuses**: covering a slot as a walk-in at the kiosk, signing up at the last minute for an
  understaffed slot, and early-morning or late-night shifts.
- **Training**: a one-time award for finishing a module, based on its length. Staff-only modules such
  as this guide don't award points.
- **Endorsements**: a small bonus when a teammate from the same event endorses you, or when a
  staff member does (no shared event needed; once per staff member per person every 30 days).
- **Completed profile**: a one-time award the first time a volunteer's profile has a photo, a bio,
  skills and a location. Their **Edit Profile** page shows a checklist of what's left.
- **Admin adjustments**: made from the volunteer's page.

## Tuning the rules

**Organization > Points** holds every number: **Points per hour**, **Top role weight**,
**Reach bonus cap**, the walk-in, last-minute and early/late percentages and windows, points per
training minute with a minimum and maximum per module, endorsement points with a monthly cap, and
the completed-profile award (0 turns it off).
New rules apply to new shifts. A developer can reapply them to past shifts with
`python manage.py recalculate_points`.

## Levels

**Organization > Levels** is your level ladder. Each level has a **name**, **minimum points** and a
description of its **benefits**. A volunteer's level is the highest one whose minimum they've
reached, so changes apply to everyone right away. Fill in the last row to add a level; removing a
level never removes anyone's points. The chart shows how many volunteers are at each level.

Volunteers see the ladder on the **Recognition** page in the main menu, with each level's benefits
and a "You are here" marker on their own level. Write benefits as a comma-separated list; each one
shows as its own perk. The Recognition link stays hidden until you add your first level.
"""),

    ('Communication and email', """
Town Hall talks to volunteers in two ways.

## In-app notifications

The bell icon in the header. Messages you send from the console (to one volunteer, or to everyone at
an event) arrive here, along with reminders such as lead assignments. They work without any setup.

## Email through listmonk

Email is sent through **listmonk**, a free, self-hosted newsletter and mailing server. Until it's
connected, emails are only printed in the server log, which also means **password reset and
invitation emails don't reach anyone**, so set it up before you invite volunteers.

1. Under **Organization > Backend > Email server (listmonk)**, enter the **listmonk URL**,
   **API user**, **API token** and **From address**. **How to connect listmonk** explains how to create
   the API user.
2. Actual delivery (SMTP, Amazon SES, Postmark...) is configured inside listmonk.
3. When you save, Town Hall creates a private **Town Hall users** list, uploads your logo, creates one
   email template per event type and language, and adds your users.

## Choosing what gets sent

**Communication > Email** lists every kind of email (welcome, password reset, training completed,
new event published and more). For each, you can switch it on or off and choose which template it
uses in each language. **Reset to default** restores Town Hall's version of a template.

- Each email goes out in the language the person last used the site in.
- Changing your name, logo or colors under **Organization > Identity** updates every template.
- **New event published** is a visual campaign. With **Send immediately** off, it waits as a draft in
  listmonk so you can edit it before sending. You can also send it to extra listmonk lists. The
  **Newsletter** form in the site footer signs visitors up to those extra lists, and it's hidden until
  you pick at least one.
- People who unsubscribe stay unsubscribed.
- **Profile reminder** and **Second profile reminder** go out by themselves, a day and a week after
  someone registers, but only if their profile still lacks a photo, bio, skills or location. Each
  lists what's missing and mentions the completed-profile award. People who registered long before
  you switch them on don't get them.
- **Shift reminder** goes out by itself at 9 AM the day before each shift, to everyone signed up for
  it, with the role, time and place. People who sign up after 9 AM that day aren't reminded, because
  their sign-up confirmation just told them the same thing.
- When a Town Hall update adds a new kind of email, choose **Re-create missing templates** so it gets
  a template in each language.
"""),

    ('Task lists with Bell Tower', """
**Bell Tower** is a separate service that holds your organization's shared task lists. Once it's
connected, every event gets a **Tasks** tab for planning and running the day. It's optional.

## Connecting

Under **Organization > Backend**, choose **Connect to Bell Tower**, type your Bell Tower address, and
approve access in Bell Tower. Town Hall acts as the Bell Tower account you approve with, which needs
to be a Bell Tower staff account so it can link your volunteers. Each person is matched to the Bell
Tower account with their email, or one is created for them.

## The Tasks tab

- **Planning**: one list for everything to get done before the day: the venue, food, supplies,
  outreach. It's hidden once the event starts (**Show planning** brings it back).
- **During the event**: checklists for the day, such as setup crew, kitchen or registration desk.
  Choose **Create list** to add one.
- Every role at the event automatically gets its own **role tasks** list. People who sign up for the
  role join it, and shift leads and area leads manage it.

Add tasks with a description, an expiry and an assignee. Click a task to edit it, or a list's name to
rename it. Drag a task onto another list to move it, or drag a list by its handle onto another to
merge them. Share a list by email with staff, coordinators or the event's volunteers.

Volunteers open their tasks with **My tasks** in the account menu, or **Your tasks** on the event
page, without needing a Bell Tower password.
"""),

    ('Languages and translation', """
Town Hall can run in several languages at once: English, Spanish, French, Ukrainian, Traditional
Chinese and Tagalog. Volunteers switch language from the site header.

## Translating your content

Everything you write (event titles, role descriptions, lessons, quiz questions...) has a field per
enabled language in the console editors. Only the first (default) language is required; anything
left empty falls back to it.

The **Auto-translate** button next to those fields fills the empty languages for you from the first
one. Review the results, especially for training content.

## Translation services

Auto-translate works out of the box with free services that have daily limits. For heavier use, add a
key under **Organization > Backend > Auto-translate**: your own **LibreTranslate** server, **Google
Cloud Translation** or **DeepL** (with a generous free tier). Town Hall tries them in that order and
falls back to the free ones.

To change which languages are switched on, or the default language, use **Organization > Region**
and restart the app.
"""),

    ('The Claude connector, Django admin and maintenance', """
## Planning with Claude

Town Hall includes a **Claude connector**, so staff can plan events by chatting with Claude. Under
**Organization > Backend > Claude connector (MCP)** you'll find the **Connector URL**. In Claude, open
**Settings > Connectors**, choose **Add custom connector** and paste it, then sign in with your
staff account and approve.

Claude can then read your events, roles, training and volunteers, and create or update events, role
slots, areas, invitations, task lists, role templates and quizzes. It never deletes anything, and it
can't change people, lessons or settings. Only active staff can connect, and **Connected apps** on the
same page lets you disconnect any of them.

## The Django admin

The Django admin at `/admin/` is a raw view of every table. You'll rarely need it, but it's where you
manage **permission groups**, and rename or delete **event categories**. Sign in with your superuser
account.

## Things to keep in mind

- The **Admin Home** greets you with today's activity, weekly hours logged, upcoming events and roles
  that still need people. It's a good place to start each day.
- **View Public Site** in the console sidebar shows the site as volunteers see it.
- Unpublished events and modules are invisible to volunteers, so you can prepare things quietly.
"""),

    ('A sample quiz is next', """
You've reached the end of the lessons. Well done!

Most training modules end with a quiz to check what volunteers learned. The next item in this module
is a **sample quiz**: this is what a quiz looks like to a volunteer. The questions are simple
and about this guide. Answer them, choose **Submit**, and you'll see the results page with each
correct answer.

Once you pass, you can mark this module complete and receive a certificate, just like a volunteer
would after their training.

When you're done, a good next step is **Organization > Identity** to set up your organization.
"""),
]

QUIZ_TITLE = 'Sample quiz: this is what a quiz looks like'

# (question, options A-D, correct letter) or (question, True/False answer)
QUESTIONS = [
    ('What must a volunteer do before they can sign up for a role that has a mandatory training module?',
     ['Complete the training module', 'Ask a coordinator for permission', 'Upload a photo ID', 'Nothing'], 'A'),
    ('Volunteers can see an event before it is published.', False),
    ('Where do volunteers check in and out on the day of an event?',
     ['The Django admin', 'A kiosk', 'The Role Directory', 'Organization settings'], 'B'),
    ('Completing shifts earns volunteers impact points, which decide their level.', True),
    ('Which section of the console holds reusable role templates?',
     ['Volunteer Pool', 'Venues', 'Role Directory', 'Communication'], 'C'),
]


def admin_guide():
    """The admin guide module, or None if it hasn't been created."""
    return TrainingModule.objects.filter(system_key=ADMIN_GUIDE_KEY).first()


@transaction.atomic
def ensure_admin_guide():
    """Create the staff-only admin guide if it doesn't exist yet, and return it.

    Written under the default language, like other content Town Hall creates, so every
    other language falls back to it until it's translated.
    """
    module = admin_guide()
    if module:
        return module
    with translation.override(settings.LANGUAGE_CODE):
        module = TrainingModule.objects.create(
            system_key=ADMIN_GUIDE_KEY, title=TITLE, description=DESCRIPTION, icon='admin_panel_settings',
            published=True, staff_only=True,
        )
        for order, (title, content) in enumerate(LESSONS, start=1):
            TrainingLesson.objects.create(training_module=module, title=title, content=content.strip(), order=order)
        quiz = Quiz.objects.create(training_module=module, title=QUIZ_TITLE, percentage_to_pass=60,
                                   order=len(LESSONS) + 1)
        for question in QUESTIONS:
            if len(question) == 2:
                text, answer = question
                q = QuizQuestion.objects.create(question_text=text, option_a='True', option_b='False',
                                                correct_option='A' if answer else 'B', is_true_false=True)
            else:
                text, (a, b, c, d), correct = question
                q = QuizQuestion.objects.create(question_text=text, option_a=a, option_b=b, option_c=c,
                                                option_d=d, correct_option=correct)
            quiz.questions.add(q)
    return module
