**OCEANKIND / MAR FUTURA**

System Improvement Report

Hardware \| Software \| Scalability \| Security \| July 2026

\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

1\. Executive Summary

OceanKind is an underwater acoustic monitoring system designed to detect illegal blast fishing. The current deployment uses a **Raspberry Pi 4 Model B** with a HifiBerry ADC Pro HAT, two Aquarian H5 hydrophones, solar power, and cellular connectivity. A scikit-learn ML classifier runs locally on 5-second audio clips, and alerts are pushed to Azure Blob Storage and delivered via WhatsApp (Twilio).

This report assesses three areas for improvement: **hardware right-sizing** (the current compute platform is over-specified for the workload), **software architecture** (the single-threaded monolith creates detection gaps and operational fragility), and **dashboard scalability** (the current blob-polling pattern cannot support filtering, pagination, or multiple units), and **security** (the entire system is publicly accessible with no authentication at any layer). The goal is a system that is cheaper to build, uses less power, is more reliable in remote deployments, properly secured, and can scale to multiple units without architectural changes.

2\. Hardware Assessment

2.1 Current Hardware Profile

  --------------- ----------------------------------------------------------- --------------------------------------------------------------------------------------
  **Component**   **Current**                                                 **Notes**
  SBC             Raspberry Pi 4B (2GB+)                                      4-core ARM Cortex-A72, 1.5GHz
  Audio ADC (?)   HifiBerry DAC+ ADC Pro --- or --- Raspberry Pi Codec Zero   Codebase references HifiBerry; system diagram shows Codec Zero. Needs clarification.
  Power draw      \~3.5W total system                                         Pi alone: \~3.0W
  Storage         MicroSD card                                                Single point of failure
  Connectivity    ZTE 4G modem (USB/Ethernet)                                 Cellular, no WiFi needed
  Solar panel     40-100W                                                     Sized for Pi power draw
  Battery         12V LiFePO4, 20-50Ah                                        \~360Wh nominal
  --------------- ----------------------------------------------------------- --------------------------------------------------------------------------------------

2.2 ML Model Analysis

The classification model is a **StandardScaler followed by LogisticRegression** pipeline, packaged in a 3 KB joblib file. The model takes 52 input features (20 MFCCs with mean and standard deviation, plus 12 spectral descriptors) and produces a binary classification (blast vs. background) with a single dot product of 52 weights plus a bias term, followed by a sigmoid function. This is **53 parameters total**. The inference step itself is computationally trivial and could execute in microseconds on any modern microcontroller.

  ---------------------- -----------------------------------------------
  **Property**           **Value**
  Model type             Pipeline: StandardScaler + LogisticRegression
  Input features         52 (20 MFCCs x2 + 12 spectral descriptors)
  Parameters             53 (52 weights + 1 bias)
  Model file size        3.0 KB (joblib serialized)
  Training sample rate   22,050 Hz (mono)
  Clip length            5 seconds
  Inference cost         52 multiply-accumulates + sigmoid
  ---------------------- -----------------------------------------------

The computational bottleneck is not inference but **feature extraction**. The current implementation uses librosa (Python) to compute MFCCs and spectral descriptors, which takes 1-3 seconds per 5-second clip on the Pi 4. This step requires FFT computation, mel-filterbank application, and discrete cosine transform. On an ESP32-S3 or similar microcontroller, these operations would need to be reimplemented in C using libraries such as ESP-ADF or CMSIS-DSP, which is feasible but represents the primary porting effort.

2.3 The Raspberry Pi 4 Is Over-Specified

The Pi 4 provides 4 CPU cores at 1.5 GHz, up to 8 GB RAM, USB 3.0, gigabit Ethernet, dual HDMI, and a full desktop-class Linux environment. The OceanKind workload uses **one core intermittently**, approximately 50-100 MB of RAM (Python + librosa + model), no display output, and no USB 3.0 peripherals. The system is paying the power budget of a desktop SBC (\~3W) to run a script that records audio, performs a small FFT-based feature extraction, executes a 52-parameter dot product, and uploads JSON over cellular.

