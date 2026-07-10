class DocumentRepository:
    def __init__(self, db):
        self.db = db

    def save_document(self, data):
        self.db["documents"].insert_one(data)

    def get_documents(self, claim_id):
        return list(
            self.db["documents"].find({"claim_id": claim_id})
        )