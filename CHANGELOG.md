# Changelog

All notable changes to the **Groq Dictater Pro** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-09-26

### Added
- feat: add MIT LICENSE and `config.example.json` template for open-source GitHub publishing.
- feat: add dynamic toggle button labeling ("بدء الإملاء" / "إيقاف الإملاء") and accessible names for enhanced screen reader accessibility (NVDA/WCAG 2.1 AA).
- test: add automated regression tests for `APP_DATA_DIR` identity, default model fallback, payload generation, and recording button label state transitions.

### Changed
- refactor: extract `_build_transcription_payload` helper in `audio_engine.py` following Clean Code guidelines (≤ 20 lines, single abstraction level).
- docs: update `README.md` with GitHub badges, accessibility standards, architecture table, build instructions, and developer contact information.

### Fixed
- fix: correct application data directory name from `WhisperDictaterPro` to `GroqDictaterPro` and logger name to `groq_dictater` in `config.py` to prevent data and log collision.
- fix: expand `.gitignore` to comprehensively exclude cache, virtual environments, build artifacts, and local credentials.

## [1.0.0] - 2026-07-25

### Added
- feat: initial release of GroqDictaterPro with wxPython GUI, Groq Cloud API Whisper transcription, DPAPI key encryption, and PyInstaller executable build.
