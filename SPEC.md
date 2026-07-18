# MATSO (Military Analysis & Tactical Simulation Orchestrator)
**Comprehensive System Specification V4.0**

## 1. Executive Summary & System Overview
MATSO is an advanced, AI-assisted wargaming and tactical decision-support platform. The core philosophy of MATSO relies on a **Neuro-Symbolic Architecture**, which strictly decouples deterministic physics (calculated by a Python engine) from probabilistic tactical reasoning (handled by Large Language Models)[cite: 2].

To guarantee future extensibility and maintainability, the system is designed with a **Highly Modular, Plug-and-Play Architecture**. All auxiliary functions—such as weather processing, elevation mapping, and specific AI node roles—are built as isolated modules that connect to the core orchestrator via standardized interfaces, preventing tight coupling and enabling seamless feature expansion.

---

## 2. Pluggable Architecture & Deployment Strategy

### 2.1 Core Orchestrator & Plugin Ecosystem
Instead of a monolithic backend, the Python FastAPI server acts as a lightweight **Core Orchestrator**. It routes data between independent, swappable modules:
*   **Core Bus:** Handles state management (MariaDB/Redis) and WebSocket broadcasting to Nuxt 4.
*   **Plugin Interfaces:** Strict API contracts (gRPC/REST) allow new modules to be attached or detached without altering the core logic.

### 2.2 Phase 1: Single-Node with Role-Switching (Current)
*   **Hardware:** One central server + one dedicated AI compute node (vLLM running a 31B model).
*   **Execution Logic (Time-Slicing):** The AI node dynamically switches roles (Strategic Planner, OPFOR Commander, AAR Analyst) by hot-swapping System Prompts and LoRA adapters.
*   **Image Arbitration Exception:** Any real-time video or image arbitration completely bypasses the AI node, utilizing deterministic, non-AI computer vision pipelines on the central server to guarantee strict, rule-based officiating.

### 2.3 Phase 2: Multi-Node Mixture-of-Agents (MoA) (Future Expansion)
*   **Parallel Proposers & Aggregators:** Specialized smaller models will act as "Proposers," simultaneously querying domain-specific RAG databases to generate parallel tactical assessments[cite: 2]. A central model acts as the "Aggregator" to synthesize proposals[cite: 2].
*   **Dynamic Debate & Termination:** Utilizes Wald's Sequential Probability Ratio Test (SPRT) to monitor consensus among Proposers, terminating the debate automatically once log-likelihood boundaries are crossed[cite: 2].

---

## 3. Modular Physics Engine & External Environment Plugins

LLMs lack inherent spatial and geometric awareness[cite: 2]. MATSO solves this by forcing all data through independent, deterministic physics modules before reaching the AI.

### 3.1 High-Resolution DTED Module (Spatial & Elevation)
*   **GeoTIFF Integration:** The system ingests Taiwan's Digital Terrain Elevation Data (DTED) using the provided `TW_ALL.tiff` file.
*   **Coordinate System:** WGS84.
*   **Module Specs:** Handled by a dedicated geospatial Python module (e.g., utilizing `rasterio` and `GDAL`). It parses the specific `PAMDataset` metadata to establish environmental boundaries:
    *   Minimum Elevation: `-3.0099999904633` meters (handling coastal/below-sea-level dynamics).
    *   Maximum Elevation: `3691.3601074219` meters (handling alpine combat scenarios).
    *   Mean Elevation: `754.01758094214` meters.
    *   Valid Data Percent: `34.99%`.
*   **Hexagonal Grid Mapping:** Drawing from the Geo-Commander framework, this module translates the continuous DTED raster into a machine-readable hexagonal grid to calculate Line of Sight (LOS), terrain masking, and movement penalties[cite: 2].

### 3.2 Pluggable Weather & Environment Module (Decoupled)
*   **Architecture:** Weather processing is extracted into an isolated, standalone microservice (External Weather Module). It prevents tight coupling with the core physics engine.
*   **Data Ingestion:** Automatically fetches and parses Central Weather Administration (CWA) data, including radar reflectivity, lightning strikes, and 1-hour precipitation forecasts.
*   **Standardized Output:** The module continuously publishes a standardized JSON payload to the Core Orchestrator.
    *   *Example Output:* `{"grid_id": "H-45", "rf_attenuation_db": 12.5, "mobility_modifier": 0.75, "uav_operability": false}`.
*   **Plug-and-Play:** If the CWA API changes, or if a user wants to inject synthetic/fictional weather data for a specific scenario, this module can be hot-swapped without touching the core physics engine or AI prompts.

### 3.3 Telemetry & Mesh Network Attenuation
*   **Integration:** The Core Orchestrator combines the outputs from the DTED Module and the Weather Module.
*   **RF Signal Degradation:** If terrain masking (from `TW_ALL.tiff`) and severe weather (from the Weather Module) block a unit's LoRa or Meshtastic node, the orchestrator flags the unit's status as `DEGRADED` or `OFFLINE`.
*   **Interception:** Commands outside physical weapons range or without LOS are instantly rejected by the core. The AI is never invoked, eliminating physical hallucination.

---

## 4. Domain Adaptation & Anti-Hallucination Guardrails

