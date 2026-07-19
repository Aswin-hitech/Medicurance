import requests
import io
import wave
import struct

def test_flask():
    url_tts = "http://127.0.0.1:5000/api/chat/tts"
    url_stt = "http://127.0.0.1:5000/api/chat/voice"

    print("--- Testing Flask TTS ---")
    try:
        res = requests.post(url_tts, json={"text": "Hello world", "language": "en-IN"}, timeout=10)
        print("TTS Status:", res.status_code)
        print("TTS Bytes:", len(res.content))
    except Exception as e:
        print("TTS Error:", e)

    print("\n--- Testing Flask Voice (STT + Chat) ---")
    try:
        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(struct.pack('<h', 0) * 16000) # 1 sec of silence
            
        files = {"audio": ("test.wav", wav_io.getvalue(), "audio/wav")}
        data = {"conversation_id": "test-123", "language": "en-IN"}
        res = requests.post(url_stt, files=files, data=data, timeout=30)
        print("Voice Status:", res.status_code)
        try:
            print("Voice Response:", res.json())
        except:
            print("Voice Text:", res.text[:200])
    except Exception as e:
        print("Voice Error:", e)

test_flask()
