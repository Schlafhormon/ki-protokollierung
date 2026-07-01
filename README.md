# Protokollierungsassistenz

Automatic transcription and meeting minutes generation from audio recordings of German municipal meetings.

Automatische Transkription und Protokollerstellung aus Audioaufnahmen von deutschen Kommunalsitzungen.

### Screenshots

<table>
  <tr>
    <td align="center"><strong>Upload</strong></td>
    <td align="center"><strong>Processing</strong></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/1.png" alt="Upload" width="400"></td>
    <td><img src="docs/screenshots/2.png" alt="Processing" width="400"></td>
  </tr>
  <tr>
    <td align="center"><strong>Assign segments to agenda items</strong></td>
    <td align="center"><strong>Export meeting minutes</strong></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/3.png" alt="Assign" width="400"></td>
    <td><img src="docs/screenshots/4.png" alt="Export" width="400"></td>
  </tr>
</table>

---

## System Requirements / Systemanforderungen

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| **Disk Space** | 25 GB | 40 GB |
| **RAM** | 8 GB | 16 GB |
| **Internet** | Required for setup | Required for setup |
| **Operating System** | Windows 10/11, macOS 11+, Linux | - |

### Optional: NVIDIA GPU (Windows/Linux only)

If you have an NVIDIA graphics card, the application can transcribe audio much faster. macOS users will use CPU mode (still works, just much slower).

Wenn Sie eine NVIDIA-Grafikkarte haben, kann die Anwendung Audio viel schneller transkribieren. macOS-Benutzer verwenden den CPU-Modus (funktioniert trotzdem, nur viel langsamer).

---

## Installation

### Step 1: Download the Application

Download the application from GitHub:

Laden Sie die Anwendung von GitHub herunter:

1. Go to: **https://github.com/aihpi/pilotproject-protokollierungsassistenz**
2. Click the green **"Code"** button
3. Click **"Download ZIP"**
4. Save the file to your computer (e.g., Downloads folder)
5. **Extract the ZIP file:**
   - **Windows:** Right-click the ZIP file → "Extract All..." → Choose a location (e.g., Desktop or Documents)
   - **macOS:** Double-click the ZIP file to extract it

You should now have a folder called `protokollierungsassistenz-main` (or similar).

Sie sollten jetzt einen Ordner namens `protokollierungsassistenz-main` (oder ähnlich) haben.

---

### Step 2: Install Docker Desktop

Docker is required to run the application. Download and install Docker Desktop:

Docker wird benötigt, um die Anwendung auszuführen. Laden Sie Docker Desktop herunter und installieren Sie es:

