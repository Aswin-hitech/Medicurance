from __future__ import annotations

import argparse
from pprint import pprint

from database.government_officer_repository import get_officer_by_identifier
from database.mongo_client import admins_collection, officers_collection, users_collection
from services.auth_service import (
    authenticate_admin,
    authenticate_officer,
    authenticate_user,
    fetch_user_role,
    revalidate_session_account,
)


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _digit_values(value):
    normalized = _digits(value)
    values = [normalized] if normalized else []
    try:
        values.append(int(normalized))
    except ValueError:
        pass
    return values


def _identifier_queries(identifier):
    raw = str(identifier or "").strip().lower()
    digits = _digits(raw)
    normalized = digits or raw
    if "@" in normalized:
        return {
            "admin": {"email": normalized},
            "officer": {"email": normalized},
            "user": {"email": normalized},
        }

    digit_values = _digit_values(normalized)
    identity_query = {
        "$or": [
            {"auth.ppoNumber": normalized},
            {"officer_id": normalized},
            {"phone": {"$in": digit_values}},
            {"mobile": {"$in": digit_values}},
            {"phone_number": {"$in": digit_values}},
        ]
    }
    return {
        "admin": {"email": normalized},
        "officer": identity_query,
        "user": {
            "$or": [
                {"mobile": normalized},
                {"phone": normalized},
                {"phone_number": normalized},
                {"mobile": {"$in": digit_values}},
                {"phone": {"$in": digit_values}},
                {"phone_number": {"$in": digit_values}},
                {"auth.ppoNumber": normalized},
                {"officer_id": normalized},
            ]
        },
    }


def _safe_summary(doc):
    if not doc:
        return None
    keys = [
        "_id",
        "officer_id",
        "ppo_number",
        "name",
        "email",
        "mobile",
        "phone",
        "phone_number",
        "role",
        "status",
        "account_status",
        "government_verified",
        "verification_completed",
        "is_verified",
    ]
    summary = {key: str(doc.get(key)) for key in keys if key in doc}
    summary["has_password"] = bool(doc.get("password"))
    summary["password_type"] = type(doc.get("password")).__name__ if doc.get("password") is not None else None
    return summary


def _print_lookup(name, collection, query):
    doc = collection.find_one(query)
    print(f"{collection.name}.find_one(")
    pprint(query)
    print(f") => {bool(doc)}")
    if doc:
        pprint(_safe_summary(doc))
    print()
    return doc


def main():
    parser = argparse.ArgumentParser(description="Trace MediCurance login lookup and authentication flow.")
    parser.add_argument("identifier", help="Mobile number, email, officer_id, or PPO number")
    parser.add_argument("--role", choices=["admin", "officer", "user"], default="officer")
    parser.add_argument("--password", default=None, help="Optional password to verify. Do not use on shared terminals.")
    args = parser.parse_args()

    queries = _identifier_queries(args.identifier)
    print("=== Raw Collection Queries ===")
    _print_lookup("admin", admins_collection, queries["admin"])
    _print_lookup("officer", officers_collection, queries["officer"])
    _print_lookup("user", users_collection, {**queries["user"], "role": args.role} if args.role != "user" else queries["user"])

    print("=== Repository Lookup ===")
    officer = get_officer_by_identifier(args.identifier)
    print("get_officer_by_identifier(identifier) =>", bool(officer))
    if officer:
        pprint(_safe_summary(officer))
    print()

    print("=== Role Resolution ===")
    resolved = fetch_user_role(args.identifier, preferred_role=args.role)
    pprint({key: resolved.get(key) for key in ("found", "role", "collection", "identifier")})
    print("Resolved document:")
    pprint(_safe_summary(resolved.get("document")))
    print()

    print("=== Password Verification ===")
    if args.password is None:
        print("Password Valid: not tested (--password not provided)")
    else:
        if args.role == "admin":
            auth_result = authenticate_admin(args.identifier, args.password)
        elif args.role == "officer":
            auth_result = authenticate_officer(args.identifier, args.password)
        else:
            auth_result = authenticate_user(args.identifier, args.password)
        pprint({key: auth_result.get(key) for key in ("ok", "reason", "role")})
        print("Password Valid:", bool(auth_result.get("ok")))
    print()

    print("=== Session Revalidation Simulation ===")
    validation = revalidate_session_account(args.identifier, args.role)
    pprint({key: validation.get(key) for key in ("ok", "reason", "role", "collection")})
    print()

    print("=== Expected Redirect ===")
    if args.role == "admin":
        print("Redirect: admin.dashboard")
    elif args.role == "officer":
        print("Redirect: officer.dashboard")
    else:
        print("Redirect: user.dashboard")


if __name__ == "__main__":
    main()
