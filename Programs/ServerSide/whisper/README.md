# Whisper model files

This folder is the download cache for `faster-whisper` model files. The model weights are large and are excluded from Git.

By default, `app/stt/whisper.py` downloads `Systran/faster-whisper-small` here on its first transcription. Set `WHISPER_MODEL_ID` to another faster-whisper model ID or a local CTranslate2 model directory. For a pre-populated folder, set `WHISPER_LOCAL_FILES_ONLY=true` to prevent a download attempt.

Do not place recordings in this folder. Incoming audio files are read by the caller and are not copied or retained by `WhisperSTT`.
