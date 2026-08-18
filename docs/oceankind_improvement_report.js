const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
        Header, Footer, AlignmentType, LevelFormat, HeadingLevel,
        BorderStyle, WidthType, ShadingType, PageNumber, PageBreak } = require('docx');
const fs = require('fs');

// --- Futurity style config ---
const styles = {
  default: { document: { run: { font: "Arial", size: 22 } } },
  paragraphStyles: [
    { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { size: 32, bold: true, font: "Arial" },
      paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 } },
    { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { size: 26, bold: true, font: "Arial" },
      paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 1 } },
    { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { size: 22, bold: true, font: "Arial" },
      paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 2 } }
  ]
};

const pageProperties = {
  page: {
    size: { width: 11906, height: 16838 },
    margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
  }
};

const tableBorder = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const tableBorders = { top: tableBorder, bottom: tableBorder, left: tableBorder, right: tableBorder };
const cellMargins = { top: 80, bottom: 80, left: 120, right: 120 };
const TABLE_WIDTH = 9026;

function headerCell(text, width) {
  return new TableCell({
    borders: tableBorders, width: { size: width, type: WidthType.DXA },
    shading: { fill: "F3F3F3", type: ShadingType.CLEAR }, margins: cellMargins,
    children: [new Paragraph({ children: [new TextRun({ text, bold: true, size: 20, font: "Arial" })] })]
  });
}

function bodyCell(text, width, opts = {}) {
  return new TableCell({
    borders: tableBorders, width: { size: width, type: WidthType.DXA }, margins: cellMargins,
    children: [new Paragraph({
      alignment: opts.rightAlign ? AlignmentType.RIGHT : AlignmentType.LEFT,
      children: [new TextRun({ text, size: 20, font: "Arial", bold: opts.bold || false, color: opts.color || "000000" })]
    })]
  });
}

function p(text, opts = {}) {
  return new Paragraph({
    spacing: { after: opts.after || 120, before: opts.before || 0, line: 276 },
    alignment: opts.center ? AlignmentType.CENTER : AlignmentType.LEFT,
    children: Array.isArray(text) ? text : [new TextRun({ text, size: opts.size || 22, font: "Arial", color: opts.color || "000000", bold: opts.bold || false, italics: opts.italics || false })]
  });
}

function h1(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text, font: "Arial" })] });
}
function h2(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text, font: "Arial" })] });
}
function h3(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun({ text, font: "Arial" })] });
}

function richP(runs, opts = {}) {
  return new Paragraph({
    spacing: { after: opts.after || 120, before: opts.before || 0, line: 276 },
    children: runs.map(r => new TextRun({ size: 22, font: "Arial", ...r }))
  });
}

