"""
ASR (Automatic Speech Recognition) stage for NederCorpus.

Transcribes audio files (produced by podcast acquirers) to plain text
using faster-whisper (CTranslate2 backend, MIT license).

Usage:
  python -m pipeline asr --input data/raw/ --sources uvn_podcast uvv_podcast

The stage walks raw/<source>/ directories, finds .mp3/.m4a files that
have no corresponding .txt transcript yet, and transcribes them in-place.
The existing parsers (parse_uvn_podcast_dir, etc.) then pick up the .txt files.

Requirements:
  pip install faster-whisper
"""
from pipeline.asr.transcribe import transcribe_dir, run_asr

__all__ = ["transcribe_dir", "run_asr"]
