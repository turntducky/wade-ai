import os
import re
import shutil
import hashlib
import logging
import tempfile
import urllib.request
import numpy as np

from pathlib import Path

try:
    import whisper
    import onnxruntime as ort
    import sounddevice as sd
    import openwakeword
    import openwakeword.utils
    from kokoro_onnx import Kokoro
    from openwakeword.model import Model

    ort.set_default_logger_severity(4)
except ImportError as _voice_import_err:
    raise ImportError(
        f"Voice dependencies are not installed ({_voice_import_err}). "
        "Run:  pip install wade-ai[voice]  or  pip install openai-whisper kokoro-onnx openwakeword onnxruntime sounddevice"
    ) from _voice_import_err

from app.core.config import VOICE_DIR
from app.core.hardware import probe_hardware

KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
WADE_WAKEWORD_URL = "https://raw.githubusercontent.com/turntducky/wade-ai/main/app/assets/wade.onnx"
INTERNAL_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

KNOWN_ASSET_HASHES: dict[str, str | None] = {
    "wade.onnx":        "b477180379c432f49043b860923d5b496832580ffd40cda137cacacc2ac9f06d",
    "kokoro-v1.0.onnx": None,
    "voices-v1.0.bin":  None,
}

def _verify_asset(path: Path, filename: str) -> None:
    """Raise RuntimeError if the file's SHA-256 does not match KNOWN_ASSET_HASHES."""
    expected = KNOWN_ASSET_HASHES.get(filename)
    if expected is None:
        return
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Hash mismatch for '{filename}': expected {expected[:16]}… got {digest[:16]}…  "
            "File deleted. Possible supply-chain tampering."
        )

logger = logging.getLogger("wade.voice")

