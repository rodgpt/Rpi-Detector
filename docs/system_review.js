const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
        AlignmentType, HeadingLevel, BorderStyle, WidthType, ShadingType, PageBreak } = require('docx');
const fs = require('fs');

const styles = {
  default: { document: { run: { font: "Arial", size: 22 } } },
  paragraphStyles: [
    { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { size: 32, bold: true, font: "Arial" },
      paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 } },
    { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { size: 26, bold: true, font: "Arial" },
      paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 1 } },
  ]
};

const pageProps = {
  page: {
    size: { width: 11906, height: 16838 },
    margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
  }
};

const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMar = { top: 80, bottom: 80, left: 120, right: 120 };
const W = 9026;
const INDENT = 400; // DXA indent for pushback/effort blocks

function hCell(t, w) {
  return new TableCell({ borders, width: { size: w, type: WidthType.DXA },
    shading: { fill: "F3F3F3", type: ShadingType.CLEAR }, margins: cellMar,
    children: [new Paragraph({ children: [new TextRun({ text: t, bold: true, size: 20, font: "Arial" })] })] });
}
function bCell(t, w, opts = {}) {
  return new TableCell({ borders, width: { size: w, type: WidthType.DXA }, margins: cellMar,
    children: [new Paragraph({ children: [new TextRun({ text: t, size: 20, font: "Arial", bold: opts.bold||false, color: opts.color||"000000" })] })] });
}
function h1(t) { return new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text: t, font: "Arial" })] }); }
function h2(t) { return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text: t, font: "Arial" })] }); }

function rp(runs, opts = {}) {
  return new Paragraph({
    spacing: { after: opts.after || 120, before: opts.before || 0, line: 276 },
    indent: opts.indent ? { left: opts.indent } : undefined,
    children: runs.map(r => new TextRun({ size: 22, font: "Arial", ...r }))
  });
}
function p(t, opts = {}) {
  return new Paragraph({
    spacing: { after: opts.after || 120, line: 276 },
    indent: opts.indent ? { left: opts.indent } : undefined,
    children: [new TextRun({ text: t, size: opts.size || 22, font: "Arial", color: opts.color || "000000", italics: opts.italics || false, bold: opts.bold || false })]
  });
}

function tag(priority) {
  const colors = { "FIX NOW": "CC0000", "SHOULD FIX": "DD6600", "CAN WAIT": "888888" };
  return { text: `[${priority}]  `, bold: true, color: colors[priority] || "000000" };
}

