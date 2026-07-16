import base64
import sys
import os

sys.path.append(os.path.abspath('.'))
from services.gnani_service import transcribe_audio

webm_b64 = "GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGbgQREVMAXkKq4EIVlOndqtBEIGbgQxWgecBAgAAABBBzDqawE6jAAExAAGvAW86H70DtwEQgZgJgQAAAIH1gAAAgfsEAAAABUu57wD1EQK4ERsJ0QEQZwsA/p4B94EQV4ECQsqBAlh2gQJUeIECTniBAkZrgQIAAAABU7uBAX9wA1i5iQEDAAJ5iQEHAAJ1iQMBAAh1iQMBAAhyhwAAAAAER3KDAAAAAD1xhoEBLI1PcHVzaEQDBABAAABBV4EEQruBBFh2gQJUeIEEToEBVniBAlZ0gQI="
webm_bytes = base64.b64decode(webm_b64)

res = transcribe_audio(webm_bytes, lang="en-IN")
print("Transcript:", res)
