#!/usr/bin/env python3
"""Telegram personal-inbox spam watcher.

Runs every few minutes via launchd. Scans new incoming messages in your
private chats and groups, scores them for spam, and reports candidates to
your Saved Messages. In block_delete mode, eligible unsolicited private
chats are blocked and removed locally after the audit report is sent.

Tune: edit SPAM_KEYWORDS / thresholds below, or add sender IDs to
ALLOWLIST_IDS (as integers) in ~/.tgspamwatch/config.json.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

BASE = os.path.expanduser("~/.tgspamwatch")
CONFIG = os.path.join(BASE, "config.json")
STATE = os.path.join(BASE, "state.json")
LOCK = os.path.join(BASE, ".lock")
SESSION = os.path.join(BASE, "session")

# --- Tunables ---------------------------------------------------------------
SPAM_KEYWORDS = [
    "crypto", "airdrop", "double your", "investment opportunity",
    "guaranteed profit", "guaranteed return", "claim your", "free money",
    "earn money fast", "limited time offer", "verify your account",
    "account will be suspended", "you've won", "congratulations",
    "lottery", "forex", "binary options", "dm me", "message me to",
    "100% profit", "risk-free",
]
SUSPICIOUS_TLDS = {
    "xyz", "top", "click", "link", "buzz", "win", "bid", "loan",
    "gq", "ml", "tk", "cf", "pw",
}
URL_SHORTENERS = {
    "bit.ly", "t.ly", "tinyurl.com", "goo.gl", "ow.ly", "is.gd",
    "buff.ly", "rebrand.ly", "cutt.ly",
}
URL_RE = re.compile(r"https?://([a-z0-9.-]+)", re.IGNORECASE)
GENERIC_OPENER_PATTERNS = (
    re.compile(r"\blet me know if you want company\b"),
    re.compile(r"\b(?:it's been (?:so )?long|how's life treating you)\b"),
    re.compile(r"\bhappy you(?:'re| are) here\b"),
)
PURPOSE_CONTEXT_PATTERNS = (
    re.compile(r"\b(?:referred|recommended|introduced) (?:me|you|by)\b"),
    re.compile(r"\b(?:gave|shared|sent) me your (?:contact|details|number)\b"),
    re.compile(r"\b(?:i am|i'm|this is|my name is)\b.{0,50}\b(?:from|with|at)\b"),
    re.compile(r"\b(?:reaching out|contacting you)\b.{0,80}\b(?:about|regarding|because|to)\b"),
    re.compile(r"\b(?:about|regarding|concerning|because of)\b"),
)
PURPOSE_REQUEST_PATTERNS = (
    re.compile(r"\b(?:can|could|would) you\b"),
    re.compile(r"\b(?:i'd|i would) like to\b"),
    re.compile(r"\b(?:looking for|need|request|question|invite)\b"),
    re.compile(r"\b(?:discuss|schedule|review|help with|connect about)\b"),
)
SCORE_THRESHOLD = 4
PER_DIALOG_LIMIT = 30
MAX_CANDIDATES_PER_RUN = 20
FIRST_RUN_LOOKBACK_DAYS = 7
REPORT_CHUNK_LIMIT = 3900
# ----------------------------------------------------------------------------


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def secure_runtime_files():
    """Keep Telegram credentials and the reusable login session private."""
    os.makedirs(BASE, mode=0o700, exist_ok=True)
    os.chmod(BASE, 0o700)
    for path in (CONFIG, STATE, f"{SESSION}.session"):
        if os.path.exists(path):
            os.chmod(path, 0o600)


def score_message(text, msg):
    """Return (score, [reasons]). Conservative: obvious spam only."""
    score = 0
    reasons = []
    low = text.lower().replace("’", "'")

    for kw in SPAM_KEYWORDS:
        if kw in low:
            score += 2
            reasons.append(f"keyword '{kw}'")
            break

    if any(pattern.search(low) for pattern in GENERIC_OPENER_PATTERNS):
        score += 2
        reasons.append("generic stranger opener")

    for m in URL_RE.finditer(text):
        host = m.group(1).lower()
        tld = host.rsplit(".", 1)[-1] if "." in host else ""
        if host in URL_SHORTENERS or (host != "t.me" and tld in SUSPICIOUS_TLDS):
            score += 2
            reasons.append(f"suspicious link ({host})")
            break
    else:
        if URL_RE.search(text):
            score += 1
            reasons.append("contains link")

    fwd = getattr(msg, "forward", None)
    if fwd is not None and getattr(fwd, "channel_id", None):
        score += 2
        reasons.append("forwarded from channel")

    alpha = [c for c in text if c.isalpha()]
    if len(alpha) > 20 and sum(c.isupper() for c in alpha) / len(alpha) > 0.6:
        score += 1
        reasons.append("mostly caps")

    return score, reasons


def clearly_explains_purpose(text):
    """Require both meaningful context and a concrete ask or intention."""
    low = text.lower().replace("’", "'")
    if len(re.findall(r"\b[\w'-]+\b", low)) < 12:
        return False
    has_context = any(pattern.search(low) for pattern in PURPOSE_CONTEXT_PATTERNS)
    has_request = any(pattern.search(low) for pattern in PURPOSE_REQUEST_PATTERNS)
    return has_context and has_request


def has_obvious_spam_reason(reasons):
    strong_prefixes = ("keyword '", "suspicious link")
    strong_reasons = {"forwarded from channel", "generic stranger opener"}
    return any(
        reason.startswith(strong_prefixes) or reason in strong_reasons
        for reason in reasons
    )


def apply_purpose_exemption(score, reasons, text, context_score):
    if (
        context_score
        and clearly_explains_purpose(text)
        and not has_obvious_spam_reason(reasons)
    ):
        return max(0, score - 4)
    return score


def score_dialog_context(dialog, messages, prior_reply=None):
    """Score relationship context that a standalone message cannot show."""
    entity = dialog.entity
    if (
        not dialog.is_user
        or getattr(entity, "bot", False)
        or getattr(entity, "verified", False)
    ):
        return 0, []
    if prior_reply is None:
        prior_reply = any(message.out for message in messages)
    if prior_reply:
        return 0, []

    score = 4
    reasons = ["unsolicited private chat with no prior reply"]

    if not getattr(entity, "username", None) and not getattr(
        entity, "mutual_contact", False
    ):
        score += 1
        reasons.append("no username or mutual contact")

    return score, reasons


def sender_label(msg):
    sender = getattr(msg, "sender", None)
    if sender is None:
        return f"id {msg.sender_id}"
    name = f"{getattr(sender, 'first_name', '') or ''} {getattr(sender, 'last_name', '') or ''}".strip()
    username = getattr(sender, "username", None)
    label = name or f"id {msg.sender_id}"
    if username:
        label += f" (@{username})"
    return label


async def get_contact_ids(client):
    """Return contact user IDs using Telethon's stable raw API."""
    from telethon.tl.functions.contacts import GetContactsRequest

    result = await client(GetContactsRequest(hash=0))
    return {user.id for user in getattr(result, "users", [])}


