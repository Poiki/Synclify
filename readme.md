# Synclify

**Synclify** keeps Spotify and YouTube Music playlists in sync using the official Spotify Web API and the YouTube Data API v3. The project is designed for everyday listeners, power users, and contributors who want a reliable, scriptable way to manage playlists across streaming platforms.

> Everyone is welcome to clone this repository, configure their own credentials, and use or extend the tool.

---

## Table of Contents

1. [Features](#features)
2. [Architecture Overview](#architecture-overview)
3. [Prerequisites](#prerequisites)
4. [Installation](#installation)
5. [Configuring API Credentials](#configuring-api-credentials)
   - [Spotify OAuth App](#spotify-oauth-app)
   - [Google / YouTube OAuth App](#google--youtube-oauth-app)
6. [Environment Variables](#environment-variables)
7. [Usage](#usage)
   - [Interactive CLI](#interactive-cli)
   - [Legacy Sync Flow](#legacy-sync-flow)
8. [Project Structure](#project-structure)
9. [Troubleshooting](#troubleshooting)
10. [Contributing](#contributing)
11. [License](#license)

---

## Features

- Bidirectional playlist management between Spotify and YouTube Music (add, dedupe, summarise by artist).
- Modular services for Spotify and YouTube, plus adapters for easy extension.
- Rich CLI experience with colourised output (via `rich`).
- Duplicate detection using fuzzy title and artist similarity.
- Google web search fallback to locate YouTube Music tracks when the API quota is exhausted.
- OAuth2 authentication with local token caching.
- Planning/legacy sync mode preserved for backwards compatibility.

---

## Architecture Overview

```
synclify/
©À©¤©¤ cli.py              # Top-level CLI dispatcher
©À©¤©¤ manager.py          # High-level playlist management (add/remove/report)
©À©¤©¤ adapters_impl.py    # Spotify and YouTube adapters implementing common protocol
©À©¤©¤ services/           # Thin wrappers around external APIs
©À©¤©¤ utils.py            # Normalisation helpers and duplicate detection utilities
©À©¤©¤ retry.py            # Shared exponential backoff logic
©À©¤©¤ state.py            # Runtime state shared across modules
©¸©¤©¤ legacy_sync.py      # Original sync workflow (kept for compatibility)
```

- **Adapters** separate product-specific details from generic playlist operations.
- **Services** handle API calls, authentication, and rate-limit aware retries.
- **Manager** orchestrates playlist actions (artist summary, add tracks, remove duplicates).
- **CLI** exposes both the new manager flow and the legacy sync flow.

---

## Prerequisites

- Python **3.9+**
- Spotify Premium account (required to modify playlists via API)
- Google account with access to the Cloud Console
- Git (optional, recommended for cloning)

---

## Installation

```bash
# Clone the repository
git clone https://github.com/<your-username>/synclify.git
cd synclify

# (Optional) create a virtual environment
python -m venv .venv
. .venv/Scripts/activate   # Windows PowerShell
# source .venv/bin/activate # macOS / Linux

# Install dependencies
pip install -r requirements.txt
```

Dependencies are pinned in `requirements.txt` for reproducibility:

```
spotipy==2.23.0
google-api-python-client==2.145.0
google-auth==2.35.0
google-auth-oauthlib==1.2.1
python-dotenv==1.0.1
rich==13.9.2
requests==2.32.3
beautifulsoup4==4.12.3
```

---

## Configuring API Credentials

Synclify uses OAuth2 for both Spotify and Google. You only need to configure each provider once; refresh tokens are stored locally in the `.tokens/` directory (ignored by Git).

### Spotify OAuth App

1. Log into the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Create a **new app**. Give it a name such as `Synclify Local`.
3. In *Settings ¡ú Redirect URIs*, add:
   ```
   http://127.0.0.1:8000/callback
   ```
4. Save the settings, then note the **Client ID** and **Client Secret**.
5. (Optional but recommended) Add your Spotify account to *Users and Access* to avoid 403 errors in development mode.

### Google / YouTube OAuth App

1. Open the [Google Cloud Console](https://console.cloud.google.com/), create a project (for example `Synclify`).
2. Navigate to **APIs & Services ¡ú Library** and enable **YouTube Data API v3**.
3. Under **APIs & Services ¡ú OAuth consent screen**, configure an internal or external consent screen. Add your Google account as a test user.
4. Go to **Credentials ¡ú Create credentials ¡ú OAuth client ID**, choose **Desktop App**.
5. Download the resulting `client_secret.json` file and place it at the project root. (This file is ignored by Git via `.gitignore`.)

---

## Environment Variables

Create a `.env` file in the project root with the following keys:

```env
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8000/callback
```

Additional environment variables can be added later; `python-dotenv` automatically loads the file when the CLI starts.

> **Security tip:** Never commit `.env`, `.tokens/`, or `client_secret.json` to Git. They are already included in `.gitignore`.

---

## Usage

### Interactive CLI

Launch the main Synclify CLI:

```bash
python sync_playlist.py
```

You will see a menu similar to:

```
Synclify
1) Legacy sync mode
2) Manage Spotify playlists
3) Manage YouTube playlists
4) Exit
Select an option:
```

- **Manage Spotify playlists:** add tracks, remove duplicates, or bulk-remove by artist.
- **Manage YouTube playlists:** same actions, using the web search fallback when API results are missing.
- **Legacy sync mode:** run the original add-only sync workflow maintained in `synclify/legacy_sync.py`.

During the first run Synclify opens browser windows for Spotify and Google to complete OAuth consent. Tokens are cached in `.tokens/` for subsequent runs.

### Legacy Sync Flow

To trigger the legacy mode directly:

```bash
python -m synclify.legacy_sync
```

This keeps the original prompt flow in place (compare source/destination playlists, planning mode, manual URL entry, etc.).

---

## Project Structure

| Path                          | Description                                                     |
|------------------------------|-----------------------------------------------------------------|
| `sync_playlist.py`           | Entrypoint; forwards to the interactive CLI                     |
| `synclify/cli.py`            | Menu controller                                                  |
| `synclify/manager.py`        | Playlist orchestration (artist summaries, add/remove, dedupe)   |
| `synclify/services/`         | Spotify / YouTube service layers with OAuth + retry logic       |
| `synclify/adapters_impl.py`  | Adapter implementations bridging services and manager           |
| `synclify/utils.py`          | Title/artist normalisation, dedupe helpers, parsing functions   |
| `synclify/websearch.py`      | Google web scraping fallback for YouTube Music                  |
| `synclify/state.py`          | Shared runtime state (quota flags, plan mode, caches)           |
| `synclify/legacy_sync.py`    | Original one-way sync script preserved for compatibility        |

---

## Troubleshooting

| Problem / Symptom                                      | Suggested Fix |
|--------------------------------------------------------|---------------|
| `SpotifyException: 403` when listing playlists         | Ensure your Spotify app added your user under ¡°Users and Access¡±, and that the redirect URI matches exactly. Delete `.tokens/spotify_token.json` and retry. |
| YouTube quota exhausted (HTTP 403 `quotaExceeded`)     | Switch to planning mode or wait until the daily quota resets (YouTube API quota is 10,000 units/day). |
| Google CAPTCHA during web search                       | A browser window will open; solve the CAPTCHA and press ENTER to retry. |
| Duplicate removal does nothing                         | Ensure the similarity threshold (default 0.90) is appropriate for your playlist. |
| CLI exits with `client_secret.json` missing            | Make sure the file is downloaded from Google Cloud and placed beside `sync_playlist.py`. |

Logs with `[yellow]` messages are warnings; `[red]` indicates an error that stops the current action; `[green]` confirms success.

---

## Contributing

1. Fork the repository and create a feature branch.
2. Keep new code modular (services/adapters/manager) and add concise comments only where necessary.
3. Run linting/tests (add your own if required) before opening a pull request.
4. Describe credentials or setup steps clearly so maintainers can reproduce behaviour.

Bug reports and enhancement ideas are warmly welcome. Please use English when filing issues or PRs so the widest community can collaborate.

---

## License

This project is available under the **MIT License**. When publishing to GitHub, add a `LICENSE` file if you have not already done so.

---

Happy syncing! Feel free to share your fork or improvements¡ªeveryone can use Synclify under their own credentials.