More critically, the Pi 4 introduces **operational fragility** in a remote solar deployment. The MicroSD card is a well-documented single point of failure: repeated writes cause wear, and power loss during writes can corrupt the filesystem. The team has already implemented overlay filesystem protection (protect\_sd.sh) and a two-phase OTA update process to mitigate this, but both add complexity and introduce their own failure modes. The OTA update, in particular, requires two reboots and can leave the unit in an unrecoverable state if the process fails mid-way.

2.4 Hardware Alternatives

  ---------------------- ----------- --------- --------------- ---------- -----------------------------------------
  **Platform**           **Power**   **RAM**   **Storage**     **Cost**   **Trade-off**
  Raspberry Pi 4B        \~3.0W      2-8GB     SD card         \~\$55     Current. Overkill, SD fragility.
  Raspberry Pi Zero 2W   \~0.4W      512MB     SD card         \~\$15     Runs Python/librosa. SD issue remains.
  ESP32-S3 + PSRAM       \~0.2W      8MB       Flash (no SD)   \~\$8      Requires C port of feature extraction.
  Cloud offload          \~0.1W\*    Minimal   N/A             Variable   Record + send WAVs. High cellular cost.
  ---------------------- ----------- --------- --------------- ---------- -----------------------------------------

*\* Microcontroller only, excluding modem.*

2.4.1 Raspberry Pi Zero 2W

The Pi Zero 2W (quad-core ARM Cortex-A53, 512 MB RAM) can run the existing Python codebase with minimal changes. Power draw drops from \~3W to \~0.4W, allowing a significantly smaller solar panel (10-20W) and battery (10Ah). The 512 MB of RAM is sufficient for the workload. However, the **SD card fragility remains**, and librosa feature extraction will be slower (\~3-5s per clip vs. 1-3s on the Pi 4). This is the lowest-effort migration path.

2.4.2 ESP32-S3 With PSRAM

An ESP32-S3 module with 8 MB PSRAM (e.g., ESP32-S3-WROOM-1-N16R8) eliminates the SD card entirely: firmware lives in flash, OTA updates are handled via dual-partition A/B schemes native to ESP-IDF, and there is no filesystem to corrupt. Power draw drops to \~0.2W for the MCU. The 53-parameter logistic regression model is trivial to run. The challenge is **porting the MFCC feature extraction to C**. Libraries exist (ESP-ADF, CMSIS-DSP) but this represents a development effort of approximately 1-2 weeks. Audio capture via I2S is native to ESP-IDF and well-documented.

2.4.3 Cloud Offload Architecture

In this model, the edge device (ESP32 or similar) only records audio and transmits raw or compressed clips to a cloud endpoint, where classification runs server-side. This eliminates the need to port feature extraction to embedded C. However, continuous 5-second clips at 22 kHz mono 16-bit are approximately 220 KB each. At one clip per cycle (\~8-10 seconds total), this produces **\~1.5-2 GB of cellular data per day**. Compression (Opus, FLAC) could reduce this by 50-70%, but the cellular cost remains significant for remote deployments. A hybrid approach (edge pre-filter on simple RMS energy, only upload clips above a threshold) would reduce volume substantially.

2.5 Detection Range Consideration

The system is intended to detect blasts that may occur **hundreds or thousands of kilometers away**. Underwater sound propagates efficiently, particularly through the SOFAR channel, where low-frequency signals can travel transcontinental distances. However, distant blasts arrive as heavily attenuated, low-frequency signals mixed with ambient ocean noise. Whether the current classifier can detect these depends on: (a) what the training data included (nearby blasts only, or also distant signatures), (b) the noise floor of the capture chain, and (c) the frequency response of the hydrophones.

The Aquarian H5 has a sensitivity of approximately -185 dB re 1V/µPa across 10 Hz-100 kHz. The HifiBerry ADC Pro provides 24-bit resolution with a low noise floor. For distant detection, the **ADC quality and hydrophone sensitivity matter more than compute power**. If the system moves to a cheaper ADC (e.g., ESP32 built-in ADC at 12-bit), distant blast detection capability would likely degrade significantly. An external I2S ADC module (e.g., INMP441 MEMS mic or a PCM1808-based board) would preserve capture quality while running on a smaller compute platform.

