import os
import sys
from pathlib import Path

# Add root directory to path
root_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(root_dir))

# Mock settings environment variables
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

from services.ecard_generator import generate_ecard_assets

mock_profile = {
    "name": "Arulmozhi Varman",
    "beneficiary_name": "Arulmozhi Varman",
    "ppo_number": "PPO-9988776",
    "aadhaar_number": "9999-8888-7777",
    "mobile_number": "9000010000",
    "emergency_contact": "Kundavai Pirattiyar",
    "emergency_phone": "9000020000",
    "dob": "1958-10-12",
    "blood_group": "A+",
    "treasury_office": "Thanjavur Treasury Office",
    "address": {
        "doorNo": "Chola Palace",
        "street": "Raja Raja Street",
        "area": "Thanjavur Palace Ground",
        "district": "Thanjavur",
        "pincode": "613001"
    },
    "profilePhoto": "https://example.com/photo.png",
    "ecard_status": "ACTIVE"
}

def main():
    print("Starting e-Health Card generation test...")
    
    # We mock upload_to_storage in the test so it doesn't need to connect to Supabase
    import services.ecard_generator as eg
    
    # Keep track of generated local paths
    generated_files = []
    
    # We patch upload_to_storage to return a mock url and not call the actual api
    def mock_upload(local_path, folder=None, bucket_name=None, storage_path=None):
        filename = Path(local_path).name
        # Copy file to output directory so we can inspect it!
        output_dir = Path(__file__).resolve().parent / "output"
        output_dir.mkdir(exist_ok=True)
        dest_path = output_dir / filename
        import shutil
        shutil.copy(local_path, dest_path)
        print(f"   [Mock Upload] Copied local file to test output: {dest_path}")
        generated_files.append(str(dest_path))
        return f"https://mock-supabase.co/storage/v1/object/public/letters/ecards/{filename}"
        
    eg.upload_to_storage = mock_upload

    # Run generation
    assets = eg.generate_ecard_assets(mock_profile)
    
    print("\nGeneration Output Assets:")
    print(assets)
    
    assert assets is not None, "E-card asset generation failed"
    assert "front_url" in assets, "Front URL missing"
    assert "back_url" in assets, "Back URL missing"
    assert "pdf_url" in assets, "PDF URL missing"
    
    print("\nGenerated local files for inspection:")
    for f in generated_files:
        print(f" - {f}")
        
    print("\nE-card rendering test completed successfully!")

if __name__ == "__main__":
    main()
