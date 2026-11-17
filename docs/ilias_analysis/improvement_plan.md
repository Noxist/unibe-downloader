# Improvement Plan

Prioritized roadmap for enhancing the automation suite (`crawler` and `unibe-downloader`).

## Priority 0 (Critical / Blocker)
*   **Session Resilience**: Implement robust detection of expired sessions during long-running sync operations and automatically trigger `eduid_auth.py` to refresh tokens without failing the entire pipeline.
*   **Error Handling in Live Recorder**: Ensure the `live_recorder.py` recovers gracefully from transient network drops or HLS segment 404s without corrupting the final MP4.

## Priority 1 (High Impact)
*   **Direct HTTP Downloads**: Refactor `downloader.py` to use a standard HTTP client (e.g., `httpx` or `requests`) with exported session cookies, removing the dependency on Playwright's API context for simple file transfers.
*   **Resume Support for Videos**: Implement HTTP Range request logic for `dist.opencast.unibe.ch` MP4 downloads in `podcast_sync.py` to resume interrupted multi-gigabyte downloads.

## Priority 2 (Optimization)
*   **ILIAS File Resumability**: Test and, if supported, implement HTTP Range requests for standard ILIAS file downloads.
*   **Parallelization**: Modify `downloader.py` and `podcast_sync.py` to download multiple files concurrently using `asyncio`, respecting server rate limits.

## Priority 3 (Feature Additions)
*   **Heuristic Live Stream Discovery**: Build a scanner in the `crawler` to brute-force or predict active `livedist` HLS endpoints based on known room identifiers and schedule data.
*   **Metadata Enrichment**: Extract more metadata from the OpenCast player JSON (e.g., slide text OCR data if available, presenter names) and include it in the `.notebooklm-export-index.json`.
