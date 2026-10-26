#!/usr/bin/env python3
"""One-time Telegram login for the spam watcher.

Reads api_id / api_hash / phone from config.json (next to this script or in
~/.tgspamwatch/), sends you a login code in Telegram, and saves the session
to ~/.tgspamwatch/session.session. Run once.
"""
import asyncio
import getpass
import json
import os
import sys

SRC = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.expanduser("~/.tgspamwatch")
SESSION = os.path.join(BASE, "session")


def secure_runtime_files():
    """Keep Telegram credentials and the reusable login session private."""
    os.makedirs(BASE, mode=0o700, exist_ok=True)
    os.chmod(BASE, 0o700)
    for path in (os.path.join(BASE, "config.json"), f"{SESSION}.session"):
        if os.path.exists(path):
            os.chmod(path, 0o600)


def load_config():
    for path in (os.path.join(BASE, "config.json"),
                 os.path.join(SRC, "config.json")):
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f), path
    sys.exit("config.json not found next to the script or in ~/.tgspamwatch/")


async def main():
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError, ApiIdInvalidError

    cfg, cfg_path = load_config()
    secure_runtime_files()
    # Keep a copy of the config in ~/.tgspamwatch for the watcher.
    dest = os.path.join(BASE, "config.json")
    if os.path.abspath(cfg_path) != os.path.abspath(dest):
        with open(dest, "w") as f:
            json.dump(cfg, f)
        os.chmod(dest, 0o600)

    phone = cfg["phone"]
    try:
        async with TelegramClient(SESSION, cfg["api_id"], cfg["api_hash"]) as client:
            if await client.is_user_authorized():
                print("Already logged in. Nothing to do.")
                return
            print(f"Requesting login code for {phone} ...")
            sent = await client.send_code_request(phone)
            code = input("Enter the code Telegram just sent you: ").strip().replace(" ", "")
            try:
                await client.sign_in(
                    phone, code, phone_code_hash=sent.phone_code_hash
                )
            except SessionPasswordNeededError:
                pw = getpass.getpass("Two-step verification password: ")
                await client.sign_in(password=pw)
            me = await client.get_me()
            name = f"{me.first_name or ''} {me.last_name or ''}".strip()
            print(f"Logged in as {name}. Session saved to ~/.tgspamwatch/.")
    except ApiIdInvalidError:
        sys.exit("Telegram rejected the api_id/api_hash pair. Check config.json.")
    finally:
        secure_runtime_files()


if __name__ == "__main__":
    asyncio.run(main())
