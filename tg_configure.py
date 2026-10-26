#!/usr/bin/env python3
"""Create a private Telegram Spam Watcher configuration."""

import argparse
import getpass
import json
import os
import re
import tempfile


DEFAULT_OUTPUT = os.path.expanduser("~/.tgspamwatch/config.json")


def validate_api_id(value):
    try:
        api_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("API ID must be a positive integer") from exc
    if api_id <= 0:
        raise ValueError("API ID must be a positive integer")
    return api_id


def validate_api_hash(value):
    api_hash = value.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise ValueError("API hash must be the 32-character value from Telegram")
    return api_hash


def validate_phone(value):
    phone = value.strip()
    if not re.fullmatch(r"\+[0-9 .()-]{7,24}", phone):
        raise ValueError("Phone must include its country code, for example +15555550123")
    return phone


def build_config(api_id, api_hash, phone):
    return {
        "action_mode": "flag",
        "allowlist_ids": [],
        "api_hash": validate_api_hash(api_hash),
        "api_id": validate_api_id(api_id),
        "phone": validate_phone(phone),
    }


def write_config(path, config):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix="config.", dir=directory)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def prompt_until_valid(prompt, validator, secret=False):
    while True:
        raw = getpass.getpass(prompt) if secret else input(prompt)
        try:
            return validator(raw)
        except ValueError as exc:
            print(f"Invalid value: {exc}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    api_id = prompt_until_valid("Telegram API ID: ", validate_api_id)
    api_hash = prompt_until_valid(
        "Telegram API hash (input hidden): ", validate_api_hash, secret=True
    )
    phone = prompt_until_valid(
        "Telegram phone with country code: ", validate_phone
    )
    config = build_config(api_id, api_hash, phone)
    write_config(args.output, config)
    print(f"Private config written to {args.output} (mode 600).")


if __name__ == "__main__":
    main()
