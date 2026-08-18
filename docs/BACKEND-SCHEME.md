# Backend scheme

**Status:** specification only. Not built, not contracted, platform undecided.
**Last updated:** 2026-08-02

This document tracks **the scheme, not the software**: what the endpoints are, what the data model looks like, what authenticates, and where credentials live. The platform choice is D-003 and is deliberately open. Everything here should survive that decision.

If you are about to write "FastAPI" or "Postgres" into this document, stop and read D-003 first.

---

## Why a backend exists at all

Not for scale. One unit produces a few hundred events a year and a JSON file loads in under a second.

It exists because **there is no place to put a credential.** The dashboard is a static file in a browser, so anything it holds is public. The Pi is a device in a field, so anything it holds is extractable. Without a third party that is neither, the storage container has to be public, and once it is public the detection history, the audio and the exact coordinates of unattended hardware are public too.

That is the whole argument. Every security finding traces back to it. Scale and query capability are real benefits but they are consequences, not reasons.

---

## What it replaces

| Today | With a backend |
|---|---|
| Pi writes blobs with the storage account key | Pi posts to the API with a per-device key |
| Pi calls Twilio with a token in its source | API calls Twilio. The device never sees the token |
| Dashboard reads public blobs | Dashboard calls the API with a session token |
| Config edited as a public blob, applied blindly | Config submitted through the API, signed, verified by the device |
| Clip linked as a public URL | Clip fetched through a short-lived signed URL |

Three trust zones: devices authenticate with per-device keys, users authenticate with sessions, and the backend is the only thing holding third-party credentials.

---

## Endpoint surface

Deliberately small. Five read endpoints, two write, one device-facing.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/devices` | All units with latest status. One call powers the map |
| `GET` | `/devices/{id}/detections` | Paginated. `?since= &until= &type= &confidence_min= &limit= &offset=` |
| `GET` | `/devices/{id}/status` | Current state. Conditional via ETag |
| `GET` | `/devices/{id}/power` | Windowed. `?range=24h\|7d\|30d` |
| `GET` | `/devices/{id}/clips/{event_id}` | Short-lived signed URL or proxied stream |
| `POST` | `/devices/{id}/events` | Device posts a detection |
| `POST` | `/devices/{id}/status` | Device posts telemetry |
| `GET` | `/devices/{id}/config` | Device fetches signed configuration |

Notably absent: anything that lets a client write configuration directly, and any endpoint that returns a credential.

**Server-sent events** are worth considering for pushing new detections to open dashboards, which removes the 30-second polling latency. Optional, not phase-one-of-the-backend.

---

## Data model

Entities, not tables. The storage technology is D-004, and the current answer is likely **no datastore at all**: per-device, per-date blob paths plus blob index tags cover the querying this system actually needs. See D-004 for the mechanisms and their limits. Model the entities below as blob layout and tags first, and only reach for a database if a requirement appears that paths and tags cannot serve.

**Device.** Identifier, display name, coordinates, hardware description, current firmware version, per-device API key hash, provisioning date. Coordinates move here from the device source, which closes F-08 permanently rather than by moving them to an environment file.

**Detection event.** Device, capture timestamp, upload timestamp (these differ and the current manifest conflates them), RMS, peak dB, classifier label, probability, prediction, what decided it, whether it was suppressed by cooldown, clip reference. Append only. Never updated, never deleted.

**Status sample.** Device, timestamp, and the telemetry block currently in `status.json`. Time series. Retention policy is a decision nobody has made.

**User.** Identifier, authentication handle, role, which devices they may see.

**Device-user grant.** Which users see which devices. Deliberately a separate entity, because "which users see which devices must be defined in the future" is an unanswered line in the original budget and modelling it as a join now costs nothing.

**Config revision.** Device, version, parameter set, who changed it, when, and a signature. Configuration becomes auditable, which it currently is not at all.

Two properties that matter more than the schema. Events are append-only, which removes the read-modify-write race (F-14) by construction rather than by locking. And capture time is distinct from upload time, which the current manifest cannot express.

---

## Authentication

**Devices.** One key per device, provisioned at deployment, stored in `/etc/oceankind.env`. Rotatable through the API without physical access, which matters for a node nobody can reach. Mutual TLS is the stronger option and is probably not worth the operational weight at this scale.

**Users.** Invite-link tokens are sufficient for a small team, and `SYSTEM_REVIEW.md` §5.4 is right that OAuth and role hierarchies are over-specified here. If the chosen platform provides managed authentication as part of the package, use it rather than hand-rolling. Two roles: viewer reads, operator changes configuration. An admin tier can wait until there is someone to be an admin over.

**Credentials.** Twilio, storage keys, IoT Hub connection strings all live in a managed secret store the backend reads at startup. None on the device, none in the browser, none in source. Rotation happens in one place with no deployment and no device update.

---

## Migration

The backend cannot arrive by flag day. The dashboard works today and a half-migrated dashboard is worse than either end state.

A workable order: stand the API up reading the existing blobs, so it returns correct data before anything writes to it. Move the dashboard's reads across one endpoint at a time, keeping the blob path as fallback. Then move the device's writes. Then make the container private, which is the point of no return and should be the last step, not the first.

The `?play=` deep links already sitting in people's WhatsApp history have to keep resolving through all of it.

---

## What the backend is not

It is not a query engine. Azure Blob Storage does prefix listing, index tag filtering, single-blob SQL and conditional requests server-side, which covers time slicing, device slicing and attribute filtering without any index of our own. Building a database to serve a few hundred events a year would be inventing work.

It is not a scaling layer. One device today, a handful within six months.

It is the place a credential can live. That is the whole justification, and keeping it in view is what stops this from growing.

---

## Open questions

Beyond the platform (D-003) and the datastore (D-004):

Whether the device keeps writing blobs and the backend indexes them, or the device posts to the backend and the backend writes storage. The first is a smaller change to the device and keeps working when the API is down. The second is cleaner and gives the backend real control. On a cellular node with intermittent connectivity, the first is probably right and the audits assumed the second without arguing for it.

What retention applies to status samples. A telemetry row a minute forever is a cost nobody has budgeted.

Whether the IoT Hub limb gets revived or removed. It currently sends messages nobody reads and costs a network call inside the deaf window. Reviving it is a real option if the platform decision lands on Azure-native services.

Whether the backend also owns alerting. Moving Twilio server-side removes the credential from the device, but it also means a detection cannot notify anyone when the cellular link is up but the API is down. The device's local retry buffer exists precisely because that happens.