// --- Document content ---
const doc = new Document({
  styles,
  sections: [{
    properties: pageProperties,
    children: [
      // Title block
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 },
        children: [new TextRun({ text: "OCEANKIND / MAR FUTURA", bold: true, size: 36, font: "Arial" })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
        children: [new TextRun({ text: "System Improvement Report", size: 26, font: "Arial" })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
        children: [new TextRun({ text: "Hardware  |  Software  |  Scalability  |  Security  |  July 2026", size: 22, font: "Arial", color: "666666" })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 300 },
        children: [new TextRun({ text: "________________________________________________________________________________________________________", size: 22, font: "Arial", color: "999999" })] }),

      // =====================================================================
      // EXECUTIVE SUMMARY
      // =====================================================================
      h1("1. Executive Summary"),

      richP([
        { text: "OceanKind is an underwater acoustic monitoring system designed to detect illegal blast fishing. The current deployment uses a " },
        { text: "Raspberry Pi 4 Model B", bold: true },
        { text: " with a HifiBerry ADC Pro HAT, two Aquarian H5 hydrophones, solar power, and cellular connectivity. A scikit-learn ML classifier runs locally on 5-second audio clips, and alerts are pushed to Azure Blob Storage and delivered via WhatsApp (Twilio)." },
      ]),

      richP([
        { text: "This report assesses three areas for improvement: " },
        { text: "hardware right-sizing", bold: true },
        { text: " (the current compute platform is over-specified for the workload), " },
        { text: "software architecture", bold: true },
        { text: " (the single-threaded monolith creates detection gaps and operational fragility), and " },
        { text: "dashboard scalability", bold: true },
        { text: " (the current blob-polling pattern cannot support filtering, pagination, or multiple units), and " },
        { text: "security", bold: true },
        { text: " (the entire system is publicly accessible with no authentication at any layer). The goal is a system that is cheaper to build, uses less power, is more reliable in remote deployments, properly secured, and can scale to multiple units without architectural changes." },
      ]),

      // =====================================================================
      // HARDWARE ASSESSMENT
      // =====================================================================
      h1("2. Hardware Assessment"),

      h2("2.1 Current Hardware Profile"),

      new Table({
        width: { size: TABLE_WIDTH, type: WidthType.DXA },
        columnWidths: [3000, 3013, 3013],
        rows: [
          new TableRow({ children: [headerCell("Component", 3000), headerCell("Current", 3013), headerCell("Notes", 3013)] }),
          new TableRow({ children: [bodyCell("SBC", 3000), bodyCell("Raspberry Pi 4B (2GB+)", 3013), bodyCell("4-core ARM Cortex-A72, 1.5GHz", 3013)] }),
          new TableRow({ children: [bodyCell("Audio ADC (?)", 3000), bodyCell("HifiBerry DAC+ ADC Pro — or — Raspberry Pi Codec Zero", 3013), bodyCell("Codebase references HifiBerry; system diagram shows Codec Zero. Needs clarification.", 3013)] }),
          new TableRow({ children: [bodyCell("Power draw", 3000), bodyCell("~3.5W total system", 3013), bodyCell("Pi alone: ~3.0W", 3013)] }),
          new TableRow({ children: [bodyCell("Storage", 3000), bodyCell("MicroSD card", 3013), bodyCell("Single point of failure", 3013)] }),
          new TableRow({ children: [bodyCell("Connectivity", 3000), bodyCell("ZTE 4G modem (USB/Ethernet)", 3013), bodyCell("Cellular, no WiFi needed", 3013)] }),
          new TableRow({ children: [bodyCell("Solar panel", 3000), bodyCell("40-100W", 3013), bodyCell("Sized for Pi power draw", 3013)] }),
          new TableRow({ children: [bodyCell("Battery", 3000), bodyCell("12V LiFePO4, 20-50Ah", 3013), bodyCell("~360Wh nominal", 3013)] }),
        ]
      }),

      p("", { after: 60 }),

      h2("2.2 ML Model Analysis"),

      richP([
        { text: "The classification model is a " },
        { text: "StandardScaler followed by LogisticRegression", bold: true },
        { text: " pipeline, packaged in a 3 KB joblib file. The model takes 52 input features (20 MFCCs with mean and standard deviation, plus 12 spectral descriptors) and produces a binary classification (blast vs. background) with a single dot product of 52 weights plus a bias term, followed by a sigmoid function. This is " },
        { text: "53 parameters total", bold: true },
        { text: ". The inference step itself is computationally trivial and could execute in microseconds on any modern microcontroller." },
      ]),

      new Table({
        width: { size: TABLE_WIDTH, type: WidthType.DXA },
        columnWidths: [3500, 5526],
        rows: [
          new TableRow({ children: [headerCell("Property", 3500), headerCell("Value", 5526)] }),
          new TableRow({ children: [bodyCell("Model type", 3500), bodyCell("Pipeline: StandardScaler + LogisticRegression", 5526)] }),
          new TableRow({ children: [bodyCell("Input features", 3500), bodyCell("52 (20 MFCCs x2 + 12 spectral descriptors)", 5526)] }),
          new TableRow({ children: [bodyCell("Parameters", 3500), bodyCell("53 (52 weights + 1 bias)", 5526)] }),
          new TableRow({ children: [bodyCell("Model file size", 3500), bodyCell("3.0 KB (joblib serialized)", 5526)] }),
          new TableRow({ children: [bodyCell("Training sample rate", 3500), bodyCell("22,050 Hz (mono)", 5526)] }),
          new TableRow({ children: [bodyCell("Clip length", 3500), bodyCell("5 seconds", 5526)] }),
          new TableRow({ children: [bodyCell("Inference cost", 3500), bodyCell("52 multiply-accumulates + sigmoid", 5526)] }),
        ]
      }),

      p("", { after: 60 }),

      richP([
        { text: "The computational bottleneck is not inference but " },
        { text: "feature extraction", bold: true },
        { text: ". The current implementation uses librosa (Python) to compute MFCCs and spectral descriptors, which takes 1-3 seconds per 5-second clip on the Pi 4. This step requires FFT computation, mel-filterbank application, and discrete cosine transform. On an ESP32-S3 or similar microcontroller, these operations would need to be reimplemented in C using libraries such as ESP-ADF or CMSIS-DSP, which is feasible but represents the primary porting effort." },
      ]),

      h2("2.3 The Raspberry Pi 4 Is Over-Specified"),

      richP([
        { text: "The Pi 4 provides 4 CPU cores at 1.5 GHz, up to 8 GB RAM, USB 3.0, gigabit Ethernet, dual HDMI, and a full desktop-class Linux environment. The OceanKind workload uses " },
        { text: "one core intermittently", bold: true },
        { text: ", approximately 50-100 MB of RAM (Python + librosa + model), no display output, and no USB 3.0 peripherals. The system is paying the power budget of a desktop SBC (~3W) to run a script that records audio, performs a small FFT-based feature extraction, executes a 52-parameter dot product, and uploads JSON over cellular." },
      ]),

      richP([
        { text: "More critically, the Pi 4 introduces " },
        { text: "operational fragility", bold: true },
        { text: " in a remote solar deployment. The MicroSD card is a well-documented single point of failure: repeated writes cause wear, and power loss during writes can corrupt the filesystem. The team has already implemented overlay filesystem protection (protect_sd.sh) and a two-phase OTA update process to mitigate this, but both add complexity and introduce their own failure modes. The OTA update, in particular, requires two reboots and can leave the unit in an unrecoverable state if the process fails mid-way." },
      ]),

      h2("2.4 Hardware Alternatives"),

      new Table({
        width: { size: TABLE_WIDTH, type: WidthType.DXA },
        columnWidths: [1800, 1200, 1200, 1200, 1126, 2500],
        rows: [
          new TableRow({ children: [
            headerCell("Platform", 1800), headerCell("Power", 1200), headerCell("RAM", 1200),
            headerCell("Storage", 1200), headerCell("Cost", 1126), headerCell("Trade-off", 2500)
          ]}),
          new TableRow({ children: [
            bodyCell("Raspberry Pi 4B", 1800), bodyCell("~3.0W", 1200), bodyCell("2-8GB", 1200),
            bodyCell("SD card", 1200), bodyCell("~$55", 1126), bodyCell("Current. Overkill, SD fragility.", 2500)
          ]}),
          new TableRow({ children: [
            bodyCell("Raspberry Pi Zero 2W", 1800), bodyCell("~0.4W", 1200), bodyCell("512MB", 1200),
            bodyCell("SD card", 1200), bodyCell("~$15", 1126), bodyCell("Runs Python/librosa. SD issue remains.", 2500)
          ]}),
          new TableRow({ children: [
            bodyCell("ESP32-S3 + PSRAM", 1800), bodyCell("~0.2W", 1200), bodyCell("8MB", 1200),
            bodyCell("Flash (no SD)", 1200), bodyCell("~$8", 1126), bodyCell("Requires C port of feature extraction.", 2500)
          ]}),
          new TableRow({ children: [
            bodyCell("Cloud offload", 1800), bodyCell("~0.1W*", 1200), bodyCell("Minimal", 1200),
            bodyCell("N/A", 1200), bodyCell("Variable", 1126), bodyCell("Record + send WAVs. High cellular cost.", 2500)
          ]}),
        ]
      }),

      p("* Microcontroller only, excluding modem.", { after: 60, italics: true, size: 18, color: "666666" }),

      h3("2.4.1 Raspberry Pi Zero 2W"),
      richP([
        { text: "The Pi Zero 2W (quad-core ARM Cortex-A53, 512 MB RAM) can run the existing Python codebase with minimal changes. Power draw drops from ~3W to ~0.4W, allowing a significantly smaller solar panel (10-20W) and battery (10Ah). The 512 MB of RAM is sufficient for the workload. However, the " },
        { text: "SD card fragility remains", bold: true },
        { text: ", and librosa feature extraction will be slower (~3-5s per clip vs. 1-3s on the Pi 4). This is the lowest-effort migration path." },
      ]),

      h3("2.4.2 ESP32-S3 With PSRAM"),
      richP([
        { text: "An ESP32-S3 module with 8 MB PSRAM (e.g., ESP32-S3-WROOM-1-N16R8) eliminates the SD card entirely: firmware lives in flash, OTA updates are handled via dual-partition A/B schemes native to ESP-IDF, and there is no filesystem to corrupt. Power draw drops to ~0.2W for the MCU. The 53-parameter logistic regression model is trivial to run. The challenge is " },
        { text: "porting the MFCC feature extraction to C", bold: true },
        { text: ". Libraries exist (ESP-ADF, CMSIS-DSP) but this represents a development effort of approximately 1-2 weeks. Audio capture via I2S is native to ESP-IDF and well-documented." },
      ]),

      h3("2.4.3 Cloud Offload Architecture"),
      richP([
        { text: "In this model, the edge device (ESP32 or similar) only records audio and transmits raw or compressed clips to a cloud endpoint, where classification runs server-side. This eliminates the need to port feature extraction to embedded C. However, continuous 5-second clips at 22 kHz mono 16-bit are approximately 220 KB each. At one clip per cycle (~8-10 seconds total), this produces " },
        { text: "~1.5-2 GB of cellular data per day", bold: true },
        { text: ". Compression (Opus, FLAC) could reduce this by 50-70%, but the cellular cost remains significant for remote deployments. A hybrid approach (edge pre-filter on simple RMS energy, only upload clips above a threshold) would reduce volume substantially." },
      ]),

      h2("2.5 Detection Range Consideration"),

      richP([
        { text: "The system is intended to detect blasts that may occur " },
        { text: "hundreds or thousands of kilometers away", bold: true },
        { text: ". Underwater sound propagates efficiently, particularly through the SOFAR channel, where low-frequency signals can travel transcontinental distances. However, distant blasts arrive as heavily attenuated, low-frequency signals mixed with ambient ocean noise. Whether the current classifier can detect these depends on: (a) what the training data included (nearby blasts only, or also distant signatures), (b) the noise floor of the capture chain, and (c) the frequency response of the hydrophones." },
      ]),

      richP([
        { text: "The Aquarian H5 has a sensitivity of approximately -185 dB re 1V/µPa across 10 Hz-100 kHz. The HifiBerry ADC Pro provides 24-bit resolution with a low noise floor. For distant detection, the " },
        { text: "ADC quality and hydrophone sensitivity matter more than compute power", bold: true },
        { text: ". If the system moves to a cheaper ADC (e.g., ESP32 built-in ADC at 12-bit), distant blast detection capability would likely degrade significantly. An external I2S ADC module (e.g., INMP441 MEMS mic or a PCM1808-based board) would preserve capture quality while running on a smaller compute platform." },
      ]),

      richP([
        { text: "Recommendation: before committing to a hardware downgrade, " },
        { text: "validate the classifier against labeled distant-blast recordings", bold: true },
        { text: ". If the training set only contains nearby events, the model will need retraining regardless of hardware choice." },
      ]),

      // Page break
      new Paragraph({ children: [new PageBreak()] }),

      // =====================================================================
      // SOFTWARE ARCHITECTURE
      // =====================================================================
      h1("3. Software Architecture"),

      h2("3.1 Current Architecture: Single-Threaded Monolith"),

      richP([
        { text: "The entire system runs as a single Python file (marfutura_iot_audio.py, 1,309 lines) executing a sequential loop on one thread. Each cycle performs: remote config check, 5-second audio recording (blocking arecord subprocess), librosa feature extraction + classification, WhatsApp alert (if triggered), blob upload, IoT Hub message, modem signal poll, Victron solar telemetry read, status upload, CSV logging, and power history upload. " },
        { text: "During classification and upload, the hydrophone is not listening.", bold: true },
      ]),

      richP([
        { text: "For a system whose purpose is detecting sub-second acoustic events, this architecture has a fundamental flaw: " },
        { text: "the detection duty cycle is well below 100%", bold: true },
        { text: ". A conservative estimate puts the deaf window at 3-15 seconds per cycle (feature extraction + network operations), meaning the system may miss 30-60% of actual blasts depending on cellular latency and upload times." },
      ]),

      h2("3.2 Proposed Architecture: Asynchronous Pipeline With Queues"),

      richP([
        { text: "The system should be restructured into independent, asynchronous components connected by queues. Each component runs on its own thread or process, fails independently, and does not block the others." },
      ]),

      new Table({
        width: { size: TABLE_WIDTH, type: WidthType.DXA },
        columnWidths: [2200, 4326, 2500],
        rows: [
          new TableRow({ children: [headerCell("Layer", 2200), headerCell("Responsibility", 4326), headerCell("Isolation", 2500)] }),
          new TableRow({ children: [
            bodyCell("Capture", 2200, { bold: true }),
            bodyCell("Continuous audio recording via ring buffer. Never stops. Writes fixed-length segments to an internal queue.", 4326),
            bodyCell("Own thread. No network, no disk I/O.", 2500)
          ]}),
          new TableRow({ children: [
            bodyCell("Detection", 2200, { bold: true }),
            bodyCell("Pulls audio segments from the capture queue. Extracts features, runs classifier. If alert triggered, places event on the alert queue.", 4326),
            bodyCell("Own thread. CPU-bound only.", 2500)
          ]}),
          new TableRow({ children: [
            bodyCell("Transport", 2200, { bold: true }),
            bodyCell("Pulls events from the alert queue. Uploads WAV to blob, sends WhatsApp, updates manifest, sends IoT Hub message. Handles retries internally.", 4326),
            bodyCell("Own thread. Network-bound. Can lag without affecting detection.", 2500)
          ]}),
          new TableRow({ children: [
            bodyCell("Telemetry", 2200, { bold: true }),
            bodyCell("On its own timer: polls solar controller, modem signal, system stats. Uploads status.json and power_history.json.", 4326),
            bodyCell("Own thread. Independent timer.", 2500)
          ]}),
          new TableRow({ children: [
            bodyCell("Config", 2200, { bold: true }),
            bodyCell("Polls remote_config.json periodically. Validates and clamps incoming values. Updates shared config object with locking.", 4326),
            bodyCell("Own thread. Validates before applying.", 2500)
          ]}),
        ]
      }),

      p("", { after: 60 }),

      richP([
        { text: "The critical invariant is that " },
        { text: "the capture layer never blocks", bold: true },
        { text: ". Whether the cellular connection is down, the blob upload is slow, or the Twilio API is timing out, audio recording continues uninterrupted. Events that cannot be transmitted are queued locally with bounded retries (the pending alert buffer pattern already exists in the codebase and should be generalized)." },
      ]),

      richP([
        { text: "The deprecated modular codebase (main.py, audio_capture.py, detector.py, alert.py, clip_saver.py) already implements the capture layer correctly using sounddevice with a callback-driven queue. This code should be recovered and used as the foundation for the capture component." },
      ]),

      h2("3.3 Code Modularization"),

      richP([
        { text: "The monolith should be broken into focused modules. Each module should have a single responsibility, a clear interface, and no knowledge of the others beyond what it receives through queues or shared configuration." },
      ]),

      new Table({
        width: { size: TABLE_WIDTH, type: WidthType.DXA },
        columnWidths: [2500, 2500, 4026],
        rows: [
          new TableRow({ children: [headerCell("Module", 2500), headerCell("Current Source", 2500), headerCell("Scope", 4026)] }),
          new TableRow({ children: [bodyCell("capture.py", 2500), bodyCell("audio_capture.py (deprecated)", 2500), bodyCell("Continuous I2S/ALSA capture, ring buffer, auto-detect audio device by name", 4026)] }),
          new TableRow({ children: [bodyCell("classifier.py", 2500), bodyCell("Lines 276-357 of monolith", 2500), bodyCell("Feature extraction + model inference. RMS fallback if model fails to load. Loud failure mode.", 4026)] }),
          new TableRow({ children: [bodyCell("transport.py", 2500), bodyCell("Lines 196-270, 911-998", 2500), bodyCell("Azure Blob upload, manifest management, WhatsApp via Twilio, IoT Hub messaging, retry queue.", 4026)] }),
          new TableRow({ children: [bodyCell("telemetry.py", 2500), bodyCell("Lines 360-855", 2500), bodyCell("Victron VE.Direct, modem signal, system stats, battery alert state machine, CSV logging.", 4026)] }),
          new TableRow({ children: [bodyCell("config.py", 2500), bodyCell("Lines 30-88, 858-863", 2500), bodyCell("All constants, env var loading, remote config polling with validation and safe clamping.", 4026)] }),
          new TableRow({ children: [bodyCell("main.py", 2500), bodyCell("Lines 1142-1308", 2500), bodyCell("Orchestrator only: starts threads, wires queues, handles shutdown.", 4026)] }),
        ]
      }),

      p("", { after: 60 }),

      h2("3.4 Dashboard Architecture"),

      richP([
        { text: "The dashboard (dashboard/index.html, 76 KB) is a " },
        { text: "self-contained static web page hosted on Azure Static Web Hosting", bold: true },
        { text: ", completely independent from the Raspberry Pi. The Pi and the dashboard never communicate directly. Azure Blob Storage serves as the intermediary: the Pi pushes three JSON files (manifest.json, status.json, power_history.json) and WAV clips to a public blob container; the dashboard polls these files every 30 seconds from the user's browser." },
      ]),

      richP([
        { text: "This decoupled architecture is a strength: the Pi has no HTTP server to maintain, the dashboard works from any browser, and either side can be updated independently. However, the public unauthenticated blob container is a critical security concern (see Section 4). The dashboard should transition to reading from the authenticated API described in Sections 3.5 and 4.3." },
      ]),

      h2("3.5 Dashboard Scalability"),

      richP([
        { text: "The current dashboard architecture fetches " },
        { text: "all data, every 30 seconds, unconditionally", bold: true },
        { text: ". There is no pagination, no date filtering, no conditional requests, and no way to ask for only what changed since the last poll. This design works for a single device with a short history, but breaks down as detection counts grow and additional units come online." },
      ]),

      h3("3.5.1 Current Scalability Problems"),

      richP([
        { text: "manifest.json grows without bound. ", bold: true },
        { text: "Every detection is appended to a single JSON file. The dashboard downloads the entire detection history on every poll cycle. After months of continuous operation, or with multiple units feeding the same container, this file becomes a significant payload. There is no mechanism to request only the last 24 hours, only blast events, or only events from a specific unit." },
      ]),

      richP([
        { text: "No conditional fetching. ", bold: true },
        { text: "status.json and power_history.json are re-downloaded in full every 30 seconds, even when nothing has changed. Power history in particular grows continuously (one row per telemetry cycle), yet the dashboard discards all but the most recent window for charting. The wasted bandwidth is small for one unit but scales linearly with device count." },
      ]),

      richP([
        { text: "No query capability. ", bold: true },
        { text: "Filtering by event type, confidence threshold, time range, or device ID must be done client-side after downloading everything. This inverts the correct pattern: the server should resolve the query and send only matching results." },
      ]),

      richP([
        { text: "Read-modify-write race condition. ", bold: true },
        { text: "The Pi downloads manifest.json, inserts a new entry, and re-uploads with overwrite. If two units write to the same manifest simultaneously, one unit's detection is silently lost. This is a fundamental design limitation of using a flat file as a shared database." },
      ]),

      h3("3.5.2 Proposed Architecture: Lightweight API Layer"),

      richP([
        { text: "The Pi's upload path does not need to change. It continues writing blobs to Azure Storage. The change is on the " },
        { text: "read side", bold: true },
        { text: ": a lightweight API sits between blob storage and the dashboard, providing query, pagination, and caching. This can be implemented as an Azure Function (serverless, scales to zero when idle) or a small FastAPI service." },
      ]),

      new Table({
        width: { size: TABLE_WIDTH, type: WidthType.DXA },
        columnWidths: [3200, 5826],
        rows: [
          new TableRow({ children: [headerCell("Endpoint", 3200), headerCell("Behavior", 5826)] }),
          new TableRow({ children: [
            bodyCell("GET /devices", 3200),
            bodyCell("List all registered units with latest status (online/offline, battery level, last detection time).", 5826)
          ]}),
          new TableRow({ children: [
            bodyCell("GET /devices/{id}/detections", 3200),
            bodyCell("Paginated detection history. Supports ?since=, ?until=, ?type=, ?confidence_min=, ?limit=, ?offset= query parameters.", 5826)
          ]}),
          new TableRow({ children: [
            bodyCell("GET /devices/{id}/status", 3200),
            bodyCell("Current device status. Supports ETag / If-None-Match for conditional requests (304 Not Modified if unchanged).", 5826)
          ]}),
          new TableRow({ children: [
            bodyCell("GET /devices/{id}/power", 3200),
            bodyCell("Time-windowed power history. Supports ?range=24h (or 7d, 30d). Returns only the requested window.", 5826)
          ]}),
          new TableRow({ children: [
            bodyCell("GET /devices/{id}/clips/{event_id}", 3200),
            bodyCell("Proxied audio clip download with authentication. Eliminates direct public blob access.", 5826)
          ]}),
        ]
      }),

      p("", { after: 60 }),

      richP([
        { text: "The API maintains a lightweight " },
        { text: "index", bold: true },
        { text: " of detections and device state. This can be as simple as an SQLite database (sufficient for tens of units) or a Cosmos DB collection (for larger scale, and already provisioned by Project 15). When the Pi uploads a new blob, an Azure Function trigger or Event Grid subscription updates the index automatically. Queries hit the index, not the raw blobs, so response times are fast regardless of total data volume." },
      ]),

      h3("3.5.3 Dashboard Changes"),

      richP([
        { text: "On the dashboard side, the API enables proper user-facing features that are currently impossible:" },
      ]),

      richP([
        { text: "Paginated detection list. ", bold: true },
        { text: "Load the most recent page on startup, fetch older pages on scroll. The dashboard never needs to hold the full history in memory." },
      ]),

      richP([
        { text: "Filter controls. ", bold: true },
        { text: "Filter by device, date range, event type, and confidence threshold. The API resolves the filter server-side and returns only matching records." },
      ]),

      richP([
        { text: "Conditional polling. ", bold: true },
        { text: "Use ETag or Last-Modified headers on status and power endpoints. The dashboard requests data every 30 seconds, but the response is a 304 (zero payload) if nothing changed. This reduces bandwidth to near-zero during quiet periods." },
      ]),

      richP([
        { text: "Multi-device map. ", bold: true },
        { text: "The /devices endpoint provides all unit locations and statuses in a single call. The Leaflet map renders each device as a marker with color-coded status (green = online, amber = degraded, red = offline). Clicking a device loads its detection list and power chart." },
      ]),

      richP([
        { text: "Server-Sent Events (optional). ", bold: true },
        { text: "For near-real-time alerting, the API can push new detection events to connected dashboards via SSE. This eliminates polling latency entirely: the dashboard receives a blast alert within seconds of the Pi's upload, rather than waiting up to 30 seconds for the next poll cycle." },
      ]),

      h3("3.5.4 Implementation Complexity"),

      richP([
        { text: "This is the most significant new development work in the report. The Pi-side changes (async pipeline, modularization) are refactors of existing code. The API layer is " },
        { text: "net-new infrastructure", bold: true },
        { text: " that must be built, deployed, and maintained. However, the scope is deliberately small: five read-only endpoints backed by an index that is populated by blob upload triggers. An Azure Function app with a Cosmos DB backend can be deployed in a single ARM template, scales automatically, and costs under $5/month at low traffic. The dashboard changes (pagination, filters, conditional polling) are standard front-end patterns with well-established libraries." },
      ]),

      // Page break
      new Paragraph({ children: [new PageBreak()] }),

      // =====================================================================
      // SECURITY & AUTHENTICATION
      // =====================================================================
      h1("4. Security and Authentication"),

      richP([
        { text: "The current system has " },
        { text: "no authentication at any layer", bold: true },
        { text: ". The blob container is public, the dashboard requires no login, the remote config file is unsigned, the modem management API is unauthenticated, and API credentials are hardcoded in source code. This section catalogues every exposure and proposes a unified auth architecture." },
      ]),

      h2("4.1 Current Exposure Inventory"),

      new Table({
        width: { size: TABLE_WIDTH, type: WidthType.DXA },
        columnWidths: [2400, 1000, 5626],
        rows: [
          new TableRow({ children: [headerCell("Asset", 2400), headerCell("Severity", 1000), headerCell("Exposure", 5626)] }),
          new TableRow({ children: [
            bodyCell("Azure Blob Storage container", 2400),
            bodyCell("CRITICAL", 1000, { bold: true, color: "CC0000" }),
            bodyCell("Public anonymous read (verified: container set to public intentionally so the static dashboard can fetch blobs without a backend). Anyone with the storage account URL can download all detection records, WAV clips, status telemetry, and power history. Azure defaults to private — this was an explicit configuration choice to avoid building an API layer.", 5626)
          ]}),
          new TableRow({ children: [
            bodyCell("Sensor GPS coordinates", 2400),
            bodyCell("CRITICAL", 1000, { bold: true, color: "CC0000" }),
            bodyCell("Hardcoded in source (lat=-33.986582, lon=-71.860006) and uploaded in status.json and every detection record. Exact physical location of solar panel, battery, hydrophones, and modem is publicly discoverable. Theft and vandalism risk in remote coastal deployment.", 5626)
          ]}),
          new TableRow({ children: [
            bodyCell("Twilio credentials", 2400),
            bodyCell("CRITICAL", 1000, { bold: true, color: "CC0000" }),
            bodyCell("Account SID and auth token hardcoded as default values in the Python source (lines 49-50). If the source code is in a public or shared repository, these credentials are compromised. Allows unauthorized WhatsApp messaging on the OceanKind Twilio account.", 5626)
          ]}),
          new TableRow({ children: [
            bodyCell("Dashboard", 2400),
            bodyCell("HIGH", 1000, { bold: true, color: "DD6600" }),
            bodyCell("Zero authentication. Static HTML served on Azure Static Web Hosting. No login, no token, no access control. Anyone with the URL has full read access to all system data, detection history, and sensor locations.", 5626)
          ]}),
          new TableRow({ children: [
            bodyCell("Remote config (remote_config.json)", 2400),
            bodyCell("HIGH", 1000, { bold: true, color: "DD6600" }),
            bodyCell("Unsigned and unauthenticated. The Pi polls this blob and applies its values (detection thresholds, recording parameters, alert settings). If an attacker gains write access to the container, they can silently change system behavior: raise thresholds to suppress detection, disable alerts, or alter recording parameters.", 5626)
          ]}),
          new TableRow({ children: [
            bodyCell("ZTE modem HTTP API", 2400),
            bodyCell("MEDIUM", 1000, { bold: true }),
            bodyCell("The Pi polls the modem's local HTTP API for signal strength without authentication. The modem is only reachable from the Pi's local network, but if the modem exposes management endpoints, anyone on the local network could reconfigure cellular connectivity.", 5626)
          ]}),
          new TableRow({ children: [
            bodyCell("Azure IoT Hub connection string", 2400),
            bodyCell("MEDIUM", 1000, { bold: true }),
            bodyCell("Stored in /etc/oceankind.env on the Pi. If the SD card is extracted or the overlay is disabled, the connection string is readable. Allows impersonation of the device to IoT Hub.", 5626)
          ]}),
          new TableRow({ children: [
            bodyCell("Detection patterns", 2400),
            bodyCell("HIGH", 1000, { bold: true, color: "DD6600" }),
            bodyCell("Public manifest.json reveals when and where blasts are detected, system sensitivity, coverage gaps, and online/offline periods. An adversary engaged in blast fishing can monitor this data to learn when the system is blind or to avoid monitored areas.", 5626)
          ]}),
          new TableRow({ children: [
            bodyCell("Raw audio recordings", 2400),
            bodyCell("MEDIUM", 1000, { bold: true }),
            bodyCell("WAV clips in the public container may capture vessel engine signatures, marine life activity, or other acoustically sensitive data. Available to anyone without restriction.", 5626)
          ]}),
        ]
      }),

      p("", { after: 60 }),

      h2("4.2 Threat Model"),

      richP([
        { text: "The system faces three distinct threat categories:" },
      ]),

      richP([
        { text: "Illegal fishing operators (primary threat). ", bold: true },
        { text: "These actors have direct motivation to monitor, evade, or disable the system. Public detection data tells them where sensors are, when they are active, and what triggers an alert. With write access to the config blob, they could raise detection thresholds to suppress alerts without anyone noticing. The physical location data enables targeted vandalism or theft of hardware." },
      ]),

      richP([
        { text: "Opportunistic attackers. ", bold: true },
        { text: "The publicly exposed Twilio credentials can be used for unauthorized messaging (spam, phishing) at OceanKind's expense. The public blob container could be used as free file hosting if write access is misconfigured. These are not targeted attacks but are likely given the credentials are in source code." },
      ]),

      richP([
        { text: "Data integrity threats. ", bold: true },
        { text: "Without authentication on the config blob, anyone with write access can alter system behavior. Without signed uploads from the Pi, a compromised network path could inject false detections or suppress real ones. The system currently has no mechanism to verify that data in the blob was actually produced by an authorized device." },
      ]),

      h2("4.3 Proposed Architecture: Authenticated API With Credential Isolation"),

      richP([
        { text: "The blob container was set to public access deliberately: without a backend API, the static dashboard had no other way to read the data. " },
        { text: "The lack of a backend is the root cause of the entire security exposure.", bold: true },
        { text: " Building the API layer (Section 3.5) is therefore not just a scalability improvement — it is a security prerequisite. Once an API exists, the container can be made private (a one-click change in the Azure Portal) and all data access flows through authenticated endpoints." },
      ]),

      richP([
        { text: "The solution unifies the scalability API with authentication. Rather than building two separate systems — one for query/pagination and one for auth — the " },
        { text: "API becomes the single gateway for all data access", bold: true },
        { text: ". No client (dashboard, mobile app, third-party integration) ever touches blob storage directly. The API holds all credentials and enforces access control." },
      ]),

      h3("4.3.1 Architecture Overview"),

      richP([
        { text: "The system divides into three trust zones:" },
      ]),

      richP([
        { text: "Edge devices (Pi / ESP32) authenticate to the API ", bold: true },
        { text: "using per-device API keys or mutual TLS certificates. Each device has a unique credential provisioned at deployment time, stored in /etc/oceankind.env (Pi) or NVS (ESP32). The device uses this credential to upload detection events, audio clips, status, and telemetry. The API validates the credential and rejects unauthorized uploads. Device keys can be rotated remotely via the API without physical access." },
      ]),

      richP([
        { text: "The API backend holds all third-party credentials. ", bold: true },
        { text: "Twilio SID/token, Azure Blob Storage connection strings, IoT Hub keys — none of these exist on the edge device or in the dashboard. The Pi sends a detection event to the API; the API decides whether to trigger a WhatsApp alert and calls Twilio server-side. This eliminates the entire class of credential-in-source vulnerabilities: the Pi never sees the Twilio token, the dashboard never sees the blob storage key." },
      ]),

      richP([
        { text: "Dashboard users authenticate via the API. ", bold: true },
        { text: "The dashboard is no longer an anonymous static page reading public blobs. Users log in (email/password, OAuth via Google/Microsoft, or a simple invite-link token system), receive a session token, and all API requests include this token. The API enforces role-based access: viewer (read detections and status), operator (change config, manage devices), and admin (manage users, rotate keys)." },
      ]),

      h3("4.3.2 Data Flow: Before and After"),

      new Table({
        width: { size: TABLE_WIDTH, type: WidthType.DXA },
        columnWidths: [2000, 3513, 3513],
        rows: [
          new TableRow({ children: [headerCell("Flow", 2000), headerCell("Current (No Auth)", 3513), headerCell("Proposed (API-Gated)", 3513)] }),
          new TableRow({ children: [
            bodyCell("Pi uploads detection", 2000),
            bodyCell("Pi writes directly to public blob container using storage account key embedded in code.", 3513),
            bodyCell("Pi POSTs to API with per-device API key. API validates, stores in private blob + index.", 3513)
          ]}),
          new TableRow({ children: [
            bodyCell("WhatsApp alert", 2000),
            bodyCell("Pi calls Twilio API directly using hardcoded SID/token.", 3513),
            bodyCell("API triggers WhatsApp server-side. Pi never sees Twilio credentials.", 3513)
          ]}),
          new TableRow({ children: [
            bodyCell("Dashboard reads data", 2000),
            bodyCell("Browser fetches public blob URLs. No auth. Anyone can read.", 3513),
            bodyCell("Browser calls API with session token. API returns only data the user is authorized to see.", 3513)
          ]}),
          new TableRow({ children: [
            bodyCell("Config change", 2000),
            bodyCell("Operator edits a public blob file. Pi polls and applies blindly.", 3513),
            bodyCell("Operator submits config via API (authenticated). API validates, signs, and stores. Pi fetches signed config from API.", 3513)
          ]}),
          new TableRow({ children: [
            bodyCell("Audio clip playback", 2000),
            bodyCell("Dashboard links directly to public blob URL.", 3513),
            bodyCell("Dashboard requests clip via API. API generates time-limited signed URL or proxies the stream.", 3513)
          ]}),
        ]
      }),

      p("", { after: 60 }),

      h3("4.3.3 API Credential Management"),

      richP([
        { text: "All secrets are stored in " },
        { text: "Azure Key Vault", bold: true },
        { text: " (or environment variables on a self-hosted backend), never in source code, never on edge devices, and never in the dashboard. The API reads secrets from Key Vault at startup and caches them in memory. Key rotation is performed in Key Vault and the API picks up new values on the next restart or via a refresh interval — no code deployment required, no device update needed." },
      ]),

      new Table({
        width: { size: TABLE_WIDTH, type: WidthType.DXA },
        columnWidths: [2800, 2113, 2113, 2000],
        rows: [
          new TableRow({ children: [headerCell("Credential", 2800), headerCell("Current Location", 2113), headerCell("Proposed Location", 2113), headerCell("Rotation", 2000)] }),
          new TableRow({ children: [
            bodyCell("Twilio SID + token", 2800),
            bodyCell("Hardcoded in Python source", 2113),
            bodyCell("Azure Key Vault (API-only)", 2113),
            bodyCell("In Key Vault, no code change", 2000)
          ]}),
          new TableRow({ children: [
            bodyCell("Azure Blob Storage key", 2800),
            bodyCell("In Python source / env", 2113),
            bodyCell("Azure Key Vault (API-only)", 2113),
            bodyCell("In Key Vault, no code change", 2000)
          ]}),
          new TableRow({ children: [
            bodyCell("IoT Hub connection string", 2800),
            bodyCell("/etc/oceankind.env on Pi", 2113),
            bodyCell("Azure Key Vault (API-only)", 2113),
            bodyCell("In Key Vault, no code change", 2000)
          ]}),
          new TableRow({ children: [
            bodyCell("Per-device API key", 2800),
            bodyCell("Does not exist", 2113),
            bodyCell("/etc/oceankind.env (Pi) or NVS (ESP32)", 2113),
            bodyCell("API issues new key, Pi fetches on next heartbeat", 2000)
          ]}),
          new TableRow({ children: [
            bodyCell("Dashboard user sessions", 2800),
            bodyCell("Does not exist", 2113),
            bodyCell("JWT / session token from API", 2113),
            bodyCell("Short-lived (hours), auto-refresh", 2000)
          ]}),
        ]
      }),

      p("", { after: 60 }),

      h3("4.3.4 Dashboard Authentication Options"),

      richP([
        { text: "The dashboard needs a login gate. Three options, in order of implementation simplicity:" },
      ]),

      richP([
        { text: "1. Invite-link tokens (simplest). ", bold: true },
        { text: "An admin generates a unique link (e.g. dashboard.oceankind.org/?token=abc123). The token maps to a user/role in the API database. No password management, no OAuth provider. Suitable for a small, trusted team. Tokens can be revoked individually." },
      ]),

      richP([
        { text: "2. OAuth via Google or Microsoft (recommended for organizations). ", bold: true },
        { text: "Users log in with their existing Google or Microsoft account. The API validates the OAuth token and checks an allowlist of authorized email addresses or domains. No password storage needed. Works well if the team already uses Google Workspace or Microsoft 365." },
      ]),

      richP([
        { text: "3. Email/password with bcrypt (most flexible, most maintenance). ", bold: true },
        { text: "Traditional auth. Requires password hashing, reset flows, and session management. Only justified if the dashboard will have many users who don't share an OAuth provider." },
      ]),

      richP([
        { text: "Regardless of method, the API returns a " },
        { text: "short-lived JWT", bold: true },
        { text: " (e.g., 4 hours) that the dashboard includes in every API request. The JWT encodes the user's role (viewer, operator, admin) so the API can enforce permissions without a database lookup on every request." },
      ]),

      h3("4.3.5 Blob Container Lockdown"),

      richP([
        { text: "The Azure Blob Storage container must be made " },
        { text: "private immediately", bold: true },
        { text: ". This is a configuration change in the Azure Portal (Storage Account → Containers → set access level to Private) and takes effect within seconds. After lockdown:" },
      ]),

      richP([
        { text: "The Pi authenticates uploads using a scoped SAS token or managed identity. The SAS token grants write-only access to specific blob paths (e.g., /devices/{device_id}/clips/) and expires after a configurable period. The Pi receives a fresh SAS token from the API during each heartbeat." },
      ]),

      richP([
        { text: "The dashboard never accesses blobs directly. All reads go through the API, which generates time-limited read-only SAS URLs for audio clip playback. These URLs expire after minutes, not days, and are scoped to the specific clip requested." },
      ]),

      richP([
        { text: "The remote_config.json blob is replaced by an API endpoint. The Pi fetches configuration from GET /devices/{id}/config with its device API key. The response is signed (HMAC or JWT) so the Pi can verify it was produced by the legitimate API server, not injected by a network-level attacker." },
      ]),

      h2("4.4 Immediate Security Actions"),

      richP([
        { text: "These actions should be taken " },
        { text: "before any other development work", bold: true },
        { text: ", as they address active vulnerabilities that exist in the running production system:" },
      ]),

      richP([
        { text: "1. Rotate the Twilio credentials. ", bold: true },
        { text: "Generate a new auth token in the Twilio console. Update /etc/oceankind.env on the Pi. Remove the hardcoded defaults from the Python source. Purge the old token from any .bak files, __pycache__ directories, and git history (use git filter-branch or BFG Repo-Cleaner)." },
      ]),

      richP([
        { text: "2. Make the blob container private. ", bold: true },
        { text: "Azure Portal → Storage Account → Containers → set access level to Private (one-click, takes effect in seconds). The container was set to public intentionally because the static dashboard has no other way to fetch data without a backend. This change will break the dashboard until either: (a) a temporary read-only SAS token is hardcoded into the dashboard, or (b) the API backend (Sections 3.5 and 4.3) is deployed. Option (a) is a quick interim fix; option (b) is the permanent solution. Either is preferable to continued public exposure." },
      ]),

      richP([
        { text: "3. Remove GPS coordinates from source code. ", bold: true },
        { text: "Move lat/lon to /etc/oceankind.env alongside other deployment-specific values. Each unit should have its own coordinates set at provisioning time, not compiled into the codebase." },
      ]),

      richP([
        { text: "4. Audit git history for secrets. ", bold: true },
        { text: "If the repository has ever been pushed to GitHub or any shared remote, the Twilio credentials and any other secrets in historical commits are compromised regardless of current file contents. Run a secret scanner (trufflehog, gitleaks) and purge findings." },
      ]),

      // =====================================================================
      // OPERATIONAL RISKS
      // =====================================================================
      h1("5. Operational Risks Carried Forward"),

      richP([
        { text: "The initial audit identified 13 issues across security, reliability, and maintainability. The following remain the highest priority and are affected by the hardware and software decisions in this report." },
      ]),

      new Table({
        width: { size: TABLE_WIDTH, type: WidthType.DXA },
        columnWidths: [2800, 1200, 5026],
        rows: [
          new TableRow({ children: [headerCell("Issue", 2800), headerCell("Severity", 1200), headerCell("Impact of Proposed Changes", 5026)] }),
          new TableRow({ children: [
            bodyCell("Twilio credentials in source", 2800),
            bodyCell("CRITICAL", 1200, { bold: true, color: "CC0000" }),
            bodyCell("Resolved by credential isolation (Section 4.3.3). Credentials move to Azure Key Vault, accessed only by API backend. Must be rotated immediately (Section 4.4).", 5026)
          ]}),
          new TableRow({ children: [
            bodyCell("Detection gaps (deaf window)", 2800),
            bodyCell("CRITICAL", 1200, { bold: true, color: "CC0000" }),
            bodyCell("Resolved by the async pipeline architecture (Section 3.2). Continuous capture with queue decoupling eliminates dead time.", 5026)
          ]}),
          new TableRow({ children: [
            bodyCell("Public blob container", 2800),
            bodyCell("HIGH", 1200, { bold: true, color: "DD6600" }),
            bodyCell("Resolved by API-gated architecture (Section 4.3). Container made private, all access through authenticated API with time-limited SAS URLs.", 5026)
          ]}),
          new TableRow({ children: [
            bodyCell("Silent-deaf on model failure", 2800),
            bodyCell("HIGH", 1200, { bold: true, color: "DD6600" }),
            bodyCell("Resolved by modularization: classifier.py should fail loudly (heartbeat flag + WhatsApp alert) and fall back to RMS detection.", 5026)
          ]}),
          new TableRow({ children: [
            bodyCell("OTA can strand a remote node", 2800),
            bodyCell("HIGH", 1200, { bold: true, color: "DD6600" }),
            bodyCell("On Pi: health check + auto-rollback needed. On ESP32: native A/B OTA with automatic rollback is built into ESP-IDF.", 5026)
          ]}),
          new TableRow({ children: [
            bodyCell("SD card fragility", 2800),
            bodyCell("HIGH", 1200, { bold: true, color: "DD6600" }),
            bodyCell("On Pi: overlay FS mitigates but adds complexity. On ESP32: eliminated entirely (firmware in flash, no filesystem writes).", 5026)
          ]}),
          new TableRow({ children: [
            bodyCell("Hardcoded audio device", 2800),
            bodyCell("MEDIUM", 1200, { bold: true }),
            bodyCell("Resolved by capture module auto-detecting device by name (already implemented in deprecated audio_capture.py).", 5026)
          ]}),
          new TableRow({ children: [
            bodyCell("Two divergent codebases", 2800),
            bodyCell("HIGH", 1200, { bold: true, color: "DD6600" }),
            bodyCell("Resolved by the proposed modular rewrite. Archive deprecated code, single canonical codebase.", 5026)
          ]}),
        ]
      }),

      p("", { after: 60 }),

      // =====================================================================
      // RECOMMENDATIONS
      // =====================================================================
      h1("6. Recommendations"),

      h2("6.1 Immediate Actions (Before Any Rewrite)"),

      richP([
        { text: "1. Execute the four immediate security actions in Section 4.4. ", bold: true },
        { text: "Rotate Twilio credentials, make the blob container private, remove GPS from source code, and audit git history for secrets. These address active vulnerabilities in the running production system." },
      ]),
      richP([
        { text: "2. Validate the classifier against distant blast recordings. ", bold: true },
        { text: "Before committing to hardware changes, confirm the model can detect events at the expected operational range. If the training set only covers nearby blasts, the model must be retrained regardless." },
      ]),

      h2("6.2 Short-Term: Software Rewrite (Pi 4, Current Hardware)"),

      richP([
        { text: "Restructure the monolith into the async pipeline described in Section 3.2. This can be done on the current Pi 4 hardware and delivers the biggest reliability improvement: eliminating detection gaps. Use the deprecated modular capture code as a foundation. Target: continuous capture with zero deaf time, modular code, proper error isolation. Begin dashboard API planning (Section 3.5) in parallel." },
      ]),

      h2("6.3 Medium-Term: Hardware Migration"),

      richP([
        { text: "The recommended migration path depends on the outcome of the distant-blast validation:" },
      ]),

      richP([
        { text: "If the HifiBerry ADC Pro is required ", bold: true },
        { text: "(distant detection demands 24-bit capture quality): migrate to " },
        { text: "Raspberry Pi Zero 2W", bold: true },
        { text: ". Same software stack, 85% power reduction, same I2S HAT compatibility. SD card fragility remains but is manageable with overlay FS." },
      ]),

      richP([
        { text: "If a simpler ADC suffices ", bold: true },
        { text: "(detection is primarily for nearby/medium-range blasts): migrate to " },
        { text: "ESP32-S3 with external I2S ADC", bold: true },
        { text: ". Eliminates SD card entirely, drops power to ~0.2W, enables native A/B OTA. Requires C port of feature extraction (~1-2 weeks development)." },
      ]),

      richP([
        { text: "Cloud offload is a viable complement to either path ", bold: true },
        { text: "if an edge pre-filter (RMS energy gate) reduces the volume of clips sent over cellular. Full cloud offload without pre-filtering is cost-prohibitive for continuous monitoring." },
      ]),

      h2("6.4 Long-Term: Dashboard API + Multi-Unit Scalability"),

      richP([
        { text: "Deploy the API layer described in Section 3.5. Replace the shared manifest.json with an indexed database (Cosmos DB or SQLite). Migrate the dashboard to API-backed pagination, filtering, and conditional polling. Add multi-device support to the map and detection views. IoT Hub device registration and Stream Analytics (already documented in PROJECT15.md but not implemented) provide the correct foundation for device management at scale." },
      ]),

      // =====================================================================
      // FOOTER
      // =====================================================================
      new Paragraph({
        spacing: { before: 600 },
        children: [new TextRun({ text: "________________________________________________________________________________________________________", size: 22, font: "Arial", color: "999999" })]
      }),
      new Paragraph({
        spacing: { before: 200 },
        children: [new TextRun({ text: "Report prepared by Futurity Systems", italics: true, size: 20, font: "Arial", color: "666666" })]
      }),
      new Paragraph({
        children: [new TextRun({ text: "OceanKind / Mar Futura  |  System Architecture Review  |  July 2026", italics: true, size: 20, font: "Arial", color: "666666" })]
      }),
    ]
  }]
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("/sessions/funny-compassionate-sagan/mnt/marFutura/OceanKind_Improvement_Report.docx", buffer);
  console.log("Document written successfully.");
});
