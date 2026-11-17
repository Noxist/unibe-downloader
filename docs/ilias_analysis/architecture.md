# Project Architecture

The automation project consists of two primary codebases:

1.  **`crawler/`**: The discovery tool used for exploring ILIAS structures and finding endpoints.
2.  **`unibe-downloader/`**: The production automation suite for regular synchronization.

## unibe-downloader Modules

*   `downloader.py`: Handles downloading of standard ILIAS files.
*   `eduid_auth.py`: Automates the SWITCH edu-ID authentication process.
*   `podcast_sync.py`: Synchronizes OpenCast podcast recordings.
*   `live_recorder.py`: Independent service that polls HLS streams for live recordings.
*   `transcribe.py`: Generates transcripts for downloaded audio/video.
*   `notebooklm_export.py`: Prepares files for export to Google NotebookLM.
*   `server_sync.py`: Syncs local data to external storage (e.g., via `rclone`).
*   `pdf_conversion.py`: Converts relevant materials to PDF format.
*   `sync_support.py`: Utility functions and helper methods for synchronization.

## Daily Execution Flow

The daily sync is orchestrated via a `systemd` timer and follows this pipeline:
1.  **systemd timer** triggers the workflow.
2.  `eduid_auth.py` acquires fresh session cookies.
3.  `downloader.py` updates course materials.
4.  `podcast_sync.py` checks for new OpenCast videos.
5.  `transcribe.py` processes any new media files.
6.  `notebooklm_export.py` runs preparation steps.
7.  `server_sync.py` pushes everything via `rclone` to remote storage.

> [!NOTE] 
> The `live_recorder.py` runs as a completely separate background service continuously polling the HLS endpoints.

## Configuration & Storage

*   **Config**: `config.server.json` contains an array of courses to track. Each entry includes `ref_id`, `name`, and `podcast_ref`.
*   **Index Files**: State and history are tracked in:
    *   `.download-index.json`
    *   `.podcast-index.json`
    *   `.notebooklm-export-index.json`
*   **Storage Strategy**:
    *   **Atomic Writes**: Downloads use `.part` or `.tmp` extensions until fully completed, then rename.
    *   **Version Archiving**: Old versions of modified files are moved to `.versions/{sha256}/`.
