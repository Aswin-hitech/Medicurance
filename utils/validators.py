def validate_mobile(mobile):
    if not mobile:
        return False
    mobile = str(mobile).strip()
    if len(mobile) != 10:
        return False

    if not mobile.isdigit():
        return False

    return True