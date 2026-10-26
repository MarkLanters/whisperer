# Changelog

Each `## X.Y.Z` section becomes the release notes the app shows when it offers that update.
The newest section is the release in development on the dev branch; finish it before tagging.

## 0.1.1
- Only one Whisperer window can run at a time (two copies used to write the same files).

## 0.1.0
First Mac version.
- Whisperer.app for Apple Silicon Macs (M1 or newer, macOS 14 or newer); transcription runs on the Mac's GPU.
- Your files live in Documents/Whisperer: recordings go into Source Files/uk or Source Files/auto, YouTube links into YouTube List.txt, transcripts appear in transcripts.
- Settings: speech model (large-v3, or the faster large-v3-turbo), default languages, keep YouTube audio, update checks.
- Updates for the app and for yt-dlp are offered at start-up and installed only after you approve them.
