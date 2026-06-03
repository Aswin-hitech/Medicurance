import pandas as pd
import re
from database.govt_repository import (
    get_employee_by_employee_id,
    get_employee_by_mobile,
    get_employee_by_email,
    get_employee_by_aadhaar,
)

def validate_and_preview_csv(file_path):
    """
    Parses a CSV file using Pandas, validates fields, detects duplicates,
    and returns a summary, preview list, and clean records ready for import.
    """
    try:
        # Load the CSV, convert all entries to strings to ensure safe parsing
        df = pd.read_csv(file_path, dtype=str)
        # Fill NaN values with empty string
        df = df.fillna('')
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to parse CSV file: {str(e)}",
            "headers": [],
            "preview_rows": [],
            "clean_records": [],
            "errors": [{"row": 0, "field": "File", "message": str(e)}],
            "stats": {"total": 0, "valid": 0, "invalid": 0}
        }

    required_cols = ['aadhaar_number', 'employee_id', 'name', 'phone_number', 'email', 'department', 'designation']
    
    # Check for missing required column names
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        return {
            "success": False,
            "message": f"CSV is missing required columns: {', '.join(missing_cols)}",
            "headers": list(df.columns),
            "preview_rows": [],
            "clean_records": [],
            "errors": [{"row": 0, "field": "Columns", "message": f"Missing columns: {missing_cols}"}],
            "stats": {"total": len(df), "valid": 0, "invalid": len(df)}
        }

    preview_rows = []
    clean_records = []
    errors_list = []
    
    # To track duplicates in the current uploaded file
    seen_ids = set()
    seen_phones = set()
    seen_emails = set()
    seen_aadhaars = set()

    email_regex = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

    for index, row in df.iterrows():
        row_num = index + 1
        row_dict = row.to_dict()
        row_errors = []
        
        # 1. Clean primary identifiers
        emp_id = str(row_dict.get('employee_id', '')).strip()
        aadhaar = re.sub(r"\D", "", str(row_dict.get('aadhaar_number', '')).strip())
        name = str(row_dict.get('name', '')).strip()
        phone = str(row_dict.get('phone_number', '')).strip()
        email = str(row_dict.get('email', '')).strip()
        dept = str(row_dict.get('department', '')).strip()
        desg = str(row_dict.get('designation', '')).strip()

        # 2. Check for missing required fields
        if not aadhaar:
            row_errors.append("Aadhaar number is missing")
        if not emp_id:
            row_errors.append("Employee ID is missing")
        if aadhaar and (not aadhaar.isdigit() or len(aadhaar) != 12):
            row_errors.append(f"Invalid Aadhaar number format: '{aadhaar}'")
        if not name:
            row_errors.append("Name is missing")
        if not phone:
            row_errors.append("Phone number is missing")
        if not email:
            row_errors.append("Email is missing")
        if not dept:
            row_errors.append("Department is missing")
        if not desg:
            row_errors.append("Designation is missing")

        # 3. Email Format Validation
        if email and not email_regex.match(email):
            row_errors.append(f"Invalid email format: '{email}'")

        # 4. Phone Number Format (should be 10 digits for our system)
        if phone and (not phone.isdigit() or len(phone) < 10 or len(phone) > 15):
            row_errors.append(f"Invalid phone number format: '{phone}'")

        # 5. File Duplicate Detection
        if emp_id:
            if emp_id in seen_ids:
                row_errors.append(f"Duplicate Employee ID in CSV: '{emp_id}'")
            else:
                seen_ids.add(emp_id)

        if aadhaar:
            if aadhaar in seen_aadhaars:
                row_errors.append(f"Duplicate Aadhaar Number in CSV: '{aadhaar}'")
            else:
                seen_aadhaars.add(aadhaar)

        if phone:
            if phone in seen_phones:
                row_errors.append(f"Duplicate Phone Number in CSV: '{phone}'")
            else:
                seen_phones.add(phone)

        if email:
            if email in seen_emails:
                row_errors.append(f"Duplicate Email in CSV: '{email}'")
            else:
                seen_emails.add(email)

        # 6. Database Duplicate Detection (only if no errors so far to optimize DB calls)
        if not row_errors:
            if get_employee_by_aadhaar(aadhaar):
                row_errors.append(f"Aadhaar number already exists in system: '{aadhaar}'")
            if get_employee_by_employee_id(emp_id):
                row_errors.append(f"Employee ID already exists in system: '{emp_id}'")
            if get_employee_by_mobile(phone):
                row_errors.append(f"Phone number already exists in system: '{phone}'")
            if get_employee_by_email(email):
                row_errors.append(f"Email already exists in system: '{email}'")

        # Status and compilation
        is_valid = len(row_errors) == 0
        
        # Construct record for DB insert
        record = {
            "employee_id": emp_id,
            "aadhaar_number": aadhaar,
            "name": name,
            "gender": str(row_dict.get('gender', 'Male')).strip(),
            "age": int(row_dict.get('age', 35)) if str(row_dict.get('age', '')).isdigit() else 35,
            "date_of_birth": str(row_dict.get('date_of_birth', '')).strip(),
            "department": dept,
            "designation": desg,
            "employee_type": str(row_dict.get('employee_type', 'Regular')).strip(),
            "experience_years": int(row_dict.get('experience_years', 5)) if str(row_dict.get('experience_years', '')).isdigit() else 5,
            "date_of_joining": str(row_dict.get('date_of_joining', '')).strip(),
            "salary": float(row_dict.get('salary', 0)) if str(row_dict.get('salary', '')).replace('.', '', 1).isdigit() else 0.0,
            "blood_group": str(row_dict.get('blood_group', 'O+')).strip(),
            "marital_status": str(row_dict.get('marital_status', 'Single')).strip(),
            "address": str(row_dict.get('address', '')).strip(),
            "city": str(row_dict.get('city', '')).strip(),
            "district": str(row_dict.get('district', '')).strip(),
            "state": str(row_dict.get('state', '')).strip(),
            "pincode": str(row_dict.get('pincode', '')).strip(),
            "phone_number": phone,
            "email": email,
            "aadhaar_last4": aadhaar[-4:],
            "pan_last4": str(row_dict.get('pan_last4', 'A000')).strip()[-4:],
            "nominee_name": str(row_dict.get('nominee_name', '')).strip(),
            "relationship": str(row_dict.get('relationship', '')).strip(),
            "insurance_provider": str(row_dict.get('insurance_provider', 'MediCurance Govt Cover')).strip(),
            "policy_number": str(row_dict.get('policy_number', '')).strip(),
            "policy_start": str(row_dict.get('policy_start', '')).strip(),
            "policy_end": str(row_dict.get('policy_end', '')).strip(),
            "claim_eligibility": str(row_dict.get('claim_eligibility', 'True')).lower() in ['true', 'yes', '1', 'eligible'],
            "medical_history": str(row_dict.get('medical_history', '')).strip(),
            "emergency_contact": str(row_dict.get('emergency_contact', '')).strip(),
            "emergency_phone": str(row_dict.get('emergency_phone', '')).strip(),
            "bank_name": str(row_dict.get('bank_name', '')).strip(),
            "account_last4": str(row_dict.get('account_last4', '0000')).strip()[-4:],
            "ifsc_code": str(row_dict.get('ifsc_code', '')).strip()
        }
        
        preview_rows.append({
            "row_num": row_num,
            "aadhaar_number": aadhaar,
            "employee_id": emp_id,
            "name": name,
            "phone_number": phone,
            "email": email,
            "department": dept,
            "designation": desg,
            "is_valid": is_valid,
            "errors": ", ".join(row_errors)
        })

        if is_valid:
            clean_records.append(record)
        else:
            for err in row_errors:
                errors_list.append({
                    "row": row_num,
                    "employee_id": emp_id,
                    "field": "Validation",
                    "message": err
                })

    return {
        "success": True,
        "message": "CSV parsed successfully",
        "headers": ['row_num', 'aadhaar_number', 'employee_id', 'name', 'phone_number', 'email', 'department', 'designation', 'status', 'errors'],
        "preview_rows": preview_rows,
        "clean_records": clean_records,
        "errors": errors_list,
        "stats": {
            "total": len(df),
            "valid": len(clean_records),
            "invalid": len(df) - len(clean_records)
        }
    }
