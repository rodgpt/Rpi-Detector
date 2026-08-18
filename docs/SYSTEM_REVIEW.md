**MAR FUTURA**

1\. Context

The OceanKind system is deployed and working. It detects events, uploads data, and delivers WhatsApp alerts. This review is not about a broken system --- it is about an **honest assessment of what should be improdev**, what the real risks are, and where the cost of fixing something outweighs the benefit.

TL;DR

**Three things need fixing today (\~1.5 days):** Twilio API credentials are hardcoded in source code, the entire data store (detection history, audio recordings, GPS coordinates) is publicly accessible with no authentication, and the sensor\'s physical location is hardcoded in the codebase. All verified during this review.

**Two things should be fixed soon (\~10-15 days):** the system stops listening while it processes audio (wrong architecture for acoustic monitoring, though the actual miss rate is low), and the codebase has accumulated technical debt (two divergent codebases, risky OTA process). These are refactors on the current hardware, not a rewrite.

**Everything else can wait:** the Raspberry Pi is overkill but it works, the dashboard is inefficient but it loads, and enterprise-grade auth is unnecessary for a small team. A backend API is the bridge between the quick fixes and the long-term architecture --- it solves security permanently and enables future scalability --- but it is not urgent for a single-unit deployment. So how soon do we expect to deploy more units, and how many more?

2\. Hardware

  2.1 Compute Platform

    **\[CAN WAIT\]** The Raspberry Pi 4 is over-specified. The ML model is 53 parameters (3 KB) --- it could theoretically run on an ESP32. Power draw is \~3.5W where a smaller board could use \~0.4W.

      **Pushback:** The Pi 4 works, it is deployed, and the solar panel handles the load. Saving 2.5W and \$40 per unit only matters if building 10+ units. The ESP32 path requires rewriting the entire feature extraction pipeline in C --- realistically 2-3 months, not 1-2 weeks. The Pi Zero 2W is a lighter migration but the SD card fragility remains. None of this is worth doing for a single deployment.

      **Effort:** Pi Zero 2W: 4-7 days. ESP32: 2-3 months. Recommend deferring until multi-unit deployment is confirmed.

      **PS:** The codebase references a HifiBerry DAC+ ADC Pro; a separate system diagram shows a Raspberry Pi Codec Zero. Which ADC is actually deployed needs to be confirmed before any hardware change.

  2.2 SD Card Fragility

    **\[SHOULD FIX\]** MicroSD cards degrade with repeated writes and are vulnerable to corruption on power loss. The team has mitigated this with an overlay filesystem, but this adds complexity and introduces its own failure modes during OTA updates.

      **Pushback:** The overlay FS mitigation is already in place and working. This is a known risk, not an active failure. It becomes critical if the unit loses power during an OTA update (the two-reboot process can strand the node). On ESP32 this problem disappears entirely --- but that is a much larger migration.

      **Effort:** Adding a health-check rollback to the OTA script: 2-3 days.

3\. Software Architecture

  3.1 Detection Gaps (Deaf Window)

    **\[SHOULD FIX\]** The system records a 5-second clip, then stops listening while it classifies and uploads. During processing (3-15 seconds), the hydrophone is deaf.

      **Pushback:** The probability of missing a specific blast is low. Blasts are infrequent events and the deaf window is short. The system is catching detections today --- three in the current session alone. But the architecture is fundamentally wrong for acoustic monitoring: it should never stop listening. The fix is restructuring the code into an async pipeline on the current Pi 4, not a rewrite on new hardware.

      **Effort:** 5-10 days. Refactor the monolith into threaded modules. The deprecated codebase already has a working capture module to build from.

  3.2 Single-File Monolith (1,309 Lines)

    **\[SHOULD FIX\]** The entire system --- audio capture, ML classification, WhatsApp alerts, blob uploads, solar telemetry, modem polling, battery monitoring --- is one Python file. Changes to any part risk breaking everything else.

      **Pushback:** A monolith is not inherently bad. It is simpler to deploy, simpler to debug, and there is only one unit. The modularization becomes necessary when implementing the async pipeline (Section 3.1), at which point it happens naturally as part of the refactor.

      **Effort:** Included in the async pipeline work (Section 3.1).

  3.3 Two Divergent Codebases

  **\[SHOULD FIX\]** The repository contains a production monolith and a deprecated modular version. The setup script installs the wrong one. The requirements.txt lists dependencies for the deprecated code. A new developer would not know which files run.

      **Pushback:** This is a maintainability problem, not an operational one --- the deployed Pi runs the correct code. But it is cheap to fix and saves real confusion.

      **Effort:** 2-3 days. Archive deprecated code, fix setup.sh and requirements.txt, add a README.

  3.4 OTA Update Risk

  **\[SHOULD FIX\]** The over-the-air update process requires two reboots and temporarily disables the overlay filesystem. If the process fails mid-way (network drop, power loss), the unit can be left in an unrecoverable state with no rollback mechanism.

      **Pushback:** Updates are infrequent and presumably done during stable conditions. The risk is real but low-probability. A health-check and automatic rollback would make this safe.

      **Effort:** 2-3 days. Add a watchdog that reverts if the service fails to start after update.

