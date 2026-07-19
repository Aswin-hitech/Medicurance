from services import trust_service


class _Claims:
    def count_documents(self, _query):
        return 0


class _Users:
    def find_one(self, _query):
        return {"mobile": "9999999999", "is_government_employee": True}


def test_verified_network_hospital_receives_weighted_trust(monkeypatch):
    monkeypatch.setattr(trust_service, "repo_verify_hospital", lambda _name: {"exists": True, "network": True})
    monkeypatch.setattr(trust_service, "claims_collection", _Claims())
    monkeypatch.setattr(trust_service, "users_collection", _Users())

    result = trust_service.calculate_trust_score(
        mobile="9999999999",
        hospital_name="Govt Hospital",
        ai_confidence=0.9,
        ocr_confidence=0.8,
        image_hash="hash",
        duplicate_result={"duplicate_probability": 0},
    )

    assert result["components"]["hospital_trust"] == 95.0
    assert result["contributions"]["hospital_trust"] == 19.0
    assert result["score"] >= 90.0


def test_legacy_verified_schema_is_accepted(monkeypatch):
    monkeypatch.setattr(trust_service, "repo_verify_hospital", lambda _name: {"verified": True, "network": True})
    result = trust_service.verify_hospital("Legacy Hospital")
    assert result["exists"] is True
    assert result["network"] is True
