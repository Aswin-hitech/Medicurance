import bcrypt
from datetime import datetime, timezone
from database.mongo_client import admins_collection

def create_admin(email, password, name, role="admin"):
    """
    Creates a new administrator with a bcrypt-hashed password.
    """
    if not email or not password:
        raise ValueError("Email and password are required")
        
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    admin_data = {
        "email": email.strip().lower(),
        "password": hashed,
        "name": name.strip(),
        "role": role,
        "status": "Active",
        "account_status": "Active",
        "is_disabled": False,
        "is_deleted": False,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    result = admins_collection.insert_one(admin_data)
    return result.inserted_id

def get_admin_by_email(email):
    """
    Fetches an admin by email.
    """
    if not email:
        return None
    return admins_collection.find_one({"email": email.strip().lower()})

def check_admin_password(admin_doc, password):
    """
    Checks if the password matches the hashed password stored in the admin document.
    """
    if not admin_doc or 'password' not in admin_doc:
        return False
        
    hashed_pwd = admin_doc['password']
    # If password is stored as string in Mongo (e.g. from json or migration), encode it
    if isinstance(hashed_pwd, str):
        hashed_pwd = hashed_pwd.encode('utf-8')
        
    return bcrypt.checkpw(password.encode('utf-8'), hashed_pwd)