| Operating System | Download Link |
|------------------|---------------|
| **Windows** | [Download Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/) |
| **macOS** | [Download Docker Desktop for Mac](https://docs.docker.com/desktop/install/mac-install/) |
| **Linux** | [Download Docker Desktop for Linux](https://docs.docker.com/desktop/install/linux-install/) |

After installation, **start Docker Desktop** and wait until it shows "Docker Desktop is running".

Nach der Installation **starten Sie Docker Desktop** und warten Sie, bis "Docker Desktop is running" angezeigt wird.

---

### Step 3: Run the Setup Script

#### Windows

1. Open the folder where you downloaded/extracted the application
2. Find the file **`setup.ps1`**
3. **Right-click** on it and select **"Run with PowerShell"**
4. Follow the on-screen instructions

If you see a security warning, click "Run anyway" or "More info" → "Run anyway".

#### macOS / Linux

1. Open **Terminal** (macOS: Applications → Utilities → Terminal)
2. Navigate to the application folder:
   ```bash
   cd /path/to/protokollierungsassistenz
   ```
3. Run the setup script:
   ```bash
   ./setup.sh
   ```
4. Follow the on-screen instructions

---

### Optional: Pin a Stable Release

By default the setup uses the existing moving image tags (`latest`,
`cpu-latest`, `gpu-latest`). To install a specific published release instead,
set `PROTOKOLL_IMAGE_TAG` before starting:

```bash
PROTOKOLL_IMAGE_TAG=v1.2.3 ./setup.sh
```

```powershell
$env:PROTOKOLL_IMAGE_TAG = "v1.2.3"
.\setup.ps1
```

The setup scripts pull missing images by default. Set
`PROTOKOLL_PULL_POLICY=always` for the old "always check for updates" behavior,
or `PROTOKOLL_PULL_POLICY=never` for fully offline starts with local images.

Runtime state is stored in local bind mounts:

- `./uploads` for retained audio playback after session restore
- `./data` for the SQLite session database and optional telemetry backups

### Lokale Datenschutzdaten / Local Privacy Data

Die Anwendung verarbeitet Audio, Transkripte und Sprecherinformationen lokal in
der Docker-Umgebung. In `./data/sessions.sqlite3` können gespeichert werden:

- Sitzungszustand, TOPs, Zuordnungen, Zusammenfassungen und Export-Metadaten
- lokale Sprecherbenennungen pro Sitzung
- lokale Job-Sprecher-Embeddings für Review-Funktionen einer Sitzung
- globale Sprecherprofile und globale Sprecher-Embeddings nur nach Opt-in
  "Sprecher dauerhaft merken" oder nach einer ausdrücklichen Aktion wie
  "Vorschlag übernehmen", "Neues Profil merken" oder "Bestehendem Profil
  zuordnen"

Das Opt-in "Sprecher dauerhaft merken" ist standardmäßig aus. Ohne diese
Auswahl werden bei der automatischen Verarbeitung keine gespeicherten
Sprecherprofile vorgeschlagen. Lokale Sprecher können trotzdem in der aktuellen
Sitzung benannt werden.

Gespeicherte Sprecherprofile lassen sich in der Sprecherprüfung verwalten:

- "Profil archivieren" entfernt das Profil aus künftigen Vorschlägen und löst
  gespeicherte Observations vom Profilnamen.
- "Embeddings löschen" entfernt die global gespeicherten biometrischen
  Referenz-Embeddings eines Profils.
- Archivierte Profile werden standardmäßig nicht mehr in der Profilliste und
  nicht mehr als automatische Sprecher-Vorschläge verwendet.

---

### Step 4: Wait for Download

The setup will download the application images (~6 GB) and AI models (~5 GB). This may take **5-15 minutes** depending on your internet speed.

Das Setup lädt die Anwendungsimages (~6 GB) und KI-Modelle (~5 GB) herunter. Dies kann je nach Internetgeschwindigkeit **5-15 Minuten** dauern.

You will see progress messages. When complete, your browser will open automatically.

Sie sehen Fortschrittsmeldungen. Nach Abschluss öffnet sich Ihr Browser automatisch.

---

### Step 5: Start Using the Application

Once setup is complete, the application is available at:

Nach Abschluss des Setups ist die Anwendung verfügbar unter:

**http://localhost:3000**

---

## Daily Usage / Tägliche Nutzung

### Starting the Application

If you restart your computer, you need to start the application again:

Wenn Sie Ihren Computer neu starten, müssen Sie die Anwendung erneut starten:

1. **Start Docker Desktop** (if not running)
2. Run the setup script:
   - **Windows:** Right-click `setup.ps1` → "Run with PowerShell"
   - **macOS/Linux:** Open Terminal in the application folder and run `./setup.sh`

The script will check if the application is already running and open your browser automatically.

Das Skript prüft, ob die Anwendung bereits läuft und öffnet automatisch Ihren Browser.

### Stopping the Application

To stop the application and free up resources:

Um die Anwendung zu stoppen und Ressourcen freizugeben:

- **Windows:** `.\setup.ps1 stop`
- **macOS/Linux:** `./setup.sh stop`

### Other Commands / Weitere Befehle

| Command | Description |
|---------|-------------|
| `./setup.sh status` | Check if services are running / Status der Dienste prüfen |
| `./setup.sh logs` | View live logs / Live-Logs anzeigen |
| `./setup.sh restart` | Restart the application / Anwendung neu starten |

---

## Troubleshooting / Fehlerbehebung

### "Docker is not running"

Make sure Docker Desktop is started and shows "Running" status.

Stellen Sie sicher, dass Docker Desktop gestartet ist und den Status "Running" zeigt.

### "Not enough disk space"

Free up at least 25 GB of disk space before running setup.

Geben Sie mindestens 25 GB Speicherplatz frei, bevor Sie das Setup ausführen.

### Application is slow

- Transcription on CPU is slower than GPU (this is normal)
- First transcription may take longer due to model loading
- Ensure Docker Desktop has enough memory allocated (8 GB+)

### View Logs

To see what's happening:

```bash
docker compose logs -f
```

Press `Ctrl+C` to stop viewing logs.

### Complete Reset

If something goes wrong and you want to start fresh:

```bash
docker compose down -v
./setup.sh  # or .\setup.ps1 on Windows
```

---

## Nutzungsstatistiken / Usage Statistics

Telemetrie ist standardmäßig deaktiviert und wird nur gesendet, wenn Nutzerinnen
oder Nutzer sie in der UI ausdrücklich einschalten. Zusätzlich muss zur Laufzeit
`TELEMETRY_WEBHOOK_URL` gesetzt sein; die URL wird nicht ins Image eingebrannt.

Telemetry is disabled by default and is only sent after explicit opt-in in the UI.
`TELEMETRY_WEBHOOK_URL` must also be configured at runtime; it is not baked into
the container image.

### Erfasste Daten / Data Collected

- Zeitstempel und App-Version / timestamp and app version
- Geräteklasse, GPU-Name und VRAM, falls verfügbar / device type, GPU name and VRAM if available
- Verwendete Whisper- und LLM-Modelle / Whisper and LLM model names
- Whisper-Batch-Größe / Whisper batch size
- Audiodauer und Verarbeitungszeiten / audio duration and processing times
- Anzahl Transkriptzeilen und Zeichenanzahl / transcript line and character counts
- Anzahl Tagesordnungspunkte / number of agenda items
- Protokoll-Zeichenanzahl / protocol character count
- Prompt-Kategorie (`default`, `custom`, `generic`), nicht der Prompt selbst / prompt category, not the prompt content
- Erfolgsstatus und technische Fehlerkategorie, falls gesetzt / success status and technical error category if set

### Nicht erfasste Daten / Data NOT Collected

- Audio-Dateien, Audiodaten oder Dateinamen / audio files, audio data or filenames
- Inhalte von Transkripten, TOPs oder Protokollen / transcript, agenda item or protocol content
- Namen, Sprecherzuordnungen oder andere Personenangaben / names, speaker mappings or other personal data
- Sprecherprofile oder Sprecher-Embeddings / speaker profiles or speaker embeddings
- TOP-Titel / agenda item titles
- System-Prompt-Inhalte oder Prompt-Auszüge / system prompt content or prompt excerpts

### Lokale Backups / Local Backups

Lokale Telemetrie-Backups sind standardmäßig deaktiviert
(`TELEMETRY_BACKUP_ENABLED=false`). Wenn sie aktiviert werden, werden nur die oben
genannten aggregierten Telemetrie-Felder als JSONL gespeichert. Die Retention ist
konfigurierbar und wird beim Schreiben angewendet:

- `TELEMETRY_BACKUP_RETENTION_DAYS` (Standard: `14`)
- `TELEMETRY_BACKUP_MAX_FILES` (Standard: `30`)
- `TELEMETRY_BACKUP_DIR` (Docker/K8s-Standard: `/app/data/telemetry_backup`)

---

## GPU Mode (Optional, Windows/Linux)

If you have an NVIDIA GPU and want faster transcription:

1. Install [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
2. Run the setup script - it will detect your GPU automatically
3. Choose "Yes" when asked about GPU mode

macOS does not support NVIDIA GPUs.

---

## Reporting Issues & Feedback

Found a bug? Have a feature request? We'd love to hear from you!

Haben Sie einen Fehler gefunden? Haben Sie einen Funktionswunsch? Wir freuen uns über Ihr Feedback!

### How to Report an Issue

1. Go to: **https://github.com/aihpi/pilotproject-protokollierungsassistenz/issues**
2. Click **"New Issue"**
3. Include the following information:
   - Your operating system (Windows/macOS/Linux)
   - What you were trying to do
   - What happened (error message, screenshot if possible)
   - Steps to reproduce the problem

### Feature Requests

Have an idea for improving the application? Create an issue and describe:
- What feature you'd like to see
- Why it would be helpful for your work

---

## For Developers

<details>
<summary>Click to expand developer documentation</summary>

### Overview

This application provides a web-based workflow for generating meeting minutes (Sitzungsprotokolle) from audio recordings:

1. **Upload** - Upload audio recording and enter agenda items (Tagesordnungspunkte/TOPs)
2. **Transcribe** - Automatic transcription with speaker diarization using WhisperX + PyAnnote
3. **Assign** - Manually assign transcript segments to each TOP
4. **Summarize** - Generate summaries per TOP using an LLM (Qwen3 8B via Ollama)
5. **Export** - Download the final meeting minutes

Der Exportbereich im letzten Schritt enthält Metadatenfelder für Gremium, Datum,
Ort, Sitzungstitel und Teilnehmer. Das Protokoll kann als DOCX, PDF oder TXT
heruntergeladen werden. Optional lassen sich Sprecherliste, Transkript-Auszug und
ein Bearbeitungs-/Generierungshinweis als Anhang aufnehmen.

### Project Structure

```
protokollierungsassistenz/
├── app/
│   ├── frontend/          # React + TypeScript web application
│   └── backend/           # FastAPI Python backend
├── scripts/               # Runtime helper scripts used by Compose
│   └── research/          # Non-production research prototypes and archives
├── .github/workflows/     # CI/CD for building Docker images
└── docker-compose.yml     # Production deployment
```

Research scripts under `scripts/research/` are not part of the production application path. They may contain local sample paths and require manual input/data setup before use.

### Pre-built Images

Docker images are automatically built and published to GitHub Container Registry.
Moving tags remain available for convenience:

- `ghcr.io/aihpi/pilotproject-protokollierungsassistenz/frontend:latest`
- `ghcr.io/aihpi/pilotproject-protokollierungsassistenz/backend:cpu-latest`
- `ghcr.io/aihpi/pilotproject-protokollierungsassistenz/backend:gpu-latest`

Versioned release tags are preferred for reproducible deployments:

- `ghcr.io/aihpi/pilotproject-protokollierungsassistenz/frontend:vX.Y.Z`
- `ghcr.io/aihpi/pilotproject-protokollierungsassistenz/backend:vX.Y.Z`
- `ghcr.io/aihpi/pilotproject-protokollierungsassistenz/backend:vX.Y.Z-gpu`

Commit-specific `sha-...` tags are used by the Kubernetes deployment manifests.
These images include all ML models pre-bundled, so no HuggingFace token is required for end users.

### Development Setup

#### 1. Ollama (for summarization)

```bash
# macOS
brew install ollama

# Start Ollama server
ollama serve

# Pull the model (in another terminal)
ollama pull qwen3:8b
```

#### 2. Backend

```bash
cd app/backend

# Install dependencies with uv
uv sync

# Set environment variables
export HF_TOKEN=your_huggingface_token

# Run development server
uv run uvicorn main:app --port 8010
```

The backend runs on `http://localhost:8010`.

#### 3. Frontend

```bash
cd app/frontend

# Install dependencies
npm install

# Run development server
npm run dev
```

The frontend runs on `http://localhost:5173`.

### Building Docker Images Locally

To build images locally (requires HuggingFace token):

```bash
export HF_TOKEN=your_huggingface_token

# CPU image
DOCKER_BUILDKIT=1 docker build \
  --secret id=hf_token,env=HF_TOKEN \
  -t backend:cpu ./app/backend

# GPU image
DOCKER_BUILDKIT=1 docker build \
  -f app/backend/Dockerfile.gpu \
  --secret id=hf_token,env=HF_TOKEN \
  -t backend:gpu ./app/backend
```

For development images without pre-cached models, build with
`--build-arg PRECACHE_MODELS=0` and provide `HF_TOKEN` at runtime if diarization
requires it.

Runtime images can be pinned through `.env` or shell environment variables:

```bash
FRONTEND_IMAGE=ghcr.io/aihpi/pilotproject-protokollierungsassistenz/frontend:v1.2.3
BACKEND_IMAGE=ghcr.io/aihpi/pilotproject-protokollierungsassistenz/backend:v1.2.3
BACKEND_GPU_IMAGE=ghcr.io/aihpi/pilotproject-protokollierungsassistenz/backend:v1.2.3-gpu
```

### Environment Variables

| Variable             | Description                                         | Default                     |
| -------------------- | --------------------------------------------------- | --------------------------- |
| `HF_TOKEN`           | HuggingFace token for local dev/runtime, or BuildKit secret for local model pre-cache builds | - |
| `FRONTEND_IMAGE`     | Frontend image reference for Docker Compose         | `frontend:latest` GHCR image |
| `BACKEND_IMAGE`      | CPU backend image reference for Docker Compose      | `backend:cpu-latest` GHCR image |
| `BACKEND_GPU_IMAGE`  | GPU backend image reference for Docker Compose override | `backend:gpu-latest` GHCR image |
| `OLLAMA_IMAGE`       | Ollama image reference for Docker Compose           | `ollama/ollama:latest`      |
| `WHISPER_MODEL`      | Whisper model size                                  | `large-v2`                  |
| `WHISPER_DEVICE`     | Device for inference (`cuda`, `cpu`, `auto`)        | `auto`                      |
| `WHISPER_BATCH_SIZE` | Batch size for transcription                        | `16`                        |
| `WHISPER_LANGUAGE`   | Language code                                       | `de`                        |
| `LLM_BASE_URL`       | Ollama API endpoint                                 | `http://localhost:11434/v1` |
| `LLM_MODEL`          | Model name for summarization                        | `qwen3:8b`                  |
| `LLM_TIMEOUT_SECONDS` | Timeout per LLM request                            | `120`                       |
| `LLM_MAX_RETRIES`    | Retries for transient LLM errors                    | `2`                         |
| `LLM_RETRY_BACKOFF_SECONDS` | Backoff between transient LLM retries       | `0.5`                       |
| `LLM_CHUNK_CHARS`    | Target chunk size for long TOP transcripts          | `12000`                     |
| `LLM_STRUCTURED_FALLBACK` | Fall back to plain text when structured output parsing fails | `true`        |
| `PERSISTENCE_DB_PATH` | SQLite session database path inside backend container | `/app/data/sessions.sqlite3` |
| `JOB_MAX_AGE_SECONDS` | Max age for in-memory job cache cleanup            | `7200`                      |
| `JOB_MAX_COUNT`      | Max number of jobs retained in memory               | `100`                       |
| `DELETE_UPLOADS_ON_JOB_CLEANUP` | Delete upload files when old jobs are cleaned up | `false`          |
| `DELETE_UPLOADS_ON_CANCEL_OR_FAILURE` | Delete upload files after cancelled/failed jobs | `true`        |
| `MAX_UPLOAD_BYTES`   | Maximum upload size                                 | `524288000`                 |
| `TRANSCRIPTION_CONCURRENCY` | Concurrent transcription workers             | `1`                         |
| `TELEMETRY_WEBHOOK_URL` | Runtime webhook URL for opt-in telemetry          | (empty, disabled)           |
| `TELEMETRY_BACKUP_ENABLED` | Enable local telemetry JSONL backups          | `false`                     |
| `TELEMETRY_BACKUP_RETENTION_DAYS` | Retention for local telemetry backups | `14`                        |
| `TELEMETRY_BACKUP_MAX_FILES` | Maximum local telemetry backup files       | `30`                        |
| `TELEMETRY_BACKUP_DIR` | Directory for local telemetry backups             | `/app/data/telemetry_backup` |

### API Endpoints

| Endpoint                         | Method | Description                          |
| -------------------------------- | ------ | ------------------------------------ |
| `/health`                        | GET    | Health check                         |
| `/api/transcribe`                | POST   | Upload audio and start transcription |
| `/api/transcribe/{job_id}`       | GET    | Get transcription job status         |
| `/api/audio/{job_id}`            | GET    | Stream audio file                    |
| `/api/summarize`                 | POST   | Generate summary for a TOP segment   |
| `/api/extract-tops`              | POST   | Extract TOPs from PDF                |
| `/api/export`                    | POST   | Export minutes as TXT, DOCX or PDF   |
| `/api/speaker-profiles`          | GET/POST | List or create speaker profiles after explicit action |
| `/api/speaker-profiles/{profile_id}` | PUT/DELETE | Rename or archive a speaker profile |
| `/api/speaker-profiles/{profile_id}/embeddings` | DELETE | Delete persisted global speaker embeddings |
| `/api/sessions/{session_id}/speaker-observations` | GET | List reviewable speaker observations |
| `/api/telemetry/session-complete`| POST   | Report opt-in aggregate telemetry    |

### Technology Stack

**Frontend:**
- React 19 with TypeScript
- Vite
- Tailwind CSS

**Backend:**
- FastAPI
- WhisperX (speech-to-text with word-level timestamps)
- PyAnnote (speaker diarization)
- Ollama with Qwen3 8B (summarization)

</details>

---

## Acknowledgements

<a href="http://hpi.de/kisz">
  <img src="app/frontend/public/logos/logo_bmftr_de.png" alt="BMFTR Logo" width="170">
</a>

The [AI Service Centre Berlin Brandenburg](http://hpi.de/kisz) is funded by the [Federal Ministry of Research, Technology and Space](https://www.bmbf.de/) under the funding code 16IS22092.