4\. Dashboard and Scalability

  4.1 Dashboard Fetches Everything, Every Time

  **\[CAN WAIT\]** The dashboard downloads the full detection history (manifest.json), full status, and full power history every 30 seconds. No pagination, no filtering, no conditional requests.

      **Pushback:** It works. One unit, a small team, a JSON file that loads in a second. The scalability problems are real but they are future problems. If the backend API (Section 5.2) gets built, these improvements come naturally as part of migrating to API-backed data fetching.

      **Effort:** 5-10 days on top of the API. Only worthwhile after the backend exists.

  4.2 No Multi-Device Support

  **\[CAN WAIT\]** The manifest.json read-modify-write pattern will fail with multiple devices (race condition: simultaneous writes lose data). The dashboard has no device selector.

      **Pushback:** There is one device. This is a real design limitation but it causes zero problems today. Address when the second unit is being planned, not before.

      **Effort:** Included in API + dashboard rebuild. No standalone fix makes sense.

5\. Security and Authentication

  5.1 Twilio Credentials in Source Code

  **\[FIX NOW\]** The Twilio account SID and auth token are hardcoded as default values in the Python source. Anyone with access to the code can send WhatsApp messages on the OceanKind account.

      No pushback. This is indefensible. Rotate today.

      **Effort:** Half a day.

  5.2 All Data Is Publicly Accessible

  **\[FIX NOW\]** The Azure Blob Storage container is set to public anonymous read. This was done intentionally: the static dashboard has no backend, so it reads blobs directly --- which only works if the container is public. The consequence is that anyone with the storage account URL can access the full detection history, all audio recordings, real-time telemetry, and the exact GPS coordinates of the sensor hardware. We verified this during the review --- all data is downloadable right now with no authentication.

      **Pushback:** The threat model matters here. The \"sophisticated adversary monitoring detection patterns\" scenario is probably extreme --- blast fishermen in rural coastal Chile are unlikely to be running surveillance on an Azure endpoint. The GPS coordinates and physical location exposure is the more concrete risk (theft, vandalism of remote equipment). The data exposure is real and verified, but the urgency depends on who might actually look.

      The fix is a one-click change in the Azure Portal (set container to Private). This breaks the dashboard until a read-only SAS token is added to its fetch calls --- a 1-day interim fix. The permanent solution is a backend API that gates all access through authenticated endpoints. **The lack of a backend is the root cause of the entire security exposure.**

      **Effort:** Interim (SAS token): 1 day. Permanent (API backend): 10-15 days.

  5.3 GPS Coordinates in Source Code

  **\[FIX NOW\]** Sensor latitude and longitude are hardcoded in the Python source and uploaded in every status update. The physical location of remote equipment is publicly discoverable.

      No pushback. Move to environment file. Trivial fix.

      **Effort:** 1 hour.

  5.4 Dashboard Has No Login

  **\[SHOULD FIX\]** The dashboard is a static HTML page with zero authentication. Anyone with the URL has full read access to all system data.

      **Pushback:** If the blob container is made private and the dashboard uses a SAS token, the dashboard URL alone no longer gives access to raw data --- the SAS token is embedded in the JavaScript. This is security through obscurity, not real auth, but for a small team it is a pragmatic interim step. Real dashboard authentication comes with the API backend.

      **Effort:** Included in API build. Simple invite-link tokens are sufficient --- OAuth and RBAC are overkill for a small team.

  5.5 Unsigned Remote Config

  **\[SHOULD FIX\]** The Pi polls a remote\_config.json blob and applies its values (detection thresholds, recording parameters). If someone gains write access to the container, they can silently alter system behavior.

      **Pushback:** Write access to the Azure storage account requires the storage key or a write-scoped SAS token. If an attacker has that, the unsigned config is the least of the problems. This becomes relevant when the API handles config delivery --- at that point, signing is cheap to add.

      **Effort:** Included in API build.

6\. Summary

  --------- ------------------------------------------ ----------------- -----------------------------------------------------------------------
  **§**     **Item**                                   **Effort**        **Honest Take**
  2.1       Compute platform (Pi 4 overkill)           4-7d / 2-3mo      Works fine. Only revisit for multi-unit rollout.
  2.2       SD card fragility                          2-3 days          Mitigated by overlay FS. Add OTA rollback.
  3.1       Detection gaps (deaf window)               5-10 days         Low miss rate today, but wrong architecture. Fix on current hardware.
  3.2       Monolith (1,309 lines)                     (incl. 3.1)       Modularize as part of async refactor.
  3.3       Two divergent codebases                    2-3 days          Cheap housekeeping. Saves confusion.
  3.4       OTA update risk                            2-3 days          Low probability but unrecoverable. Add rollback.
  4.1       Dashboard fetches everything               5-10 days         Works today. Rebuild after API exists.
  4.2       No multi-device support                    (incl. 4.1)       One device. Address when second unit is planned.
  **5.1**   **Twilio credentials in source**           **Half a day**    **No debate. Rotate today.**
  **5.2**   **Public blob container + GPS exposure**   **1d / 10-15d**   **Interim SAS fix: 1 day. Permanent API: 10-15 days.**
  **5.3**   **GPS in source code**                     **1 hour**        **Trivial. Move to env file.**
  5.4       Dashboard has no login                     (incl. API)       SAS token is pragmatic interim. Real auth comes with API.
  5.5       Unsigned remote config                     (incl. API)       Requires storage key to exploit. Sign when API handles config.
  --------- ------------------------------------------ ----------------- -----------------------------------------------------------------------