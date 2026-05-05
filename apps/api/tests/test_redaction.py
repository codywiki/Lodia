import unittest

from lodia.redaction import redact_text


class RedactionTests(unittest.TestCase):
    def test_redacts_common_pii_and_secrets(self):
        text = "联系张三 phone 13800138000 email user@example.com key sk-abcdefghijklmnopqrstuvwxyz"
        result = redact_text(text)

        self.assertTrue(result.passed)
        self.assertNotIn("13800138000", result.redacted_text)
        self.assertNotIn("user@example.com", result.redacted_text)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", result.redacted_text)
        self.assertIn("[PHONE_1]", result.redacted_text)
        self.assertIn("[EMAIL_1]", result.redacted_text)
        self.assertIn("[SECRET_1]", result.redacted_text)
        self.assertEqual(result.residual_findings, [])

    def test_strips_url_query_tokens(self):
        result = redact_text("打开 https://example.com/callback?token=secret&user=a 后继续")

        self.assertNotIn("token=secret", result.redacted_text)
        self.assertIn("https://example.com/callback", result.redacted_text)


if __name__ == "__main__":
    unittest.main()
