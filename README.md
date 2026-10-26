# Telegram Spam Watcher for macOS

A conservative personal-inbox spam filter built with
[Telethon](https://github.com/LonamiWebs/Telethon). It checks Telegram every
five minutes, reports decisions to Saved Messages, and can optionally block
senders and remove spam chats from your inbox.

## Safety model

- Contacts, bots, verified accounts, and chats you have answered are protected.
- Groups are report-only and are never automatically modified.
- A private message from a non-contact you have never answered is suspicious
  by default.
- A substantive introduction can be exempted when it explains the context and
  makes a concrete request without other spam signals.
- New installations start in **flag-only mode**. Blocking and deletion require
  an explicit configuration change.
- Deletion is local to your account (`revoke=False`); the watcher does not try
  to erase messages from the sender's account.

The purpose detector currently recognizes English-language introductions.
Review flag-only results before enabling automatic actions.

## Requirements

- macOS with Python 3
- A Telegram account
- Telegram application credentials from <https://my.telegram.org/apps>

Your API ID and API hash identify your own Telegram application. They are not
the login code sent to your Telegram account.

## Install

```bash
git clone https://github.com/meleshuk/telegram-spamwatch.git
cd telegram-spamwatch
./install.sh
```

The installer prompts for your API ID, API hash, and phone number. It writes
them directly to `~/.tgspamwatch/config.json` with mode `600`; credentials are
never written into the repository.

Complete Telegram login:

```bash
~/.tgspamwatch/venv/bin/python ~/.tgspamwatch/tg_login.py
```

Telegram may request a login code and, if enabled, your two-step verification
password. The authenticated session remains in `~/.tgspamwatch/`.

The watcher then runs every five minutes as a macOS LaunchAgent. Reports and
cleanup receipts appear in Telegram Saved Messages. Runtime errors are written
to `~/.tgspamwatch/watcher.log`.

Re-running `./install.sh` updates the code without overwriting configuration,
the allowlist, login session, or scan state.

## Action modes

The default is report-only:

```json
"action_mode": "flag"
```

After reviewing the reports, edit `~/.tgspamwatch/config.json` and set:

```json
"action_mode": "block_delete"
```

Eligible unsolicited private-chat senders will be blocked and their chats
deleted locally after an audit report is saved. To return to report-only mode,
change the value back to `flag`.

To protect a specific non-contact, add their numeric Telegram user ID to
`allowlist_ids` in the same configuration file.

## Historical cleanup

After validating flag-only results, apply the policy to older private chats:

```bash
~/.tgspamwatch/venv/bin/python ~/.tgspamwatch/tg_spamwatch.py --backfill
```

Backfill checks the full history for any reply from you and processes at most
20 candidates per pass. Run it again if a pass reports 20 actions. This command
is destructive when `action_mode` is `block_delete`.

## Detection

Signals include:

- an unsolicited private chat with no prior reply;
- no username or mutual-contact relationship;
- generic familiarity or companionship bait;
- common scam keywords, suspicious links, and URL shorteners;
- channel-forwarded promotions and excessive capitalization.

Messages that provide meaningful identity or referral context plus a concrete
request can receive an exemption, unless they also contain an obvious spam
signal. Tune keyword lists and thresholds near the top of `tg_spamwatch.py`.

## Uninstall

```bash
./uninstall.sh
```

This removes the schedule but preserves `~/.tgspamwatch/`. To fully disconnect,
also terminate the session in Telegram under **Settings → Devices**, then
remove the runtime directory yourself.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest -v
```

Do not use a real `config.json` or Telegram session in tests. See
[SECURITY.md](SECURITY.md) before publishing changes.

## License

[MIT](LICENSE)
