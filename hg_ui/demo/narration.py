"""Voice-over. Kokoro (an open-source on-device TTS model) reads each line; durations drive the video timing."""
import json, os, sys
SCENES = [
 ("intro",   "Meet Quicksilver. The notebook that thinks with you, and never sends a word anywhere."),
 ("write",   "Real notes are messy. A meeting, an invoice, a birthday, a plumber, all in one breathless line. Just write it."),
 ("magic",   "Quicksilver reads it, on your phone, and turns the chaos into a clean note. Sections. Tasks. Every date, set as a reminder. You review it, and keep your own words if you prefer."),
 ("find",    "Looking for something? Type one word, and your notes answer instantly."),
 ("ask",     "Or half remember an idea. Ask in plain words, and it brings the thought back, with the exact note it came from."),
 ("bills",   "Bills arrive as messages. Quicksilver reads them on your phone and reminds you before they are due. Mark one paid, or add your own, and the message is deleted the moment it is settled."),
 ("brief",   "What needs you shows up on your home screen, and a tap opens the exact note or bill."),
 ("capture", "Speak a voice note, even in Telugu. Snap a photo. Sketch an idea. It listens, reads, and remembers."),
 ("themes",  "And make it yours. Cartoon. Material. Ocean. Midnight."),
 ("outro",   "Everything runs on your phone. No cloud, no account. Quicksilver. Notes that move, join, and never stain."),
]
if __name__ == "__main__":
    import soundfile as sf
    from kokoro_onnx import Kokoro
    d = os.environ.get("KOKORO_DIR", "/tmp/tts"); out = sys.argv[1]; os.makedirs(out, exist_ok=True)
    k = Kokoro(os.path.join(d, "kokoro.onnx"), os.path.join(d, "voices.bin")); info = {}
    for sid, text in SCENES:
        s, sr = k.create(text, voice="af_heart", speed=0.96, lang="en-us")
        sf.write(os.path.join(out, sid + ".wav"), s, sr); info[sid] = {"dur": len(s) / sr, "sr": sr, "text": text}
        print(sid, round(len(s) / sr, 2))
    json.dump(info, open(os.path.join(out, "info.json"), "w"), indent=1)
