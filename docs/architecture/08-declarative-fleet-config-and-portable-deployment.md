# Architectural Blueprint: Declarative Fleet Configuration & Portable Deployment (SPEC-HK-008)

## 1. System Topology & Architecture

This blueprint specifies the decoupling of `HermesKarma`'s telemetry collection and deployment layer from specific homelab hardware, establishing a declarative, vendor-agnostic architecture.

```mermaid
graph TD
    subgraph Configuration Sources
        C1[HERMES_KARMA_NODES_JSON] --> CFG[FleetConfigParser]
        C2[fleet.yaml / fleet.json] --> CFG
        C3[.env Parameters] --> CFG
    end

    subgraph Core Configuration
        CFG -->|Validated Settings| KS[KarmaSettings Model]
    end

    subgraph Telemetry Collector Subsystem
        KS -->|Node Definitions| NTC[NodeTelemetryCollector]
        NTC --> StrategyFactory[Hardware Driver Factory]
        
        StrategyFactory --> D1[AmdGpuDriver<br/>sysfs / hwmon]
        StrategyFactory --> D2[NvidiaGpuDriver<br/>NVML / pynvml]
        StrategyFactory --> D3[AppleSiliconDriver<br/>sysctl / powermetrics]
        StrategyFactory --> D4[GenericLinuxDriver<br/>procfs]
        StrategyFactory --> D5[PrometheusNodeScraper<br/>HTTP :9100]
        
        StrategyFactory --> IP[InferenceEngineProbe<br/>Ollama / llama-server / vLLM]
    end

    subgraph Normalization & Storage
        D1 & D2 & D3 & D4 & D5 & IP --> Norm[Normalized Telemetry Contract]
        Norm --> MS[MetadataService / node_gpu_samples]
        Norm --> SSE[Live Telemetry SSE]
    end

    subgraph Deployment Topologies
        BareMetal[Bare Metal Homelab / Systemd]
        DockerComp[Docker Compose]
        K8sCluster[Kubernetes Cluster / Cloud Pods]
    end
```

---

## 2. Configuration Parser & Precedence Hierarchy

Configuration resolution follows a deterministic precedence waterfall:

1. **Inline Environment JSON (`HERMES_KARMA_NODES_JSON`):** Takes absolute precedence; designed for zero-filesystem cloud environments (Kubernetes ConfigMaps, AWS ECS, Google Cloud Run).
2. **Declarative Configuration File (`FLEET_CONFIG_PATH`):** Points to a `fleet.yaml` or `fleet.json` file on disk. Defaults to `~/.hermes_karma/fleet.yaml` if it exists.
3. **Legacy Environment Variables:** Parses `COORDINATOR_*`, `REMOTE_WORKER_*`, `CHUNKITO_*`, and `BEEHIVE_*` variables from `.env` for full backward compatibility.
4. **Localhost Loopback Default:** If zero nodes are configured, automatically initializes a single local coordinator node reading local system telemetry.

---

## 3. Class Hierarchy & Hardware Driver Strategy Pattern

```mermaid
classDiagram
    class NodeTelemetryCollector {
        +List~NodeConfig~ nodes_config
        +float cache_ttl
        +get_configured_nodes() List~Dict~
        +collect_node_telemetry(node) Dict
        +collect_all_telemetry() Dict
    }

    class HardwareDriver {
        <<interface>>
        +is_supported() bool
        +collect_metrics(node) Dict
    }

    class AmdGpuDriver {
        +collect_sysfs_metrics() Dict
        +collect_metrics(node) Dict
    }

    class NvidiaGpuDriver {
        +nvml_handle
        +collect_nvml_metrics() Dict
        +collect_metrics(node) Dict
    }

    class AppleSiliconDriver {
        +collect_darwin_metrics() Dict
        +collect_metrics(node) Dict
    }

    class GenericLinuxDriver {
        +collect_procfs_metrics() Dict
        +collect_metrics(node) Dict
    }

    class PrometheusNodeScraper {
        +target_host: str
        +port: int
        +scrape_metrics() Dict
    }

    NodeTelemetryCollector --> HardwareDriver
    HardwareDriver <|-- AmdGpuDriver
    HardwareDriver <|-- NvidiaGpuDriver
    HardwareDriver <|-- AppleSiliconDriver
    HardwareDriver <|-- GenericLinuxDriver
    HardwareDriver <|-- PrometheusNodeScraper
```

---

## 4. Container Deployment & Volume Boundaries

```mermaid
flowchart LR
    subgraph Host ["Host System / Homelab / K8s Node"]
        H_State["~/.hermes/state.db (SQLite WAL)"]
        H_Skills["~/.hermes/skills/"]
        H_Karma["~/.hermes_karma/metadata.db"]
        H_Sysfs["/sys (Hardware Sensors)"]
    end

    subgraph Container ["HermesKarma Container (Rootless UID 1000)"]
        C_App["FastAPI Backend (:8020)"]
        C_Static["Vanilla JS Dashboard"]
    end

    H_State -->|Mount :ro (Read-Only)| C_App
    H_Skills -->|Mount :ro (Read-Only)| C_App
    H_Karma -->|Mount :rw (Read-Write)| C_App
    H_Sysfs -->|Mount :ro (Optional)| C_App
```

### Safety Rules:
- **Hermes State Isolation:** The container must **never** mount `~/.hermes` in read-write mode. Read-only (`:ro`) mounting preserves WAL integrity and prevents database lock contentions with active agent CLI sessions.
- **Rootless Execution:** Container runs strictly as non-root user `karma` (UID/GID 1000).
- **Graceful Hardware Degradation:** If `/sys` is not mounted into the container, the driver factory seamlessly falls back to `GenericLinuxDriver` or Prometheus network scraping without crashing.
