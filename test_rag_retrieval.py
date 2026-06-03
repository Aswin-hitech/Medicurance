from services.rag_service import retrieve_rules


QUERIES = [
    "Is cataract surgery reimbursable?",
    "What documents are required for medical reimbursement?",
    "Maximum reimbursement amount",
]


def main():
    for query in QUERIES:
        print(f"\nQuery: {query}")
        results = retrieve_rules(query, k=5)
        if not results:
            print("No results returned. Build the vector database with build_vector.py.")
            continue
        for index, result in enumerate(results, start=1):
            print(f"{index}. {result['source_document']} | confidence={result['confidence']}")
            print(result["matched_rule"][:500].replace("\n", " "))


if __name__ == "__main__":
    main()
