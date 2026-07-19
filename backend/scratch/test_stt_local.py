import base64
import requests

# A tiny valid webm audio file (Opus)
# Let's just create a dummy file. Vachana might reject dummy files.
webm_b64 = "GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGbgQREVMAXkKq4EIVlOndqtBEIGbgQxWgecBAgAAABBBzDqawE6jAAExAAGvAW86H70DtwEQgZgJgQAAAIH1gAAAgfsEAAAABUu57wD1EQK4ERsJ0QEQZwsA/p4B94EQV4ECQsqBAlh2gQJUeIECTniBAkZrgQIAAAABU7uBAX9wA1i5iQEDAAJ5iQEHAAJ1iQMBAAh1iQMBAAhyhwAAAAAER3KDAAAAAD1xhoEBLI1PcHVzaEQDBABAAABBV4EEQruBBFh2gQJUeIEEToEBVniBAlZ0gQI="
webm_bytes = base64.b64decode(webm_b64)

url_stt = "http://127.0.0.1:5000/api/chat/voice"
files = {"audio": ("test.webm", webm_bytes, "audio/webm")}
data = {"conversation_id": "test-123", "language": "en-IN"}

try:
    res = requests.post(url_stt, files=files, data=data, timeout=30)
    print("Local STT Status:", res.status_code)
    print("Response:", res.text)
except Exception as e:
    print("Error:", e)
