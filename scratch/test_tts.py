import requests
import json
import base64

url = "https://api.vachana.ai/api/v1/tts/sse"
headers = {
    "Content-Type": "application/json",
    "X-API-Key-ID": "vach_1ytE2CY5X2P5oVwy4zmu9S8YwKKSIHM7Xg23ihGb19af3xACoSCsut2Ci22CE7m4f9r19CPLgKP5R1MQA239s8noijZ1F8RE_c9308216ac9b644e07e4e5689783de28"
}
payload = {
  "text": "Hello, how are you?",
  "voice": "Pranav",
  "model": "vachana-voice-v3",
  "audio_config": {
    "sample_rate": 44100,
    "encoding": "linear_pcm",
    "container": "wav"
  }
}

try:
    response = requests.post(url, headers=headers, json=payload, stream=True)
    print("Status Code:", response.status_code)
    print("Headers:", dict(response.headers))
    for i, line in enumerate(response.iter_lines()):
        if line:
            print("Line:", line[:100], "...")
        if i > 5:
            break
except Exception as e:
    print("Error:", e)