Recommendation: before committing to a hardware downgrade, **validate the classifier against labeled distant-blast recordings**. If the training set only contains nearby events, the model will need retraining regardless of hardware choice.

3\. Software Architecture

3.1 Current Architecture: Single-Threaded Monolith

The entire system runs as a single Python file (marfutura\_iot\_audio.py, 1,309 lines) executing a sequential loop on one thread. Each cycle performs: remote config check, 5-second audio recording (blocking arecord subprocess), librosa feature extraction + classification, WhatsApp alert (if triggered), blob upload, IoT Hub message, modem signal poll, Victron solar telemetry read, status upload, CSV logging, and power history upload. **During classification and upload, the hydrophone is not listening.**

For a system whose purpose is detecting sub-second acoustic events, this architecture has a fundamental flaw: **the detection duty cycle is well below 100%**. A conservative estimate puts the deaf window at 3-15 seconds per cycle (feature extraction + network operations), meaning the system may miss 30-60% of actual blasts depending on cellular latency and upload times.

3.2 Proposed Architecture: Asynchronous Pipeline With Queues

The system should be restructured into independent, asynchronous components connected by queues. Each component runs on its own thread or process, fails independently, and does not block the others.

  --------------- ---------------------------------------------------------------------------------------------------------------------------------------------- -----------------------------------------------------------------
  **Layer**       **Responsibility**                                                                                                                             **Isolation**
  **Capture**     Continuous audio recording via ring buffer. Never stops. Writes fixed-length segments to an internal queue.                                    Own thread. No network, no disk I/O.
  **Detection**   Pulls audio segments from the capture queue. Extracts features, runs classifier. If alert triggered, places event on the alert queue.          Own thread. CPU-bound only.
  **Transport**   Pulls events from the alert queue. Uploads WAV to blob, sends WhatsApp, updates manifest, sends IoT Hub message. Handles retries internally.   Own thread. Network-bound. Can lag without affecting detection.
  **Telemetry**   On its own timer: polls solar controller, modem signal, system stats. Uploads status.json and power\_history.json.                             Own thread. Independent timer.
  **Config**      Polls remote\_config.json periodically. Validates and clamps incoming values. Updates shared config object with locking.                       Own thread. Validates before applying.
  --------------- ---------------------------------------------------------------------------------------------------------------------------------------------- -----------------------------------------------------------------

The critical invariant is that **the capture layer never blocks**. Whether the cellular connection is down, the blob upload is slow, or the Twilio API is timing out, audio recording continues uninterrupted. Events that cannot be transmitted are queued locally with bounded retries (the pending alert buffer pattern already exists in the codebase and should be generalized).

The deprecated modular codebase (main.py, audio\_capture.py, detector.py, alert.py, clip\_saver.py) already implements the capture layer correctly using sounddevice with a callback-driven queue. This code should be recovered and used as the foundation for the capture component.

3.3 Code Modularization

The monolith should be broken into focused modules. Each module should have a single responsibility, a clear interface, and no knowledge of the others beyond what it receives through queues or shared configuration.

  --------------- -------------------------------- -----------------------------------------------------------------------------------------------
  **Module**      **Current Source**               **Scope**
  capture.py      audio\_capture.py (deprecated)   Continuous I2S/ALSA capture, ring buffer, auto-detect audio device by name
  classifier.py   Lines 276-357 of monolith        Feature extraction + model inference. RMS fallback if model fails to load. Loud failure mode.
  transport.py    Lines 196-270, 911-998           Azure Blob upload, manifest management, WhatsApp via Twilio, IoT Hub messaging, retry queue.
  telemetry.py    Lines 360-855                    Victron VE.Direct, modem signal, system stats, battery alert state machine, CSV logging.
  config.py       Lines 30-88, 858-863             All constants, env var loading, remote config polling with validation and safe clamping.
  main.py         Lines 1142-1308                  Orchestrator only: starts threads, wires queues, handles shutdown.
  --------------- -------------------------------- -----------------------------------------------------------------------------------------------