### 4.1 Retrieval-Augmented Fine-Tuning (RAFT)
*   Utilizes RAFT instead of standard RAG. The training dataset intentionally includes "distractor documents"[cite: 2].
*   The model learns to filter out noise and is strictly required to cite intelligence verbatim from golden documents, ensuring resilience against the "Fog of War"[cite: 2].

### 4.2 Mandatory Chain-of-Thought (CoT) & IHL Compliance
*   According to the WARBENCH benchmark, LLMs are susceptible to violating International Humanitarian Law (IHL) or Rules of Engagement (ROE) under stress[cite: 2].
*   MATSO enforces a strict architectural guardrail: the AI *must* generate an explicit Chain-of-Thought (CoT) reasoning log before outputting any tactical JSON payload[cite: 2]. This provides transparent logic for the AAR[cite: 2].

### 4.3 Optimal CPT to SFT Ratio
*   Adheres to the D-CPT Law: dedicating ~99.99% of the token budget to Continual Pre-Training (CPT) for internalizing military doctrine, while reserving a microscopic fraction for Supervised Fine-Tuning (SFT) to align output formats[cite: 2].

---

## 5. Core Database Architecture (Prisma / MariaDB)

The schema is designed to support the modular architecture, relying heavily on JSON fields to dynamically accept data from pluggable modules.

```prisma
generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "mysql"
  url      = env("DATABASE_URL")
}

// ==========================================
// 1. System & Environment Configurations
// ==========================================
model SystemConfiguration {
  id                String   @id @default(uuid())
  versionName       String
  simTickRateMs     Int      @default(1000)
  globalRules       Json     // Dynamic ROE & active plugin registries
  integrationConfig Json     // Endpoints for Pluggable Weather Module & ATAK servers
  updatedAt         DateTime @updatedAt
}

model WargameSession {
  id              String   @id @default(uuid())
  name            String
  startTime       DateTime @default(now())
  endTime         DateTime?
  currentWeather  Json     // Processed output strictly from the External Weather Module

  units           TacticalUnit[]
  eventLogs       TacticalEventLog[]
}

// ==========================================
// 2. Tactical Units & Fractal Command Chain
// ==========================================
enum UnitLevel { THEATER, CORPS, DIVISION, BRIGADE, BATTALION, COMPANY, PLATOON, SQUAD, FIRETEAM, INDIVIDUAL }
enum CommsState { ONLINE, DEGRADED, OFFLINE }
enum Faction { BLUE, RED, WHITE_CELL, ALLIED }

model TacticalUnit {
  id              String   @id @default(uuid())
  sessionId       String
  session         WargameSession @relation(fields: [sessionId], references: [id])

  designation     String
  unitLevel       UnitLevel
  faction         Faction

  // Fractal Hierarchy (Self-Referential)
  parentId        String?
  parent          TacticalUnit?  @relation("UnitHierarchy", fields: [parentId], references: [id], onDelete: Cascade)
  subUnits        TacticalUnit[] @relation("UnitHierarchy")

  // Extensible Properties & Live Telemetry
  attributes      Json     @default("{}")
  currentLat      Float?   // WGS84 Latitude
  currentLng      Float?   // WGS84 Longitude
  elevation       Float?   // Dynamically queried from the TW_ALL.tiff DTED Module
  healthStatus    Float    @default(100.0)
  commsStatus     CommsState @default(ONLINE)

  equipment       EquipmentInstance[]
  eventsInit      TacticalEventLog[] @relation("Initiator")
  eventsRecv      TacticalEventLog[] @relation("Target")
}

// ==========================================
// 3. Weaponeering & Hardware RAG Database
// ==========================================
model EquipmentTemplate {
  id              String   @id @default(uuid())
  name            String
  category        String   // KINETIC, SENSOR, COMMS, LOGISTICS, DRONE
  baseStats       Json     @default("{}")
  instances       EquipmentInstance[]
}

model EquipmentInstance {
  id              String   @id @default(uuid())
  templateId      String
  template        EquipmentTemplate @relation(fields: [templateId], references: [id])
  ownerId         String
  owner           TacticalUnit @relation(fields: [ownerId], references: [id], onDelete: Cascade)
  currentState    Json     @default("{}")
}

// ==========================================
// 4. Immutable Event Ledger (For AAR & Replay)
// ==========================================
model TacticalEventLog {
  id              String   @id @default(uuid())
  sessionId       String
  session         WargameSession @relation(fields: [sessionId], references: [id])
  timestamp       DateTime @default(now())
  eventType       String

  initiatorId     String?
  initiator       TacticalUnit? @relation("Initiator", fields: [initiatorId], references: [id])
  targetId        String?
  target          TacticalUnit? @relation("Target", fields: [targetId], references: [id])

  // Context Snapshots for accurate AAR generation
  weatherSnapshot Json     // Snapshot from the Weather Module at the time of event
  terrainModifier Float    // Modifier applied by the DTED module

  // AI Adjudication & Diagnostics
  reasoningChain  String?  @db.Text // Mandatory CoT to ensure IHL compliance & transparency[cite: 2]
  aiDecision      Json
  damageCalc      Float?

  @@index([sessionId, timestamp])
}
