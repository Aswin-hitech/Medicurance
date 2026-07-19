import requests
import urllib.request

api_url = "https://api.vachana.ai/stt/v3"
headers = {"X-API-Key-ID": "vach_1ytE2CY5X2P5oVwy4zmu9S8YwKKSIHM7Xg23ihGb19af3xACoSCsut2Ci22CE7m4f9r19CPLgKP5R1MQA239s8noijZ1F8RE_c9308216ac9b644e07e4e5689783de28"}

# Download a tiny WebM (we'll use a video file and just pass it to audio stt)
urllib.request.urlretrieve("https://upload.wikimedia.org/wikipedia/commons/4/4d/Lophius-piscatorius-1536.webm", "test.webm")

with open("test.webm", "rb") as f:
    # Read just a small chunk to not upload a huge video
    data_bytes = f.read(500000)

with open("test_small.webm", "wb") as f2:
    f2.write(data_bytes)

with open("test_small.webm", "rb") as f3:
    files = {"audio_file": ("recording.webm", f3, "audio/webm")}
    data = {"language_code": "en-IN"}
    res = requests.post(api_url, headers=headers, files=files, data=data)
    print("WebM Test:", res.status_code, res.text)