3.4 Dashboard Architecture

The dashboard (dashboard/index.html, 76 KB) is a **self-contained static web page hosted on Azure Static Web Hosting**, completely independent from the Raspberry Pi. The Pi and the dashboard never communicate directly. Azure Blob Storage serves as the intermediary: the Pi pushes three JSON files (manifest.json, status.json, power\_history.json) and WAV clips to a public blob container; the dashboard polls these files every 30 seconds from the user\'s browser.

This decoupled architecture is a strength: the Pi has no HTTP server to maintain, the dashboard works from any browser, and either side can be updated independently. However, the public unauthenticated blob container is a critical security concern (see Section 4). The dashboard should transition to reading from the authenticated API described in Sections 3.5 and 4.3.

3.5 Dashboard Scalability

The current dashboard architecture fetches **all data, every 30 seconds, unconditionally**. There is no pagination, no date filtering, no conditional requests, and no way to ask for only what changed since the last poll. This design works for a single device with a short history, but breaks down as detection counts grow and additional units come online.

3.5.1 Current Scalability Problems

**manifest.json grows without bound.** Every detection is appended to a single JSON file. The dashboard downloads the entire detection history on every poll cycle. After months of continuous operation, or with multiple units feeding the same container, this file becomes a significant payload. There is no mechanism to request only the last 24 hours, only blast events, or only events from a specific unit.

**No conditional fetching.** status.json and power\_history.json are re-downloaded in full every 30 seconds, even when nothing has changed. Power history in particular grows continuously (one row per telemetry cycle), yet the dashboard discards all but the most recent window for charting. The wasted bandwidth is small for one unit but scales linearly with device count.

**No query capability.** Filtering by event type, confidence threshold, time range, or device ID must be done client-side after downloading everything. This inverts the correct pattern: the server should resolve the query and send only matching results.

**Read-modify-write race condition.** The Pi downloads manifest.json, inserts a new entry, and re-uploads with overwrite. If two units write to the same manifest simultaneously, one unit\'s detection is silently lost. This is a fundamental design limitation of using a flat file as a shared database.

3.5.2 Proposed Architecture: Lightweight API Layer

The Pi\'s upload path does not need to change. It continues writing blobs to Azure Storage. The change is on the **read side**: a lightweight API sits between blob storage and the dashboard, providing query, pagination, and caching. This can be implemented as an Azure Function (serverless, scales to zero when idle) or a small FastAPI service.

  ------------------------------------- ------------------------------------------------------------------------------------------------------------------------
  **Endpoint**                          **Behavior**
  GET /devices                          List all registered units with latest status (online/offline, battery level, last detection time).
  GET /devices/{id}/detections          Paginated detection history. Supports ?since=, ?until=, ?type=, ?confidence\_min=, ?limit=, ?offset= query parameters.
  GET /devices/{id}/status              Current device status. Supports ETag / If-None-Match for conditional requests (304 Not Modified if unchanged).
  GET /devices/{id}/power               Time-windowed power history. Supports ?range=24h (or 7d, 30d). Returns only the requested window.
  GET /devices/{id}/clips/{event\_id}   Proxied audio clip download with authentication. Eliminates direct public blob access.
  ------------------------------------- ------------------------------------------------------------------------------------------------------------------------

The API maintains a lightweight **index** of detections and device state. This can be as simple as an SQLite database (sufficient for tens of units) or a Cosmos DB collection (for larger scale, and already provisioned by Project 15). When the Pi uploads a new blob, an Azure Function trigger or Event Grid subscription updates the index automatically. Queries hit the index, not the raw blobs, so response times are fast regardless of total data volume.

3.5.3 Dashboard Changes

On the dashboard side, the API enables proper user-facing features that are currently impossible:

**Paginated detection list.** Load the most recent page on startup, fetch older pages on scroll. The dashboard never needs to hold the full history in memory.

**Filter controls.** Filter by device, date range, event type, and confidence threshold. The API resolves the filter server-side and returns only matching records.