def build_reports(candidates):
    """Build complete reports without silently truncating candidates."""
    action_count = sum(candidate["auto_action"] for candidate in candidates)
    if action_count:
        heading = (
            f"🚫 {len(candidates)} spam candidate(s); {action_count} private "
            "chat(s) queued for block + local deletion."
        )
    else:
        heading = (
            f"🚩 {len(candidates)} possible spam message(s) — "
            "report-only, nothing deleted."
        )
    reports = []
    current = heading
    for candidate in candidates:
        item = (
            f"\n\n• {candidate['from']} in “{candidate['chat']}” "
            f"({candidate['date']}) "
            f"[score {candidate['score']}: {candidate['reasons']}]\n"
            f"  “{candidate['excerpt']}”\n"
            f"  ref {candidate['ref']}\n"
            f"  action: "
            f"{'BLOCK + DELETE LOCAL CHAT' if candidate['auto_action'] else 'report only'}"
        )
        if len(current) + len(item) > REPORT_CHUNK_LIMIT and current != heading:
            reports.append(current)
            current = heading + " (continued)" + item
        else:
            current += item
    reports.append(current)
    return reports


def should_auto_action(action_mode, dialog, context_score):
    """Destructive actions are limited to unanswered one-to-one chats."""
    return (
        action_mode == "block_delete"
        and dialog.is_user
        and not getattr(dialog.entity, "bot", False)
        and not getattr(dialog.entity, "verified", False)
        and context_score >= SCORE_THRESHOLD
    )


async def has_prior_reply(client, dialog, messages, full_history=False):
    """Check recent messages, or all history during a destructive backfill."""
    if any(message.out for message in messages):
        return True
    if not full_history:
        return False
    sent = await client.get_messages(dialog.entity, limit=1, from_user="me")
    return bool(sent)


async def block_and_delete(client, entity):
    """Block a user and remove the dialog locally, reporting each outcome."""
    from telethon.tl.functions.contacts import BlockRequest

    result = {"blocked": False, "deleted": False, "errors": []}
    try:
        await client(BlockRequest(id=entity))
        result["blocked"] = True
    except Exception as exc:
        result["errors"].append(f"block failed: {type(exc).__name__}: {exc}")
    try:
        await client.delete_dialog(entity, revoke=False)
        result["deleted"] = True
    except Exception as exc:
        result["errors"].append(f"delete failed: {type(exc).__name__}: {exc}")
    return result


def build_action_receipt(results):
    lines = ["🧹 Spam cleanup results:"]
    for result in results:
        outcomes = [
            "blocked" if result["blocked"] else "NOT blocked",
            "chat deleted locally" if result["deleted"] else "chat NOT deleted",
        ]
        line = f"\n• {result['label']}: {', '.join(outcomes)}"
        if result["errors"]:
            line += f" ({'; '.join(result['errors'])})"
        lines.append(line)
    return "\n".join(lines)


