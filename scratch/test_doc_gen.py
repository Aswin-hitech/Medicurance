import os
import sys
from pathlib import Path

# Add root directory to path
root_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(root_dir))

# Mock settings environment variables
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

from services.government_application_generator import (
    generate_pdf_application,
    generate_docx_application,
    generate_html_application
)

mock_data = {
    "claim_number": "CLM-2026-999999",
    "beneficiary_name": "John Doe",
    "ppo_number": "PPO-1234567",
    "aadhaar_number": "1234-5678-9012",
    "mobile_number": "9876543210",
    "email_address": "johndoe@example.com",
    "dob": "1960-05-15",
    "retirement_date": "2020-05-31",
    "pension_category": "Civil Pensioner",
    "department": "Education Department",
    "designation": "Headmaster",
    "treasury_office": "Chennai Central Treasury",
    "address": "No. 42, Pensioners Avenue, Chennai - 600001",
    "name": "Jane Doe",
    "relationship": "Spouse",
    "disease": "Cardiac Ailment",
    "diagnosis": "Severe Angina",
    "surgery_type": "Angioplasty with Stent",
    "treatment_category": "Surgical",
    "admission_date": "2026-07-01",
    "discharge_date": "2026-07-05",
    "doctor_name": "Dr. A. K. Smith",
    "hospital": "Apollo Hospitals, Chennai",
    "claim_type": "Reimbursement",
    "amount": "150000.00",
    "prev_claim_count": "1",
    "prev_claim_ref": "CLM-2025-111111",
    "emergency_case": "No",
    "treatment_period": "4 Days",
    "bank_name": "State Bank of India",
    "branch": "Adyar Branch",
    "account_number": "100012345678",
    "ifsc": "SBIN0001234",
    "micr": "600002015",
    "account_type": "Savings",
    "ecs_enabled": "Yes",
    "signature_name": "John Doe",
    "signature_date": "2026-07-10",
    "prescriptions": True,
    "discharge_summary": True
}

def main():
    print("Starting document generation tests...")
    
    # Create scratch output folder
    output_dir = Path(__file__).resolve().parent / "output"
    output_dir.mkdir(exist_ok=True)
    
    pdf_path = output_dir / "test_application.pdf"
    docx_path = output_dir / "test_application.docx"
    html_path = output_dir / "test_application.html"

    print("1. Testing PDF generation...")
    pdf_success = generate_pdf_application(mock_data, None, str(pdf_path))
    print(f"   PDF Success: {pdf_success} | File: {pdf_path}")
    assert pdf_success, "PDF generation failed"

    print("2. Testing DOCX generation...")
    docx_success = generate_docx_application(mock_data, None, str(docx_path))
    print(f"   DOCX Success: {docx_success} | File: {docx_path}")
    assert docx_success, "DOCX generation failed"

    print("3. Testing HTML generation...")
    html_success = generate_html_application(mock_data, None, str(html_path))
    print(f"   HTML Success: {html_success} | File: {html_path}")
    assert html_success, "HTML generation failed"

    print("All document generation tests passed successfully!")

if __name__ == "__main__":
    main()