**Conditional polling.** Use ETag or Last-Modified headers on status and power endpoints. The dashboard requests data every 30 seconds, but the response is a 304 (zero payload) if nothing changed. This reduces bandwidth to near-zero during quiet periods.

**Multi-device map.** The /devices endpoint provides all unit locations and statuses in a single call. The Leaflet map renders each device as a marker with color-coded status (green = online, amber = degraded, red = offline). Clicking a device loads its detection list and power chart.

**Server-Sent Events (optional).** For near-real-time alerting, the API can push new detection events to connected dashboards via SSE. This eliminates polling latency entirely: the dashboard receives a blast alert within seconds of the Pi\'s upload, rather than waiting up to 30 seconds for the next poll cycle.

3.5.4 Implementation Complexity

This is the most significant new development work in the report. The Pi-side changes (async pipeline, modularization) are refactors of existing code. The API layer is **net-new infrastructure** that must be built, deployed, and maintained. However, the scope is deliberately small: five read-only endpoints backed by an index that is populated by blob upload triggers. An Azure Function app with a Cosmos DB backend can be deployed in a single ARM template, scales automatically, and costs under \$5/month at low traffic. The dashboard changes (pagination, filters, conditional polling) are standard front-end patterns with well-established libraries.

4\. Security and Authentication

The current system has **no authentication at any layer**. The blob container is public, the dashboard requires no login, the remote config file is unsigned, the modem management API is unauthenticated, and API credentials are hardcoded in source code. This section catalogues every exposure and proposes a unified auth architecture.

4.1 Current Exposure Inventory

  ------------------------------------- -------------- --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Asset**                             **Severity**   **Exposure**
  Azure Blob Storage container          **CRITICAL**   Public anonymous read (verified: container set to public intentionally so the static dashboard can fetch blobs without a backend). Anyone with the storage account URL can download all detection records, WAV clips, status telemetry, and power history. Azure defaults to private --- this was an explicit configuration choice to avoid building an API layer.
  Sensor GPS coordinates                **CRITICAL**   Hardcoded in source (lat=-33.986582, lon=-71.860006) and uploaded in status.json and every detection record. Exact physical location of solar panel, battery, hydrophones, and modem is publicly discoverable. Theft and vandalism risk in remote coastal deployment.
  Twilio credentials                    **CRITICAL**   Account SID and auth token hardcoded as default values in the Python source (lines 49-50). If the source code is in a public or shared repository, these credentials are compromised. Allows unauthorized WhatsApp messaging on the OceanKind Twilio account.
  Dashboard                             **HIGH**       Zero authentication. Static HTML served on Azure Static Web Hosting. No login, no token, no access control. Anyone with the URL has full read access to all system data, detection history, and sensor locations.
  Remote config (remote\_config.json)   **HIGH**       Unsigned and unauthenticated. The Pi polls this blob and applies its values (detection thresholds, recording parameters, alert settings). If an attacker gains write access to the container, they can silently change system behavior: raise thresholds to suppress detection, disable alerts, or alter recording parameters.
  ZTE modem HTTP API                    **MEDIUM**     The Pi polls the modem\'s local HTTP API for signal strength without authentication. The modem is only reachable from the Pi\'s local network, but if the modem exposes management endpoints, anyone on the local network could reconfigure cellular connectivity.
  Azure IoT Hub connection string       **MEDIUM**     Stored in /etc/oceankind.env on the Pi. If the SD card is extracted or the overlay is disabled, the connection string is readable. Allows impersonation of the device to IoT Hub.
  Detection patterns                    **HIGH**       Public manifest.json reveals when and where blasts are detected, system sensitivity, coverage gaps, and online/offline periods. An adversary engaged in blast fishing can monitor this data to learn when the system is blind or to avoid monitored areas.
  Raw audio recordings                  **MEDIUM**     WAV clips in the public container may capture vessel engine signatures, marine life activity, or other acoustically sensitive data. Available to anyone without restriction.
  ------------------------------------- -------------- --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

4.2 Threat Model

The system faces three distinct threat categories:

**Illegal fishing operators (primary threat).** These actors have direct motivation to monitor, evade, or disable the system. Public detection data tells them where sensors are, when they are active, and what triggers an alert. With write access to the config blob, they could raise detection thresholds to suppress alerts without anyone noticing. The physical location data enables targeted vandalism or theft of hardware.

