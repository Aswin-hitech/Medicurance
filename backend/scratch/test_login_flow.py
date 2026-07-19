import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set testing flags before app startup/imports
from app import app
app.config["WTF_CSRF_ENABLED"] = False
app.config["TESTING"] = True

from flask import session
from database.mongo_client import govt_collection, users_collection, officers_collection

def test_login_flow():
    print("Initializing Flask test client...")
    client = app.test_client()

    # Clear session/cookies to ensure we start unauthenticated
    with client.session_transaction() as sess:
        sess.clear()

    # Clear any cookies if possible
    client.cookie_jar.clear() if hasattr(client.cookie_jar, 'clear') else None

    # Print a sample document from govtlist to inspect its keys
    print("\nChecking govtlist database sample...")
    sample_govt = govt_collection.find_one()
    if sample_govt:
        print("Govtlist sample keys:", list(sample_govt.keys()))
        # Print nested fields
        auth_data = sample_govt.get("auth") or {}
        pension_data = sample_govt.get("pension") or {}
        print("Auth keys:", list(auth_data.keys()))
        print("Pension keys:", list(pension_data.keys()))
        
        ppo = (
            sample_govt.get("ppo_number") or 
            sample_govt.get("ppoNumber") or 
            auth_data.get("ppoNumber") or 
            auth_data.get("ppo_number") or 
            pension_data.get("ppoNumber") or 
            pension_data.get("ppo_number")
        )
        phone = (
            sample_govt.get("phone") or 
            sample_govt.get("mobile") or 
            auth_data.get("phone") or 
            auth_data.get("mobile")
        )
        print(f"Extracted PPO: {ppo}, Extracted Phone: {phone}")
    else:
        print("No records found in govtlist collection!")
        ppo = None
        phone = None

    print("\n1. Testing GET /login page...")
    res = client.get("/login")
    print(f"Status: {res.status_code}")
    assert res.status_code in (200, 302), f"Login page failed to load (returned {res.status_code})"
    print("GET /login passed!")

    # Find a real pensioner record from database for testing PPO OTP flow
    if ppo and phone:
        print("\n2. Testing POST /send_otp for Pensioner...")
        res = client.post("/send_otp", data={
            "ppo_number": ppo,
            "mobile_number": phone
        })
        print(f"Status: {res.status_code}")
        if res.status_code != 200:
            print("Response Data:", res.data.decode('utf-8', errors='ignore')[:1000])
        assert res.status_code == 200, f"Send OTP request failed with status {res.status_code}"
        print("POST /send_otp pensioner test passed!")
    else:
        print("Skipping pensioner OTP POST test (no pensioner data found).")

    # Find an officer record for testing Officer OTP flow
    print("\n3. Querying an officer record from database...")
    officer = officers_collection.find_one({"officer_id": {"$exists": True}})
    if officer:
        officer_id = officer.get("officer_id")
        phone = officer.get("phone") or officer.get("mobile") or officer.get("auth", {}).get("phone")
        print(f"Found officer ID: {officer_id}, Phone: {phone}")
        
        print("\n4. Testing POST /send_otp for Officer...")
        res = client.post("/send_otp", data={
            "officer_id": officer_id,
            "mobile_number": phone
        })
        print(f"Status: {res.status_code}")
        if res.status_code != 200:
            print("Response Data:", res.data.decode('utf-8', errors='ignore')[:1000])
        assert res.status_code == 200, f"Send OTP for officer failed with status {res.status_code}"
        print("POST /send_otp officer test passed!")
    else:
        print("Skipping officer OTP POST test (no officer found in DB).")

    print("\nAll login flow tests completed successfully!")

if __name__ == "__main__":
    try:
        test_login_flow()
    except AssertionError as ae:
        print(f"\nTEST FAILED: {ae}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR OCCURRED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
