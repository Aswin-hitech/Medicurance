from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}
ALLOWED_MIMES = {"application/pdf", "image/png", "image/jpeg"}

try:
    import magic  # type: ignore
except Exception:  # pragma: no cover
    magic = None


def allowed_file(filename):
    """Check if the file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _detect_mime_from_buffer(buffer):
    if magic is not None:
        try:
            return magic.from_buffer(buffer, mime=True)
        except Exception:
            pass

    if buffer.startswith(b"%PDF"):
        return "application/pdf"
    if buffer.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if buffer.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def validate_file_upload(file):
    """
    Perform security validation on uploaded files using content signatures.
    Returns (is_valid, error_message, sanitized_filename)
    """
    if not file:
        return False, "No file uploaded", None

    if file.filename == "":
        return False, "Empty filename", None

    if not allowed_file(file.filename):
        return False, f"Invalid file format. Supported: {', '.join(sorted(ALLOWED_EXTENSIONS))}", None

    try:
        header = file.read(2048)
        file.seek(0)
        mime = _detect_mime_from_buffer(header)
        if mime not in ALLOWED_MIMES:
            return False, "Invalid file content detected.", None
    except Exception:
        file.seek(0)
        return False, "Unable to validate uploaded file content.", None

    filename = secure_filename(file.filename)
    return True, None, filename
