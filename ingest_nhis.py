from services.nhis_ingestion_service import ingest_nhis_policy_data


def main():
    result = ingest_nhis_policy_data()
    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
