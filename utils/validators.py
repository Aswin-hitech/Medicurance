def validate_mobile(mobile):

    if len(mobile) != 10:
        return False

    if not mobile.isdigit():
        return False

    return True