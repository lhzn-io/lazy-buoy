# Long-Term Storage and Data Archiving Strategy

This document details the strategy for managing local disk space on the solar-powered edge node `lhznbuoy` and offloading logs and audio files to a high-capacity host or cloud environment. It also details the long-term data eviction policy, replacing old raw audio and full transcripts with high-density LLM-generated summaries to minimize storage costs.

---

## 1. Physical Storage Constraints

The edge node `lhznbuoy` runs on a Raspberry Pi 5 with an industrial microSD card. This presents two primary physical constraints:
* **Capacity Limit**: High-frequency scanning of multiple VHF channels generates substantial audio data. A single 10-second segment consumes approximately 320 KB. In a busy harbor, this can accumulate to several gigabytes per week.
* **Write Amplification**: Continuous write and delete cycles on microSD cards accelerate wear, leading to potential card corruption and kernel panics. We must minimize local write operations and evict data systematically.

---

## 2. Multi-Tiered Handoff and Offloading Mechanism

To protect the microSD card and preserve data, we implement a multi-tiered storage architecture:

```
+------------------------------------------------------------+
|                       lhznbuoy (Edge)                      |
|  - MicroSD Card                                            |
|  - Tier 1: Local Raw Audio (0-3 Days)                      |
|  - Tier 1: Local Full Transcripts (0-14 Days)              |
+-----------------------------+------------------------------+
                              |
                              | Rsync / WireGuard VPN (192.168.7.0/24)
                              v
+------------------------------------------------------------+
|                 Institutional Host (garnet)                |
|  - High Capacity UMA / NVMe SSD                            |
|  - Tier 2: Remote Raw Audio Archive (3+ Days)              |
|  - Tier 2: Remote Full Transcripts (14+ Days)              |
|  - Local SFT LLM Summarizer (Daily Duty-Cycle)             |
+-----------------------------+------------------------------+
                              |
                              | API Sync / SQLite Compression
                              v
+------------------------------------------------------------+
|                       lhznbuoy (Edge)                      |
|  - Tier 3: Compressed Daily Summaries Table (14+ Days)     |
|  - 99% Storage Reduction; Retains Semantic Search          |
+------------------------------------------------------------+
```

### 2.1 Offloading to garnet.internal
We leverage the WireGuard VPN connection (`192.168.7.0/24`) to securely sync files to the central compute node (`garnet.internal` or `skipper.internal`):
* **Daily Cron Handoff**: A lightweight script runs at 01:00 UTC daily. It uses `rsync` to mirror the local `/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio/` directory and the SQLite database `/home/pi/Projects/lhzn-io/lazy-buoy/logs/transcripts.sqlite` to `/home/lhzn/Archive/buoy/` on the institutional host.
* **Network-Aware Transmission**: The sync script checks for connection stability (VPN ping checks) before initiating transfers. If the VPN link is down, the data remains queued locally until the next cycle.

---

## 3. Data Eviction and Retention Policy

The local storage on `lhznbuoy` will be managed using a sliding window:

| Data Type | Tier 1 (Local Pi) | Tier 2 (Remote Host) | Tier 3 (Archived / Evicted) |
| :--- | :--- | :--- | :--- |
| **Raw WAV Audio** | 3 Days | Unlimited (SSD) | Evicted from Pi |
| **Full Transcripts** | 14 Days | Unlimited (SQLite) | Evicted from Pi |
| **LLM Summaries** | Unlimited (SQLite) | Unlimited (SQLite) | Retained indefinitely on Pi |

### 3.1 Local Eviction Process
Every 6 hours, the daemon's pruning thread (`prune_old_records`) will execute:
1. **Raw Audio Cleanup**: Delete WAV files older than 3 days from `/logs/audio/`. The file path reference in SQLite is set to `NULL` or marked as `offloaded`.
2. **Text Transcript Cleanup**: Delete full transcripts older than 14 days from the local SQLite `vhf_transcripts` table and the LanceDB vector table.

---

## 4. Long-Term LLM-Based Summarization Strategy

To preserve semantic querying and historical insights on the edge buoy without storing megabytes of raw text, we implement an LLM-based compression strategy.

### 4.1 Daily Summary Generation on garnet.internal
The institutional gateway `garnet` (Mac Studio with 128GB UMA running MLX) executes the summarization pipeline:
1. **Data Aggregation**: Group the synced transcripts for the day by channel (e.g. Marine-16, Marine-68, NOAA-WX1) and hour block.
2. **LLM Execution**: Run a quantized SFT LLM (such as Llama-3-8B-Instruct) over the aggregated transcripts.
3. **Prompt template**:
   ```
   Generate a high-density, objective summary of the following marine VHF transcripts. 
   Focus on vessel names, locations, weather alerts, and activity types. 
   Exclude static, radio checks, and repeated weather loops.
   ```
4. **Output format**: The LLM outputs a structured JSON summary, for example:
   ```json
   {
     "date": "2026-06-02",
     "channel": "Marine-68",
     "summary_text": "Fishing activity centered near Fire Island. Several vessels reported successful striped bass catches. No distress or emergency calls recorded.",
     "keywords": ["fishing", "striped bass", "Fire Island"]
   }
   ```

### 4.2 Syncing Summaries Back to the Buoy
* The generated JSON summaries are synced back to a new `daily_summaries` table in the buoy's local SQLite database.
* The summary text is embedded using `sentence-transformers` and indexed in a secondary LanceDB table (`semantic_summaries`).
* **Storage Efficiency**: A full day of raw transcripts (typically 50,000 to 100,000 words) is compressed into a few structured summary paragraphs (under 500 words total). This reduces the local database storage footprint by approximately 99%, while still allowing the buoy's semantic search interface to retrieve daily historical trends.
