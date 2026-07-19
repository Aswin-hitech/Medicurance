import sys
import os
import traceback

sys.path.append(os.path.abspath('.'))

from services.gnani_service import transcribe_audio, text_to_speech
from services.chat_service import process_chat_query
import logging

logging.basicConfig(level=logging.DEBUG)

def run_tests():
    print("--- TESTING TTS ---")
    try:
        tts_bytes = text_to_speech("This is a test of the TTS system.", lang="en-IN")
        print(f"TTS Success. Generated {len(tts_bytes)} bytes of audio data.")
        if len(tts_bytes) > 44:
            print("TTS Header:", tts_bytes[:44])
    except Exception as e:
        print("TTS Failed:")
        traceback.print_exc()

    print("\n--- TESTING CHAT ---")
    try:
        # Pass a mock conversation ID
        chat_res = process_chat_query("What is Medicurance?", "test-conv-123", lang="en-IN")
        print("Chat Success:")
        print("Answer:", chat_res.get("answer"))
    except Exception as e:
        print("Chat Failed:")
        traceback.print_exc()

    print("\n--- TESTING STT ---")
    try:
        # Mock a tiny WAV file for STT
        import wave
        import io
        import struct
        
        # Create a silent wav in memory
        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(struct.pack('<h', 0) * 16000) # 1 sec of silence
            
        stt_res = transcribe_audio(wav_io.getvalue(), lang="en-IN")
        print("STT Result:", stt_res)
    except Exception as e:
        print("STT Failed:")
        traceback.print_exc()

if __name__ == "__main__":
    run_tests()