def dialog_has_new_messages(dialog, last_seen):
    """Avoid a network fetch when the dialog's top message is already seen."""
    top_message = getattr(dialog, "message", None)
    return not (
        last_seen
        and top_message is not None
        and top_message.id <= last_seen
    )


async def main():
    secure_runtime_files()
    backfill = "--backfill" in sys.argv[1:]

    # Stale-lock guard: skip if another run is active (lock newer than 10 min).
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        if time.time() - os.path.getmtime(LOCK) < 600:
            return
        os.remove(LOCK)
        return  # let the next scheduled run pick it up cleanly

    try:
        await run(backfill=backfill)
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


async def run(backfill=False):
    from telethon import TelegramClient

    cfg = load_json(CONFIG, None)
    if not cfg:
        sys.exit(f"No config at {CONFIG}; run tg_login.py first.")
    state = load_json(STATE, {})
    dialogs_state = state.setdefault("dialogs", {})
    allowlist = set(cfg.get("allowlist_ids", []))
    action_mode = cfg.get("action_mode", "flag")

    now = datetime.now(timezone.utc)
    first_run = not dialogs_state
    cutoff = now - timedelta(days=FIRST_RUN_LOOKBACK_DAYS if first_run else 1)

    async with TelegramClient(
        SESSION, cfg["api_id"], cfg["api_hash"]
    ) as client:
        if not await client.is_user_authorized():
            sys.exit("Not logged in. Run tg_login.py first.")

        contact_ids = await get_contact_ids(client)

        candidates = []
        actions = {}
        fetched_dialogs = 0
        new_messages = 0
        dialog_errors = 0
        async for dialog in client.iter_dialogs():
            if backfill and not dialog.is_user:
                continue
            # Skip broadcast channels you follow; scan private chats + groups.
            if dialog.is_channel and not dialog.is_group:
                continue
            dkey = str(dialog.id)
            last_seen = dialogs_state.get(dkey, 0)
            if backfill and (
                getattr(dialog.entity, "id", None) in contact_ids
                or getattr(dialog.entity, "id", None) in allowlist
            ):
                continue
            if not backfill and not dialog_has_new_messages(dialog, last_seen):
                continue
            try:
                messages = await client.get_messages(
                    dialog.entity, limit=PER_DIALOG_LIMIT
                )
                prior_reply = await has_prior_reply(
                    client, dialog, messages, full_history=backfill
                )
            except Exception:
                dialog_errors += 1
                continue
            fetched_dialogs += 1
            context_score, context_reasons = score_dialog_context(
                dialog, messages, prior_reply=prior_reply
            )
            newest = max(
                last_seen,
                messages[0].id if messages else 0,
            )
            messages_to_check = messages
            if backfill:
                latest_incoming = next(
                    (
                        message for message in messages
                        if not message.out and (message.text or "").strip()
                    ),
                    None,
                )
                messages_to_check = [latest_incoming] if latest_incoming else []
            for m in messages_to_check:
                if not backfill and m.id <= last_seen:
                    break
                newest = max(newest, m.id)
                if not backfill and m.date < cutoff:
                    continue
                new_messages += 1
                if m.out:                      # your own messages
                    continue
                text = (m.text or "").strip()
                if not text:
                    continue
                sid = m.sender_id
                if sid in contact_ids or sid in allowlist:
                    continue
                score, reasons = score_message(text, m)
                score += context_score
                reasons.extend(context_reasons)
                score = apply_purpose_exemption(
                    score, reasons, text, context_score
                )
                if score >= SCORE_THRESHOLD:
                    auto_action = should_auto_action(
                        action_mode, dialog, context_score
                    )
                    candidates.append({
                        "from": sender_label(m),
                        "chat": dialog.name,
                        "date": m.date.astimezone().strftime("%Y-%m-%d %H:%M"),
                        "score": score,
                        "reasons": ", ".join(reasons),
                        "excerpt": text[:150].replace("\n", " "),
                        "ref": f"{dialog.id}:{m.id}",
                        "auto_action": auto_action,
                    })
                    if auto_action and dkey not in actions:
                        actions[dkey] = {
                            "entity": dialog.entity,
                            "label": sender_label(m),
                        }
                    if len(candidates) >= MAX_CANDIDATES_PER_RUN:
                        break
            dialogs_state[dkey] = newest
            if len(candidates) >= MAX_CANDIDATES_PER_RUN:
                break

        if candidates:
            for report in build_reports(candidates):
                await client.send_message("me", report)

        action_results = []
        for action in actions.values():
            result = await block_and_delete(client, action["entity"])
            result["label"] = action["label"]
            action_results.append(result)
        if action_results:
            await client.send_message("me", build_action_receipt(action_results))

        state["last_run"] = now.isoformat()
        state["last_result"] = {
            "backfill": backfill,
            "fetched_dialogs": fetched_dialogs,
            "new_messages": new_messages,
            "candidates": len(candidates),
            "auto_actions": len(action_results),
            "action_errors": sum(bool(result["errors"]) for result in action_results),
            "dialog_errors": dialog_errors,
        }
        with open(STATE, "w") as f:
            json.dump(state, f)
        os.chmod(STATE, 0o600)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
