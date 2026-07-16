import sys
import os
sys.path.append(os.path.abspath('.'))

from services.gnani_service import text_to_speech

print("Testing text_to_speech...")
res = text_to_speech("Hello, how are you?")
print("Result type:", type(res))
print("Result length:", len(res))
if len(res) > 44:
    print("Header:", res[:44])
