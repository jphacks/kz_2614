# Whisper model cache location

This folder is retained as a documentation pointer; it is not the model cache used by the application. `faster-whisper` stores downloaded model files under `Programs/ServerSide/whisper/` from the repository root. The path is set in [`STTLLMTTS/stt/whisper.py`](../../stt/whisper.py), and the cache is excluded from Git.

By default, the application downloads `Systran/faster-whisper-small` on its first transcription. Set `WHISPER_MODEL_ID` to another faster-whisper model ID or a local CTranslate2 model directory. Set `WHISPER_LOCAL_FILES_ONLY=true` to prevent download attempts when model files are already available in the configured cache.

Do not place recordings in the model cache. Incoming audio is read by the caller and is not copied or retained by `WhisperSTT`.
