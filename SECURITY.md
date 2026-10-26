# Security

## Protect your Telegram account

Never share or commit any of these files:

- `config.json`, which contains your API hash and phone number
- `session.session`, which is a reusable authenticated Telegram session
- `state.json` or watcher logs, which may contain chat metadata

The installer stores runtime data under `~/.tgspamwatch/` with restrictive
permissions. Repository ignore rules provide an additional guard, but do not
replace checking `git status` before every push.

If a session file is exposed, terminate the affected session immediately in
Telegram under **Settings → Devices**. If API credentials are exposed, manage
or replace them at <https://my.telegram.org/apps>.

## Reporting a vulnerability

Please use a private GitHub Security Advisory rather than a public issue. Do
not include API credentials, phone numbers, session files, or message content.
