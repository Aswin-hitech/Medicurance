import requests
import json
import base64

def test_tts():
    api_url = "https://api.vachana.ai/api/v1/tts/sse"
    headers = {
        "X-API-Key-ID": "vach_1ytE2CY5X2P5oVwy4zmu9S8YwKKSIHM7Xg23ihGb19af3xACoSCsut2Ci22CE7m4f9r19CPLgKP5R1MQA239s8noijZ1F8RE_c9308216ac9b644e07e4e5689783de28",
        "Content-Type": "application/json"
    }
    
    data = {
        "text": "Hello, how are you?",
        "voice": "Pranav",
        "model": "vachana-voice-v3",
        "audio_config": {
            "sample_rate": 44100,
            "encoding": "linear_pcm",
            "container": "wav",
            "num_channels": 1,
            "sample_width": 2
        }
    }
    
    try:
        response = requests.post(api_url, headers=headers, json=data, stream=True, timeout=30)
        print("Status:", response.status_code)
        audio_bytes = bytearray()
        for i, line in enumerate(response.iter_lines()):
            if line:
                line_str = line.decode('utf-8')
                print(f"Line {i}:", line_str[:150])
                if line_str.startswith("data: "):
                    payload = line_str[6:].strip()
                    if payload:
                        try:
                            chunk_data = json.loads(payload)
                            if "audio" in chunk_data:
                                audio_bytes.extend(base64.b64decode(chunk_data["audio"]))
                        except json.JSONDecodeError:
                            # Maybe it's just raw base64?
                            try:
                                audio_bytes.extend(base64.b64decode(payload))
                                print("Successfully decoded raw base64")
                            except:
                                print("Failed to decode payload:", payload[:50])
        print("Total audio bytes:", len(audio_bytes))
    except Exception as e:
        print("Error:", e)

test_tts()
