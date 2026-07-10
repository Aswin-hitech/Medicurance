from __future__ import annotations

import unittest
from unittest.mock import patch

from memory.claim_memory import ClaimMemory
from utils.jwt_utils import decode_token, issue_auth_tokens, is_token_revoked, revoke_refresh_token


class Phase2SecurityTests(unittest.TestCase):
    def test_jwt_round_trip(self):
        tokens = issue_auth_tokens("claimant-1", "user", extra={"mobile": "9999999999"})
        payload = decode_token(tokens["access_token"])
        self.assertEqual(payload["sub"], "claimant-1")
        self.assertEqual(payload["role"], "user")
        self.assertEqual(payload["type"], "access")

    def test_token_revocation_hook(self):
        tokens = issue_auth_tokens("claimant-2", "user")
        with patch("utils.jwt_utils.token_revocations_collection.find_one", return_value={"jti_hash": "revoked"}):
            self.assertTrue(is_token_revoked(tokens["access_token"]))

    def test_claim_memory_trace(self):
        memory = ClaimMemory(
            claim_id="claim-1",
            mobile="9999999999",
            name="Test User",
            hospital="Test Hospital",
            amount=1000.0,
        )
        memory.add_trace(
            agent="ocr",
            status="finished",
            duration_ms=12.5,
            confidence=0.91,
            retries=1,
            summary="OCR complete",
            execution_id="exec-1",
        )
        state = memory.to_state()
        self.assertEqual(state["agent_trace"][0]["agent"], "ocr")
        self.assertEqual(state["agent_trace"][0]["execution_id"], "exec-1")


if __name__ == "__main__":
    unittest.main()

