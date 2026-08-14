import hashlib
import hmac
import unittest

from services.whatsapp import (
    clean_agent_reply,
    extract_incoming_messages,
    is_sender_allowed,
    pseudonymous_sender_key,
    split_agent_reply,
    verify_webhook_signature,
)


class WhatsAppServiceTests(unittest.TestCase):
    def test_verifies_meta_signature(self):
        body = b'{"object":"whatsapp_business_account"}'
        secret = "test-secret"
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        self.assertTrue(
            verify_webhook_signature(body, f"sha256={digest}", secret)
        )
        self.assertFalse(
            verify_webhook_signature(body + b"x", f"sha256={digest}", secret)
        )

    def test_extracts_text_message_and_profile(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "field": "messages",
                    "value": {
                        "contacts": [{
                            "wa_id": "94770000000",
                            "profile": {"name": "Test User"},
                        }],
                        "messages": [{
                            "from": "+94 77 000 0000",
                            "id": "wamid.test",
                            "type": "text",
                            "text": {"body": "Hello Workmate"},
                        }],
                    },
                }],
            }],
        }

        messages = extract_incoming_messages(payload)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].sender_id, "94770000000")
        self.assertEqual(messages[0].profile_name, "Test User")
        self.assertEqual(messages[0].text, "Hello Workmate")

    def test_allowlist_is_optional_and_normalized(self):
        self.assertTrue(is_sender_allowed("94770000000", ""))
        self.assertTrue(
            is_sender_allowed("94770000000", "+94 77 000 0000,+94771111111")
        )
        self.assertFalse(is_sender_allowed("94770000000", "+94771111111"))

    def test_sender_key_is_stable_and_does_not_contain_phone(self):
        first = pseudonymous_sender_key("+94 77 000 0000", "secret")
        second = pseudonymous_sender_key("94770000000", "secret")
        self.assertEqual(first, second)
        self.assertNotIn("94770000000", first)

    def test_cleans_frontend_contract_and_splits_long_reply(self):
        text = (
            "Answer\n\n[[EVIDENCE_JSON]]{\"items\": []}[[/EVIDENCE_JSON]]"
            "[RENDER_ENTERPRISE_FORM]"
        )
        self.assertEqual(clean_agent_reply(text), "Answer")

        chunks = split_agent_reply("word " * 100, limit=120)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