class VoiceService:
    def __init__(self):
        self.hw = probe_hardware()
        self.primary = self.hw.get("primary", {})
        self.devices = self.hw.get("devices", [])
        self.backend = self.primary.get("backend", "cpu")
        self.vram = self.primary.get("memory_usable_gb", 0)
        self.npu_device = next((d for d in self.devices if d.get("kind") == "npu"), None)
        self.has_npu = self.npu_device is not None
        self.fs = 16000
        self.chunk_size = 1280 

        self._bootstrap_assets()
        
        logger.info("👂 Initializing Wake Word engine...")
        wade_model_path = VOICE_DIR / "wade.onnx"

        if not wade_model_path.exists():
            raise FileNotFoundError(f"Missing custom wake word model in {VOICE_DIR}")

        onnx_providers: list = ["CPUExecutionProvider"]
        
        available_providers = ort.get_available_providers()
        
        if self.has_npu:
            if "CoreMLExecutionProvider" in available_providers:
                onnx_providers.insert(0, "CoreMLExecutionProvider")
            elif "OpenVINOExecutionProvider" in available_providers:
                onnx_providers.insert(0, ("OpenVINOExecutionProvider", {"device_type": "NPU"}))
            elif "QNNExecutionProvider" in available_providers:
                onnx_providers.insert(0, "QNNExecutionProvider")
            
            npu_name = self.npu_device.get("name", "NPU") if self.npu_device else "NPU"
            logger.info(f"🚀 NPU Detected ({npu_name}). Offloading Voice/STT/TTS to save GPU for LLM.")
        
        if self.backend == "cuda" and not self.has_npu:
            available = ort.get_available_providers()
            onnx_providers = [p for p in ["CUDAExecutionProvider", "CPUExecutionProvider"] if p in available]

        try:
            self.oww_model = Model(
                wakeword_models=[str(wade_model_path)],
                inference_framework="onnx",
                inference_options={"providers": onnx_providers},
            )
        except TypeError:
            logger.warning(
                "openwakeword: inference_options not supported by this version — "
                "loading wake word model with default CPU providers."
            )
            self.oww_model = Model(
                wakeword_models=[str(wade_model_path)],
                inference_framework="onnx",
            )

        model_size = "small" if (self.backend == "cuda" or self.has_npu) and (self.vram > 6 or self.has_npu) else "base"
        
        stt_device = "cpu"
        if self.has_npu:
            stt_device = "npu"
            logger.info(f"🎙️ Loading Whisper ({model_size}) on NPU...")
        elif self.backend in ("cuda", "rocm"):
            stt_device = "cuda"
            logger.info(f"🎙️ Loading Whisper ({model_size}) on {self.backend.upper()}...")
        else:
            logger.info(f"🎙️ Loading Whisper ({model_size}) on CPU...")

        self.stt_model = whisper.load_model(model_size, device=stt_device)
        
        model_path = VOICE_DIR / "kokoro-v1.0.onnx"
        voices_path = VOICE_DIR / "voices-v1.0.bin"
        
        if not model_path.exists() or not voices_path.exists():
             raise FileNotFoundError(f"Missing TTS assets in {VOICE_DIR}")
             
        try:
            self.tts = Kokoro(str(model_path), str(voices_path), providers=onnx_providers)  # type: ignore
        except TypeError:
            logger.warning(
                "kokoro_onnx: 'providers' not supported by this version — "
                "loading TTS with default providers."
            )
            self.tts = Kokoro(str(model_path), str(voices_path))

    def _bootstrap_assets(self):
        """Ensures all necessary audio files are present in ~/.wade/voice."""
        VOICE_DIR.mkdir(parents=True, exist_ok=True)

        local_wade_model = INTERNAL_ASSETS_DIR / "wade.onnx"
        target_wade_model = VOICE_DIR / "wade.onnx"
        
        if not target_wade_model.exists() and local_wade_model.exists():
            print("📦 Installing bundled 'Wade' wake word model...")
            shutil.copy(local_wade_model, target_wade_model)
            try:
                _verify_asset(target_wade_model, "wade.onnx")
            except RuntimeError as e:
                logger.error("❌ Bundled wake word model failed integrity check: %s", e)
                raise
            print("✅ wade.onnx installed.")

        try:
            openwakeword.utils.download_models()
        except Exception:
            pass

        assets = {
            "wade.onnx": WADE_WAKEWORD_URL,
            "kokoro-v1.0.onnx": KOKORO_MODEL_URL,
            "voices-v1.0.bin": KOKORO_VOICES_URL,
        }

        for filename, url in assets.items():
            target = VOICE_DIR / filename
            if target.exists():
                continue
            print(f"📥 Voice component missing: {filename}. Downloading...")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Wade-AI-Client"})
                fd, tmp_path = tempfile.mkstemp(dir=VOICE_DIR, suffix=".tmp")
                try:
                    with urllib.request.urlopen(req) as response, os.fdopen(fd, "wb") as out_file:
                        out_file.write(response.read())
                    _verify_asset(Path(tmp_path), filename)
                    Path(tmp_path).replace(target)
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
                print(f"✅ {filename} installed.")
            except RuntimeError as e:
                logger.error(f"❌ Asset integrity check failed for {filename}: {e}")
            except Exception as e:
                logger.error(f"❌ Failed to download {filename}: {e}")

    def _draw_visualizer(self, audio_chunk):
        """RMS-based terminal visualizer."""
        rms = np.sqrt(np.mean(audio_chunk.astype(np.float32)**2))
        level = int(min(rms / 1000 * 50, 50)) 
        bar = "█" * level + "-" * (50 - level)
        print(f"\r🎤 [{bar}] {level}%", end="", flush=True)

    def _play_wake_chime(self):
        """Generates and plays a brief activation tone mathematically."""
        duration = 0.15
        frequency = 880.0
        
        t = np.linspace(0, duration, int(self.fs * duration), endpoint=False)
        tone = 0.2 * np.sin(2 * np.pi * frequency * t) * np.linspace(1, 0, len(t))
        
        sd.play(tone.astype(np.float32), samplerate=self.fs)
        sd.wait()

    def listen_for_wake_word(self, keyword="wade"):
        """Always-on listener with bulletproof type parsing."""
        print(f"\n👂 Wade is active. Try saying 'Wade'...")
        
        with sd.InputStream(samplerate=self.fs, channels=1, dtype='int16', blocksize=self.chunk_size) as stream:
            while True:
                audio_chunk, _ = stream.read(self.chunk_size)
                processed_chunk = audio_chunk.flatten().astype(np.int16)
                
                prediction = self.oww_model.predict(processed_chunk)
                score = 0.0
                
                if isinstance(prediction, (list, tuple)) and len(prediction) > 0:
                    latest_frame = prediction[-1]
                elif isinstance(prediction, dict):
                    latest_frame = prediction
                else:
                    latest_frame = {}

                if isinstance(latest_frame, dict):
                    raw_val = latest_frame.get(keyword, 0.0)
                    
                    if isinstance(raw_val, (int, float)):
                        score = float(raw_val)
                    elif isinstance(raw_val, np.number):
                        score = float(raw_val.item()) 
                    else:
                        score = 0.0

                rms = float(np.sqrt(np.mean(processed_chunk.astype(np.float32)**2)))
                level = int(min(rms / 1000 * 30, 30)) 
                bar = "█" * level + "-" * (30 - level)
                
                print(f"\r🎤 [{bar}] {level}% | Score: {score:.4f} ", end="", flush=True)

                if score > 0.50:
                    print(f"\n✨ System Wake! (Confidence: {score:.2f})")
                    return True

    def listen(self, max_duration: float = 8.0, silence_duration: float = 1.2) -> str:
        """Transcribes command using VAD — stops recording once the user stops speaking."""
        print("\n🎤 Listening for command...")
        self._play_wake_chime()

        SPEECH_THRESHOLD = 0.02
        chunks_per_sec = self.fs / self.chunk_size
        silence_limit = int(silence_duration * chunks_per_sec)
        max_chunks = int(max_duration * chunks_per_sec)

        frames = []
        speech_started = False
        silence_frames = 0

        with sd.InputStream(samplerate=self.fs, channels=1, dtype='float32', blocksize=self.chunk_size) as stream:
            for _ in range(max_chunks):
                chunk, _ = stream.read(self.chunk_size)
                frames.append(chunk.copy())
                rms = float(np.sqrt(np.mean(chunk ** 2)))

                if rms > SPEECH_THRESHOLD:
                    speech_started = True
                    silence_frames = 0
                elif speech_started:
                    silence_frames += 1
                    if silence_frames >= silence_limit:
                        break

        if not frames:
            return ""

        audio_data = np.concatenate(frames).flatten()
        result = self.stt_model.transcribe(audio_data, fp16=(self.backend == "cuda"))
        return str(result.get("text", "")).strip()

    def speak(self, text: str, voice="am_michael"):
        """Speech synthesis to default output with text sanitization."""
        if not text: return
        
        clean_text = re.sub(r'[*_`#~]', '', text)
        clean_text = re.sub(r'[^\x00-\x7F]+', '', clean_text)
        clean_text = clean_text.strip()
        
        if not clean_text: return

        print(f"🔊 Wade: {clean_text}")
        try:
            samples, sample_rate = self.tts.create(clean_text, voice=voice, speed=1.0)
            sd.play(samples, sample_rate)
            sd.wait()
        except Exception as e:
            logger.error(f"TTS failed: {e}")

    def transcribe_file(self, filepath: str) -> str:
        """Transcribes an audio file from disk via Whisper."""
        print(f"\n🎤 Transcribing inbound voice note...")
        result = self.stt_model.transcribe(filepath, fp16=(self.backend == "cuda"))
        return str(result.get("text", "")).strip()

    def generate_audio_file(self, text: str, output_filepath: str, voice="am_michael"):
        """Synthesizes speech and encodes it directly to an OGG Opus file."""
        import scipy.io.wavfile as wavfile
        import tempfile
        import subprocess
        
        if not text: return
        
        clean_text = re.sub(r'[*_`#~]', '', text)
        clean_text = re.sub(r'[^\x00-\x7F]+', '', clean_text)
        clean_text = clean_text.strip()
        
        if not clean_text: return

        print(f"🔊 Encoding W.A.D.E.'s audio response...")
        try:
            samples, sample_rate = self.tts.create(clean_text, voice=voice, speed=1.0)

            fd, temp_wav = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                wavfile.write(temp_wav, sample_rate, samples)
                subprocess.run([
                    "ffmpeg", "-y", "-i", temp_wav,
                    "-c:a", "libopus", "-b:a", "32k", "-vbr", "on", output_filepath
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            finally:
                try:
                    os.remove(temp_wav)
                except OSError:
                    pass

            print("✅ Voice note formatted successfully.")
            
        except FileNotFoundError:
            logger.error("❌ ffmpeg not found! Please ensure ffmpeg is installed and in your system PATH.")
        except Exception as e:
            logger.error(f"❌ TTS File generation failed: {e}")

_voice_service_instance = None

def get_voice_service() -> VoiceService:
    """Singleton accessor for the VoiceService instance."""
    global _voice_service_instance
    if _voice_service_instance is None:
        _voice_service_instance = VoiceService()
    return _voice_service_instance