**Opportunistic attackers.** The publicly exposed Twilio credentials can be used for unauthorized messaging (spam, phishing) at OceanKind\'s expense. The public blob container could be used as free file hosting if write access is misconfigured. These are not targeted attacks but are likely given the credentials are in source code.

**Data integrity threats.** Without authentication on the config blob, anyone with write access can alter system behavior. Without signed uploads from the Pi, a compromised network path could inject false detections or suppress real ones. The system currently has no mechanism to verify that data in the blob was actually produced by an authorized device.

4.3 Proposed Architecture: Authenticated API With Credential Isolation

The blob container was set to public access deliberately: without a backend API, the static dashboard had no other way to read the data. **The lack of a backend is the root cause of the entire security exposure.** Building the API layer (Section 3.5) is therefore not just a scalability improvement --- it is a security prerequisite. Once an API exists, the container can be made private (a one-click change in the Azure Portal) and all data access flows through authenticated endpoints.

The solution unifies the scalability API with authentication. Rather than building two separate systems --- one for query/pagination and one for auth --- the **API becomes the single gateway for all data access**. No client (dashboard, mobile app, third-party integration) ever touches blob storage directly. The API holds all credentials and enforces access control.

4.3.1 Architecture Overview

The system divides into three trust zones:

**Edge devices (Pi / ESP32) authenticate to the API** using per-device API keys or mutual TLS certificates. Each device has a unique credential provisioned at deployment time, stored in /etc/oceankind.env (Pi) or NVS (ESP32). The device uses this credential to upload detection events, audio clips, status, and telemetry. The API validates the credential and rejects unauthorized uploads. Device keys can be rotated remotely via the API without physical access.

**The API backend holds all third-party credentials.** Twilio SID/token, Azure Blob Storage connection strings, IoT Hub keys --- none of these exist on the edge device or in the dashboard. The Pi sends a detection event to the API; the API decides whether to trigger a WhatsApp alert and calls Twilio server-side. This eliminates the entire class of credential-in-source vulnerabilities: the Pi never sees the Twilio token, the dashboard never sees the blob storage key.

**Dashboard users authenticate via the API.** The dashboard is no longer an anonymous static page reading public blobs. Users log in (email/password, OAuth via Google/Microsoft, or a simple invite-link token system), receive a session token, and all API requests include this token. The API enforces role-based access: viewer (read detections and status), operator (change config, manage devices), and admin (manage users, rotate keys).

4.3.2 Data Flow: Before and After

  ---------------------- ----------------------------------------------------------------------------------------- -----------------------------------------------------------------------------------------------------------------------
  **Flow**               **Current (No Auth)**                                                                     **Proposed (API-Gated)**
  Pi uploads detection   Pi writes directly to public blob container using storage account key embedded in code.   Pi POSTs to API with per-device API key. API validates, stores in private blob + index.
  WhatsApp alert         Pi calls Twilio API directly using hardcoded SID/token.                                   API triggers WhatsApp server-side. Pi never sees Twilio credentials.
  Dashboard reads data   Browser fetches public blob URLs. No auth. Anyone can read.                               Browser calls API with session token. API returns only data the user is authorized to see.
  Config change          Operator edits a public blob file. Pi polls and applies blindly.                          Operator submits config via API (authenticated). API validates, signs, and stores. Pi fetches signed config from API.
  Audio clip playback    Dashboard links directly to public blob URL.                                              Dashboard requests clip via API. API generates time-limited signed URL or proxies the stream.
  ---------------------- ----------------------------------------------------------------------------------------- -----------------------------------------------------------------------------------------------------------------------

4.3.3 API Credential Management

