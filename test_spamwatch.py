import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

import tg_configure as configure
import tg_spamwatch as watcher


class FakeClient:
    async def __call__(self, request):
        return SimpleNamespace(
            users=[SimpleNamespace(id=101), SimpleNamespace(id=202)]
        )


class FakeActionClient:
    def __init__(self):
        self.block_requests = []
        self.deleted = []

    async def __call__(self, request):
        self.block_requests.append(request)

    async def delete_dialog(self, entity, *, revoke=False):
        self.deleted.append((entity, revoke))


class FakeHistoryClient:
    def __init__(self, sent_messages):
        self.sent_messages = sent_messages
        self.calls = []

    async def get_messages(self, entity, **kwargs):
        self.calls.append((entity, kwargs))
        return self.sent_messages


class SpamWatcherTests(unittest.TestCase):
    def message(self, **overrides):
        values = {
            "forward": None,
            "sender": None,
            "sender_id": 1,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_plain_message_is_not_spam(self):
        score, reasons = watcher.score_message(
            "Are we still meeting tomorrow?", self.message()
        )
        self.assertEqual((score, reasons), (0, []))

    def test_keyword_and_suspicious_link_reach_threshold(self):
        score, reasons = watcher.score_message(
            "Claim your free money at https://offer.xyz/win", self.message()
        )
        self.assertGreaterEqual(score, watcher.SCORE_THRESHOLD)
        self.assertIn("suspicious link (offer.xyz)", reasons)

    def test_known_web_link_alone_stays_below_threshold(self):
        score, _ = watcher.score_message(
            "Here is the agenda: https://example.com/agenda", self.message()
        )
        self.assertLess(score, watcher.SCORE_THRESHOLD)

    def test_generic_stranger_openers_get_content_signal(self):
        examples = (
            "Let me know if you want company 👌.",
            "Amy, it’s been too long! How’s life treating you?",
            "Hello, ❤ happy you're here!",
        )
        for text in examples:
            with self.subTest(text=text):
                score, reasons = watcher.score_message(text, self.message())
                self.assertEqual(score, 2)
                self.assertIn("generic stranger opener", reasons)

    def test_new_unconnected_private_chat_adds_context_signal(self):
        dialog = SimpleNamespace(
            is_user=True,
            entity=SimpleNamespace(username=None, mutual_contact=False),
        )
        messages = [SimpleNamespace(out=False)]
        score, reasons = watcher.score_dialog_context(dialog, messages)
        self.assertEqual(score, 5)
        self.assertIn("unsolicited private chat with no prior reply", reasons)
        self.assertIn("no username or mutual contact", reasons)

    def test_prior_reply_disables_stranger_context_signal(self):
        dialog = SimpleNamespace(
            is_user=True,
            entity=SimpleNamespace(username=None, mutual_contact=False),
        )
        messages = [SimpleNamespace(out=False), SimpleNamespace(out=True)]
        self.assertEqual(watcher.score_dialog_context(dialog, messages), (0, []))

    def test_verified_user_is_never_scored_as_unsolicited(self):
        dialog = SimpleNamespace(
            is_user=True,
            entity=SimpleNamespace(
                username="official", mutual_contact=False, bot=False, verified=True
            ),
        )
        messages = [SimpleNamespace(out=False)]
        self.assertEqual(watcher.score_dialog_context(dialog, messages), (0, []))

    def test_backfill_checks_full_history_for_prior_reply(self):
        sent = [SimpleNamespace(out=True)]
        client = FakeHistoryClient(sent)
        dialog = SimpleNamespace(entity=SimpleNamespace(id=123))
        recent = [SimpleNamespace(out=False)]
        found = asyncio.run(
            watcher.has_prior_reply(client, dialog, recent, full_history=True)
        )
        self.assertTrue(found)
        self.assertEqual(client.calls[0][1]["from_user"], "me")

    def test_clear_purpose_requires_context_and_concrete_ask(self):
        clear = (
            "Hi Irina, I am Sam from Acme Design. Jane referred me to you "
            "because I would like to discuss a product review next week."
        )
        self.assertTrue(watcher.clearly_explains_purpose(clear))
        self.assertFalse(watcher.clearly_explains_purpose("Hi, can we talk?"))
        self.assertFalse(
            watcher.clearly_explains_purpose(
                "Hello, I hope you are doing well and having a wonderful day today."
            )
        )

    def test_obvious_spam_does_not_earn_purpose_exemption(self):
        text = (
            "I am Dana from Acme and have a crypto opportunity to discuss with you. "
            "I am contacting you because I would like to offer an investment."
        )
        content_score, reasons = watcher.score_message(
            text,
            self.message(),
        )
        self.assertTrue(watcher.has_obvious_spam_reason(reasons))
        total = content_score + 5
        self.assertEqual(
            watcher.apply_purpose_exemption(total, reasons, text, 5), total
        )

    def test_clear_purpose_exempts_otherwise_unsolicited_message(self):
        text = (
            "Hi Irina, I am Sam from Acme Design. Jane referred me to you "
            "because I would like to discuss a product review next week."
        )
        reasons = [
            "unsolicited private chat with no prior reply",
            "no username or mutual contact",
        ]
        score = watcher.apply_purpose_exemption(5, reasons, text, 5)
        self.assertLess(score, watcher.SCORE_THRESHOLD)

    def test_vague_unsolicited_message_remains_candidate(self):
        reasons = [
            "unsolicited private chat with no prior reply",
            "no username or mutual contact",
        ]
        score = watcher.apply_purpose_exemption(
            5, reasons, "Hi, can we talk?", 5
        )
        self.assertGreaterEqual(score, watcher.SCORE_THRESHOLD)


class RuntimeAndConfigurationTests(unittest.TestCase):
    def test_new_configuration_defaults_to_flag_only(self):
        config = configure.build_config(
            "123456", "0123456789abcdef0123456789abcdef", "+15555550123"
        )
        self.assertEqual(config["action_mode"], "flag")
        self.assertEqual(config["allowlist_ids"], [])
        self.assertEqual(config["api_id"], 123456)

    def test_configuration_rejects_invalid_credentials(self):
        with self.assertRaises(ValueError):
            configure.validate_api_id("not-a-number")
        with self.assertRaises(ValueError):
            configure.validate_api_hash("replace-me")
        with self.assertRaises(ValueError):
            configure.validate_phone("555-0100")

    def test_configuration_is_written_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "private", "config.json")
            config = configure.build_config(
                123456, "0123456789abcdef0123456789abcdef", "+15555550123"
            )
            configure.write_config(path, config)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with open(path) as handle:
                self.assertEqual(json.load(handle), config)

    def test_contact_ids_use_raw_telethon_result(self):
        ids = asyncio.run(watcher.get_contact_ids(FakeClient()))
        self.assertEqual(ids, {101, 202})

    def test_unchanged_dialog_does_not_need_message_fetch(self):
        dialog = SimpleNamespace(message=SimpleNamespace(id=50))
        self.assertFalse(watcher.dialog_has_new_messages(dialog, 50))
        self.assertTrue(watcher.dialog_has_new_messages(dialog, 49))
        self.assertTrue(watcher.dialog_has_new_messages(dialog, 0))

    def test_secure_runtime_files_locks_down_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "BASE": temp_dir,
                "CONFIG": os.path.join(temp_dir, "config.json"),
                "STATE": os.path.join(temp_dir, "state.json"),
                "SESSION": os.path.join(temp_dir, "session"),
            }
            originals = {name: getattr(watcher, name) for name in paths}
            try:
                for name, value in paths.items():
                    setattr(watcher, name, value)
                for path in (
                    paths["CONFIG"], paths["STATE"], f'{paths["SESSION"]}.session'
                ):
                    with open(path, "w") as file:
                        file.write("test")
                    os.chmod(path, 0o644)
                watcher.secure_runtime_files()
                self.assertEqual(os.stat(temp_dir).st_mode & 0o777, 0o700)
                self.assertEqual(os.stat(paths["CONFIG"]).st_mode & 0o777, 0o600)
                self.assertEqual(
                    os.stat(f'{paths["SESSION"]}.session').st_mode & 0o777,
                    0o600,
                )
            finally:
                for name, value in originals.items():
                    setattr(watcher, name, value)

    def test_long_reports_are_chunked_without_losing_candidates(self):
        candidates = []
        for index in range(20):
            candidates.append({
                "from": f"sender-{index}",
                "chat": "chat",
                "date": "2026-10-26 12:00",
                "score": 4,
                "reasons": "test",
                "excerpt": "x" * 150,
                "ref": f"1:{index}",
                "auto_action": False,
            })
        reports = watcher.build_reports(candidates)
        self.assertGreater(len(reports), 1)
        self.assertTrue(all(len(report) <= watcher.REPORT_CHUNK_LIMIT for report in reports))
        combined = "\n".join(reports)
        for index in range(20):
            self.assertIn(f"ref 1:{index}", combined)

    def test_auto_action_only_for_unanswered_private_chat(self):
        entity = SimpleNamespace(bot=False, verified=False)
        private = SimpleNamespace(is_user=True, entity=entity)
        group = SimpleNamespace(is_user=False, entity=entity)
        self.assertTrue(
            watcher.should_auto_action("block_delete", private, 4)
        )
        self.assertFalse(watcher.should_auto_action("flag", private, 4))
        self.assertFalse(
            watcher.should_auto_action("block_delete", private, 0)
        )
        self.assertFalse(
            watcher.should_auto_action("block_delete", group, 4)
        )

    def test_action_receipt_records_both_operations(self):
        receipt = watcher.build_action_receipt([{
            "label": "Spammer",
            "blocked": True,
            "deleted": True,
            "errors": [],
        }])
        self.assertIn("Spammer: blocked, chat deleted locally", receipt)

    def test_block_and_delete_uses_local_only_deletion(self):
        client = FakeActionClient()
        entity = SimpleNamespace(id=123)
        result = asyncio.run(watcher.block_and_delete(client, entity))
        self.assertTrue(result["blocked"])
        self.assertTrue(result["deleted"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(client.block_requests), 1)
        self.assertEqual(client.deleted, [(entity, False)])


if __name__ == "__main__":
    unittest.main()
