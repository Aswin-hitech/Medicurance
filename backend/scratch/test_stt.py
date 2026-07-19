import requests

url = "https://api.vachana.ai/api/v1/stt"
headers = {
    "X-API-Key-ID": "vach_1ytE2CY5X2P5oVwy4zmu9S8YwKKSIHM7Xg23ihGb19af3xACoSCsut2Ci22CE7m4f9r19CPLgKP5R1MQA239s8noijZ1F8RE_c9308216ac9b644e07e4e5689783de28"
}
audio_bytes = b'RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00'
files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
data = {"lang": "en-IN"}

try:
    response = requests.post(url, headers=headers, files=files, data=data)
    print("REST Response:", response.status_code, response.text)
except Exception as e:
    print("REST Error:", e)
