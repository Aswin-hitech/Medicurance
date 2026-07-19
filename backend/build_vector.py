from services.rag_service import build_vector_db


def main():
    print("Building FAISS vector database from annexure PDFs...")
    ok = build_vector_db()
    if ok:
        print("Vector database created at data/vector_store/.")
    else:
        print("Vector database build failed or was skipped. Check logs and installed packages.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