const doc = new Document({
  styles,
  sections: [{
    properties: pageProps,
    children: [

      // === TITLE ===
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 },
        children: [new TextRun({ text: "MAR FUTURA", bold: true, size: 36, font: "Arial" })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
        children: [new TextRun({ text: "System Review", size: 26, font: "Arial" })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
        children: [new TextRun({ text: "Hardware  |  Software  |  Scalability  |  Security  |  July 2026", size: 22, font: "Arial", color: "666666" })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 300 },
        children: [new TextRun({ text: "________________________________________________________________________________________________________", size: 22, font: "Arial", color: "999999" })] }),

      // === 1. CONTEXT ===
      h1("1. Context"),

      rp([
        { text: "The OceanKind system is deployed and working. It detects events, uploads data, and delivers WhatsApp alerts. This review is not about a broken system — it is about an " },
        { text: "honest assessment of what should be improved", bold: true },
        { text: ", what the real risks are, and where the cost of fixing something outweighs the benefit." },
      ]),

      rp([
        { text: "Each finding is tagged " },
        { text: "FIX NOW", bold: true, color: "CC0000" },
        { text: " (active vulnerability), " },
        { text: "SHOULD FIX", bold: true, color: "DD6600" },
        { text: " (real issue, system works without it), or " },
        { text: "CAN WAIT", bold: true, color: "888888" },
        { text: " (only matters at scale). Each includes an effort estimate and an honest pushback on whether it is truly worth doing right now." },
      ]),

      // === TL;DR ===
      h2("TL;DR"),

      rp([
        { text: "Three things need fixing today (~1.5 days): ", bold: true },
        { text: "Twilio API credentials are hardcoded in source code, the entire data store (detection history, audio recordings, GPS coordinates) is publicly accessible with no authentication, and the sensor's physical location is hardcoded in the codebase. All verified during this review." },
      ]),

      rp([
        { text: "Two things should be fixed soon (~2-3 weeks): ", bold: true },
        { text: "the system stops listening while it processes audio (wrong architecture for acoustic monitoring, though the actual miss rate is low), and the codebase has accumulated technical debt (two divergent codebases, risky OTA process). These are refactors on the current hardware, not a rewrite." },
      ]),

      rp([
        { text: "Everything else can wait: ", bold: true },
        { text: "the Raspberry Pi is overkill but it works, the dashboard is inefficient but it loads, and enterprise-grade auth is unnecessary for a small team. A backend API is the bridge between the quick fixes and the long-term architecture — it solves security permanently and enables future scalability — but it is not urgent for a single-unit deployment." },
      ]),

      // === 2. HARDWARE ===
      h1("2. Hardware"),

      h2("2.1 Compute Platform"),
      rp([
        tag("CAN WAIT"),
        { text: "The Raspberry Pi 4 is over-specified. The ML model is 53 parameters (3 KB) — it could theoretically run on an ESP32. Power draw is ~3.5W where a smaller board could use ~0.4W." },
      ]),
      rp([
        { text: "Pushback: ", bold: true },
        { text: "The Pi 4 works, it is deployed, and the solar panel handles the load. Saving 2.5W and $40 per unit only matters if building 10+ units. The ESP32 path requires rewriting the entire feature extraction pipeline in C — realistically 2-3 months, not 1-2 weeks. The Pi Zero 2W is a lighter migration but the SD card fragility remains. None of this is worth doing for a single deployment." },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "Pi Zero 2W: 4-7 days. ESP32: 2-3 months. Recommend deferring until multi-unit deployment is confirmed." },
      ], { indent: INDENT }),
      rp([
        { text: "PS: ", bold: true },
        { text: "The codebase references a HifiBerry DAC+ ADC Pro; a separate system diagram shows a Raspberry Pi Codec Zero. Which ADC is actually deployed needs to be confirmed before any hardware change." },
      ], { indent: INDENT, after: 160 }),

      h2("2.2 SD Card Fragility"),
      rp([
        tag("SHOULD FIX"),
        { text: "MicroSD cards degrade with repeated writes and are vulnerable to corruption on power loss. The team has mitigated this with an overlay filesystem, but this adds complexity and introduces its own failure modes during OTA updates." },
      ]),
      rp([
        { text: "Pushback: ", bold: true },
        { text: "The overlay FS mitigation is already in place and working. This is a known risk, not an active failure. It becomes critical if the unit loses power during an OTA update (the two-reboot process can strand the node). On ESP32 this problem disappears entirely — but that is a much larger migration." },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "Adding a health-check rollback to the OTA script: 2-3 days." },
      ], { indent: INDENT }),

      // === 3. SOFTWARE ===
      new Paragraph({ children: [new PageBreak()] }),
      h1("3. Software Architecture"),

      h2("3.1 Detection Gaps (Deaf Window)"),
      rp([
        tag("SHOULD FIX"),
        { text: "The system records a 5-second clip, then stops listening while it classifies and uploads. During processing (3-15 seconds), the hydrophone is deaf." },
      ]),
      rp([
        { text: "Pushback: ", bold: true },
        { text: "The probability of missing a specific blast is low. Blasts are infrequent events and the deaf window is short. The system is catching detections today — three in the current session alone. But the architecture is fundamentally wrong for acoustic monitoring: it should never stop listening. The fix is restructuring the code into an async pipeline on the current Pi 4, not a rewrite on new hardware." },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "5-10 days. Refactor the monolith into threaded modules. The deprecated codebase already has a working capture module to build from." },
      ], { indent: INDENT }),

      h2("3.2 Single-File Monolith (1,309 Lines)"),
      rp([
        tag("SHOULD FIX"),
        { text: "The entire system — audio capture, ML classification, WhatsApp alerts, blob uploads, solar telemetry, modem polling, battery monitoring — is one Python file. Changes to any part risk breaking everything else." },
      ]),
      rp([
        { text: "Pushback: ", bold: true },
        { text: "A monolith is not inherently bad. It is simpler to deploy, simpler to debug, and there is only one unit. The modularization becomes necessary when implementing the async pipeline (Section 3.1), at which point it happens naturally as part of the refactor." },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "Included in the async pipeline work (Section 3.1)." },
      ], { indent: INDENT }),

      h2("3.3 Two Divergent Codebases"),
      rp([
        tag("SHOULD FIX"),
        { text: "The repository contains a production monolith and a deprecated modular version. The setup script installs the wrong one. The requirements.txt lists dependencies for the deprecated code. A new developer would not know which files run." },
      ]),
      rp([
        { text: "Pushback: ", bold: true },
        { text: "This is a maintainability problem, not an operational one — the deployed Pi runs the correct code. But it is cheap to fix and saves real confusion." },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "2-3 days. Archive deprecated code, fix setup.sh and requirements.txt, add a README." },
      ], { indent: INDENT }),

      h2("3.4 OTA Update Risk"),
      rp([
        tag("SHOULD FIX"),
        { text: "The over-the-air update process requires two reboots and temporarily disables the overlay filesystem. If the process fails mid-way (network drop, power loss), the unit can be left in an unrecoverable state with no rollback mechanism." },
      ]),
      rp([
        { text: "Pushback: ", bold: true },
        { text: "Updates are infrequent and presumably done during stable conditions. The risk is real but low-probability. A health-check and automatic rollback would make this safe." },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "2-3 days. Add a watchdog that reverts if the service fails to start after update." },
      ], { indent: INDENT }),

      // === 4. SCALABILITY ===
      new Paragraph({ children: [new PageBreak()] }),
      h1("4. Dashboard and Scalability"),

      h2("4.1 Dashboard Fetches Everything, Every Time"),
      rp([
        tag("CAN WAIT"),
        { text: "The dashboard downloads the full detection history (manifest.json), full status, and full power history every 30 seconds. No pagination, no filtering, no conditional requests." },
      ]),
      rp([
        { text: "Pushback: ", bold: true },
        { text: "It works. One unit, a small team, a JSON file that loads in a second. The scalability problems are real but they are future problems. If the backend API (Section 5.2) gets built, these improvements come naturally as part of migrating to API-backed data fetching." },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "5-10 days on top of the API. Only worthwhile after the backend exists." },
      ], { indent: INDENT }),

      h2("4.2 No Multi-Device Support"),
      rp([
        tag("CAN WAIT"),
        { text: "The manifest.json read-modify-write pattern will fail with multiple devices (race condition: simultaneous writes lose data). The dashboard has no device selector." },
      ]),
      rp([
        { text: "Pushback: ", bold: true },
        { text: "There is one device. This is a real design limitation but it causes zero problems today. Address when the second unit is being planned, not before." },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "Included in API + dashboard rebuild. No standalone fix makes sense." },
      ], { indent: INDENT }),

      // === 5. SECURITY ===
      h1("5. Security and Authentication"),

      h2("5.1 Twilio Credentials in Source Code"),
      rp([
        tag("FIX NOW"),
        { text: "The Twilio account SID and auth token are hardcoded as default values in the Python source. Anyone with access to the code can send WhatsApp messages on the OceanKind account." },
      ]),
      rp([
        { text: "No pushback. This is indefensible. Rotate today." },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "Half a day." },
      ], { indent: INDENT }),

      h2("5.2 All Data Is Publicly Accessible"),
      rp([
        tag("FIX NOW"),
        { text: "The Azure Blob Storage container is set to public anonymous read. This was done intentionally: the static dashboard has no backend, so it reads blobs directly — which only works if the container is public. The consequence is that anyone with the storage account URL can access the full detection history, all audio recordings, real-time telemetry, and the exact GPS coordinates of the sensor hardware. We verified this during the review — all data is downloadable right now with no authentication." },
      ]),
      rp([
        { text: "Pushback: ", bold: true },
        { text: "The threat model matters here. The \"sophisticated adversary monitoring detection patterns\" scenario is probably extreme — blast fishermen in rural coastal Chile are unlikely to be running surveillance on an Azure endpoint. The GPS coordinates and physical location exposure is the more concrete risk (theft, vandalism of remote equipment). The data exposure is real and verified, but the urgency depends on who might actually look." },
      ], { indent: INDENT }),
      rp([
        { text: "The fix is a one-click change in the Azure Portal (set container to Private). This breaks the dashboard until a read-only SAS token is added to its fetch calls — a 1-day interim fix. The permanent solution is a backend API that gates all access through authenticated endpoints. " },
        { text: "The lack of a backend is the root cause of the entire security exposure.", bold: true },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "Interim (SAS token): 1 day. Permanent (API backend): 10-15 days." },
      ], { indent: INDENT }),

      h2("5.3 GPS Coordinates in Source Code"),
      rp([
        tag("FIX NOW"),
        { text: "Sensor latitude and longitude are hardcoded in the Python source and uploaded in every status update. The physical location of remote equipment is publicly discoverable." },
      ]),
      rp([
        { text: "No pushback. Move to environment file. Trivial fix." },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "1 hour." },
      ], { indent: INDENT }),

      h2("5.4 Dashboard Has No Login"),
      rp([
        tag("SHOULD FIX"),
        { text: "The dashboard is a static HTML page with zero authentication. Anyone with the URL has full read access to all system data." },
      ]),
      rp([
        { text: "Pushback: ", bold: true },
        { text: "If the blob container is made private and the dashboard uses a SAS token, the dashboard URL alone no longer gives access to raw data — the SAS token is embedded in the JavaScript. This is security through obscurity, not real auth, but for a small team it is a pragmatic interim step. Real dashboard authentication comes with the API backend." },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "Included in API build. Simple invite-link tokens are sufficient — OAuth and RBAC are overkill for a small team." },
      ], { indent: INDENT }),

      h2("5.5 Unsigned Remote Config"),
      rp([
        tag("SHOULD FIX"),
        { text: "The Pi polls a remote_config.json blob and applies its values (detection thresholds, recording parameters). If someone gains write access to the container, they can silently alter system behavior." },
      ]),
      rp([
        { text: "Pushback: ", bold: true },
        { text: "Write access to the Azure storage account requires the storage key or a write-scoped SAS token. If an attacker has that, the unsigned config is the least of the problems. This becomes relevant when the API handles config delivery — at that point, signing is cheap to add." },
      ], { indent: INDENT }),
      rp([
        { text: "Effort: ", bold: true },
        { text: "Included in API build." },
      ], { indent: INDENT }),

      // === SUMMARY TABLE ===
      new Paragraph({ children: [new PageBreak()] }),
      h1("6. Summary"),

      new Table({
        width: { size: W, type: WidthType.DXA },
        columnWidths: [700, 2600, 1400, 4326],
        rows: [
          new TableRow({ children: [hCell("§", 700), hCell("Item", 2600), hCell("Effort", 1400), hCell("Honest Take", 4326)] }),

          // Hardware
          new TableRow({ children: [
            bCell("2.1", 700), bCell("Compute platform (Pi 4 overkill)", 2600),
            bCell("4-7d / 2-3mo", 1400), bCell("Works fine. Only revisit for multi-unit rollout.", 4326)
          ]}),
          new TableRow({ children: [
            bCell("2.2", 700), bCell("SD card fragility", 2600),
            bCell("2-3 days", 1400), bCell("Mitigated by overlay FS. Add OTA rollback.", 4326)
          ]}),

          // Software
          new TableRow({ children: [
            bCell("3.1", 700), bCell("Detection gaps (deaf window)", 2600),
            bCell("5-10 days", 1400), bCell("Low miss rate today, but wrong architecture. Fix on current hardware.", 4326)
          ]}),
          new TableRow({ children: [
            bCell("3.2", 700), bCell("Monolith (1,309 lines)", 2600),
            bCell("(incl. 3.1)", 1400), bCell("Modularize as part of async refactor.", 4326)
          ]}),
          new TableRow({ children: [
            bCell("3.3", 700), bCell("Two divergent codebases", 2600),
            bCell("2-3 days", 1400), bCell("Cheap housekeeping. Saves confusion.", 4326)
          ]}),
          new TableRow({ children: [
            bCell("3.4", 700), bCell("OTA update risk", 2600),
            bCell("2-3 days", 1400), bCell("Low probability but unrecoverable. Add rollback.", 4326)
          ]}),

          // Scalability
          new TableRow({ children: [
            bCell("4.1", 700), bCell("Dashboard fetches everything", 2600),
            bCell("5-10 days", 1400), bCell("Works today. Rebuild after API exists.", 4326)
          ]}),
          new TableRow({ children: [
            bCell("4.2", 700), bCell("No multi-device support", 2600),
            bCell("(incl. 4.1)", 1400), bCell("One device. Address when second unit is planned.", 4326)
          ]}),

          // Security
          new TableRow({ children: [
            bCell("5.1", 700, { bold: true }), bCell("Twilio credentials in source", 2600, { bold: true }),
            bCell("Half a day", 1400, { bold: true }), bCell("No debate. Rotate today.", 4326, { bold: true })
          ]}),
          new TableRow({ children: [
            bCell("5.2", 700, { bold: true }), bCell("Public blob container + GPS exposure", 2600, { bold: true }),
            bCell("1d / 10-15d", 1400, { bold: true }), bCell("Interim SAS fix: 1 day. Permanent API: 10-15 days.", 4326, { bold: true })
          ]}),
          new TableRow({ children: [
            bCell("5.3", 700, { bold: true }), bCell("GPS in source code", 2600, { bold: true }),
            bCell("1 hour", 1400, { bold: true }), bCell("Trivial. Move to env file.", 4326, { bold: true })
          ]}),
          new TableRow({ children: [
            bCell("5.4", 700), bCell("Dashboard has no login", 2600),
            bCell("(incl. API)", 1400), bCell("SAS token is pragmatic interim. Real auth comes with API.", 4326)
          ]}),
          new TableRow({ children: [
            bCell("5.5", 700), bCell("Unsigned remote config", 2600),
            bCell("(incl. API)", 1400), bCell("Requires storage key to exploit. Sign when API handles config.", 4326)
          ]}),
        ]
      }),

      p("", { after: 80 }),

      rp([
        { text: "Bold rows", bold: true },
        { text: " are immediate actions (~1.5 days total). The async pipeline refactor is the most impactful single investment (~5-10 days). The API backend (~10-15 days) is the permanent fix for security and the foundation for everything else. Hardware changes and dashboard rebuilds should wait until multi-unit deployment is on the table." },
      ]),

      // === FOOTER ===
      new Paragraph({ spacing: { before: 600 },
        children: [new TextRun({ text: "________________________________________________________________________________________________________", size: 22, font: "Arial", color: "999999" })] }),
      new Paragraph({ spacing: { before: 200 },
        children: [new TextRun({ text: "Report prepared by Futurity Systems", italics: true, size: 20, font: "Arial", color: "666666" })] }),
      new Paragraph({
        children: [new TextRun({ text: "Mar Futura  |  System Review  |  July 2026", italics: true, size: 20, font: "Arial", color: "666666" })] }),
    ]
  }]
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("/sessions/funny-compassionate-sagan/mnt/marFutura/MarFutura_System_Review.docx", buffer);
  console.log("Done.");
});