All secrets are stored in **Azure Key Vault** (or environment variables on a self-hosted backend), never in source code, never on edge devices, and never in the dashboard. The API reads secrets from Key Vault at startup and caches them in memory. Key rotation is performed in Key Vault and the API picks up new values on the next restart or via a refresh interval --- no code deployment required, no device update needed.

  --------------------------- ---------------------------- ---------------------------------------- --------------------------------------------------
  **Credential**              **Current Location**         **Proposed Location**                    **Rotation**
  Twilio SID + token          Hardcoded in Python source   Azure Key Vault (API-only)               In Key Vault, no code change
  Azure Blob Storage key      In Python source / env       Azure Key Vault (API-only)               In Key Vault, no code change
  IoT Hub connection string   /etc/oceankind.env on Pi     Azure Key Vault (API-only)               In Key Vault, no code change
  Per-device API key          Does not exist               /etc/oceankind.env (Pi) or NVS (ESP32)   API issues new key, Pi fetches on next heartbeat
  Dashboard user sessions     Does not exist               JWT / session token from API             Short-lived (hours), auto-refresh
  --------------------------- ---------------------------- ---------------------------------------- --------------------------------------------------

4.3.4 Dashboard Authentication Options

The dashboard needs a login gate. Three options, in order of implementation simplicity:

**1. Invite-link tokens (simplest).** An admin generates a unique link (e.g. dashboard.oceankind.org/?token=abc123). The token maps to a user/role in the API database. No password management, no OAuth provider. Suitable for a small, trusted team. Tokens can be revoked individually.

**2. OAuth via Google or Microsoft (recommended for organizations).** Users log in with their existing Google or Microsoft account. The API validates the OAuth token and checks an allowlist of authorized email addresses or domains. No password storage needed. Works well if the team already uses Google Workspace or Microsoft 365.

**3. Email/password with bcrypt (most flexible, most maintenance).** Traditional auth. Requires password hashing, reset flows, and session management. Only justified if the dashboard will have many users who don\'t share an OAuth provider.

Regardless of method, the API returns a **short-lived JWT** (e.g., 4 hours) that the dashboard includes in every API request. The JWT encodes the user\'s role (viewer, operator, admin) so the API can enforce permissions without a database lookup on every request.

4.3.5 Blob Container Lockdown

The Azure Blob Storage container must be made **private immediately**. This is a configuration change in the Azure Portal (Storage Account → Containers → set access level to Private) and takes effect within seconds. After lockdown:

The Pi authenticates uploads using a scoped SAS token or managed identity. The SAS token grants write-only access to specific blob paths (e.g., /devices/{device\_id}/clips/) and expires after a configurable period. The Pi receives a fresh SAS token from the API during each heartbeat.

The dashboard never accesses blobs directly. All reads go through the API, which generates time-limited read-only SAS URLs for audio clip playback. These URLs expire after minutes, not days, and are scoped to the specific clip requested.

The remote\_config.json blob is replaced by an API endpoint. The Pi fetches configuration from GET /devices/{id}/config with its device API key. The response is signed (HMAC or JWT) so the Pi can verify it was produced by the legitimate API server, not injected by a network-level attacker.

4.4 Immediate Security Actions

These actions should be taken **before any other development work**, as they address active vulnerabilities that exist in the running production system:

**1. Rotate the Twilio credentials.** Generate a new auth token in the Twilio console. Update /etc/oceankind.env on the Pi. Remove the hardcoded defaults from the Python source. Purge the old token from any .bak files, \_\_pycache\_\_ directories, and git history (use git filter-branch or BFG Repo-Cleaner).

**2. Make the blob container private.** Azure Portal → Storage Account → Containers → set access level to Private (one-click, takes effect in seconds). The container was set to public intentionally because the static dashboard has no other way to fetch data without a backend. This change will break the dashboard until either: (a) a temporary read-only SAS token is hardcoded into the dashboard, or (b) the API backend (Sections 3.5 and 4.3) is deployed. Option (a) is a quick interim fix; option (b) is the permanent solution. Either is preferable to continued public exposure.

**3. Remove GPS coordinates from source code.** Move lat/lon to /etc/oceankind.env alongside other deployment-specific values. Each unit should have its own coordinates set at provisioning time, not compiled into the codebase.

**4. Audit git history for secrets.** If the repository has ever been pushed to GitHub or any shared remote, the Twilio credentials and any other secrets in historical commits are compromised regardless of current file contents. Run a secret scanner (trufflehog, gitleaks) and purge findings.

