from database.mongo_client import documents_collection


def save_document(data):

    documents_collection.insert_one(data)


def get_documents(claim_id):

    return list(
        documents_collection.find({"claim_id": claim_id})
    )