"""Town Hall's side of Switchboard, the organization's no-code workflow builder.

A building does two things for Switchboard: it emits events and it runs actions. The contract
is Switchboard's docs/PROTOCOL.md (version 1); names, paths and JSON shapes here follow it.

- manifest.py   what Town Hall offers: the triggers and actions served at /.well-known/switchboard
- views.py      the manifest, the consent page, the token exchange and the bearer-key API
- actions.py    the actions Switchboard can call (person.find, person.invite, ...)
- signals.py    where events come from: model signals on the real things that happen
- events.py     builds an event's envelope and queues it for every connection
- delivery.py   signs and posts deliveries, retries them and keeps the log
"""