5\. Operational Risks Carried Forward

The initial audit identified 13 issues across security, reliability, and maintainability. The following remain the highest priority and are affected by the hardware and software decisions in this report.

  ------------------------------ -------------- -----------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Issue**                      **Severity**   **Impact of Proposed Changes**
  Twilio credentials in source   **CRITICAL**   Resolved by credential isolation (Section 4.3.3). Credentials move to Azure Key Vault, accessed only by API backend. Must be rotated immediately (Section 4.4).
  Detection gaps (deaf window)   **CRITICAL**   Resolved by the async pipeline architecture (Section 3.2). Continuous capture with queue decoupling eliminates dead time.
  Public blob container          **HIGH**       Resolved by API-gated architecture (Section 4.3). Container made private, all access through authenticated API with time-limited SAS URLs.
  Silent-deaf on model failure   **HIGH**       Resolved by modularization: classifier.py should fail loudly (heartbeat flag + WhatsApp alert) and fall back to RMS detection.
  OTA can strand a remote node   **HIGH**       On Pi: health check + auto-rollback needed. On ESP32: native A/B OTA with automatic rollback is built into ESP-IDF.
  SD card fragility              **HIGH**       On Pi: overlay FS mitigates but adds complexity. On ESP32: eliminated entirely (firmware in flash, no filesystem writes).
  Hardcoded audio device         **MEDIUM**     Resolved by capture module auto-detecting device by name (already implemented in deprecated audio\_capture.py).
  Two divergent codebases        **HIGH**       Resolved by the proposed modular rewrite. Archive deprecated code, single canonical codebase.
  ------------------------------ -------------- -----------------------------------------------------------------------------------------------------------------------------------------------------------------

6\. Recommendations

6.1 Immediate Actions (Before Any Rewrite)

**1. Execute the four immediate security actions in Section 4.4.** Rotate Twilio credentials, make the blob container private, remove GPS from source code, and audit git history for secrets. These address active vulnerabilities in the running production system.

**2. Validate the classifier against distant blast recordings.** Before committing to hardware changes, confirm the model can detect events at the expected operational range. If the training set only covers nearby blasts, the model must be retrained regardless.

6.2 Short-Term: Software Rewrite (Pi 4, Current Hardware)

Restructure the monolith into the async pipeline described in Section 3.2. This can be done on the current Pi 4 hardware and delivers the biggest reliability improvement: eliminating detection gaps. Use the deprecated modular capture code as a foundation. Target: continuous capture with zero deaf time, modular code, proper error isolation. Begin dashboard API planning (Section 3.5) in parallel.

6.3 Medium-Term: Hardware Migration

The recommended migration path depends on the outcome of the distant-blast validation:

**If the HifiBerry ADC Pro is required** (distant detection demands 24-bit capture quality): migrate to **Raspberry Pi Zero 2W**. Same software stack, 85% power reduction, same I2S HAT compatibility. SD card fragility remains but is manageable with overlay FS.

**If a simpler ADC suffices** (detection is primarily for nearby/medium-range blasts): migrate to **ESP32-S3 with external I2S ADC**. Eliminates SD card entirely, drops power to \~0.2W, enables native A/B OTA. Requires C port of feature extraction (\~1-2 weeks development).

**Cloud offload is a viable complement to either path** if an edge pre-filter (RMS energy gate) reduces the volume of clips sent over cellular. Full cloud offload without pre-filtering is cost-prohibitive for continuous monitoring.

6.4 Long-Term: Dashboard API + Multi-Unit Scalability

Deploy the API layer described in Section 3.5. Replace the shared manifest.json with an indexed database (Cosmos DB or SQLite). Migrate the dashboard to API-backed pagination, filtering, and conditional polling. Add multi-device support to the map and detection views. IoT Hub device registration and Stream Analytics (already documented in PROJECT15.md but not implemented) provide the correct foundation for device management at scale.

\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

*Report prepared by Futurity Systems*

*OceanKind / Mar Futura \| System Architecture Review \| July 2026*
