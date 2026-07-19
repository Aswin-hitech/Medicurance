import re


def validate_password_policy(password):
    """
    Enforce the production password policy used by registration, reset,
    and officer account creation paths.
    """
    password = str(password or "")
    checks = [
        (len(password) >= 8, "Password must be at least 8 characters long."),
        (re.search(r"[A-Z]", password), "Password must include an uppercase letter."),
        (re.search(r"[a-z]", password), "Password must include a lowercase letter."),
        (re.search(r"\d", password), "Password must include a number."),
        (re.search(r"[^A-Za-z0-9]", password), "Password must include a special character."),
    ]
    errors = [message for ok, message in checks if not ok]
    return not errors, errors
