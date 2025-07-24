# Alloy Migrator

A comprehensive tool to migrate Prometheus node_exporter and Promtail configurations to Grafana Alloy format.

## Overview

Grafana Alloy is a modern observability pipeline that combines the strengths of various telemetry collectors. This tool automates the migration of existing Prometheus node_exporter and Promtail configurations to Alloy's configuration format.

## Features

- **Promtail Migration**: Converts Promtail configurations including:
  - File scraping with glob patterns
  - Systemd journal collection
  - Complex pipeline stages (regex, JSON parsing, labels, metrics)
  - Relabeling rules
  - Multiple Loki endpoints

- **Node Exporter Migration**: Converts node_exporter configurations including:
  - Collector flags (systemd, textfile, etc.)
  - Textfile collector directory configuration
  - Automatic prometheus.scrape and remote_write setup

- **Combined Migration**: Migrate both configurations into a single Alloy config file

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/alloy-migrator.git
cd alloy-migrator

# Install using uv (recommended)
uv pip install -e .

# Or install using pip
pip install -e .
```

## Usage

After installation, the `alloy-migrator` command will be available:

### Migrate Promtail Configuration

```bash
alloy-migrator migrate-promtail path/to/promtail/config.yml

# Save to file
alloy-migrator migrate-promtail path/to/promtail/config.yml -o alloy-promtail.river

# Show configuration differences
alloy-migrator migrate-promtail path/to/promtail/config.yml --diff
```

### Migrate Node Exporter Configuration

```bash
# From systemd service file
alloy-migrator migrate-node-exporter --service-file /path/to/prometheus_exporter.service

# From ExecStart line directly
alloy-migrator migrate-node-exporter --exec-start "/usr/bin/node_exporter --collector.systemd"

# Save to file
alloy-migrator migrate-node-exporter --service-file service.file -o alloy-node.river
```

### Migrate Both Configurations

```bash
alloy-migrator migrate-all \
  --promtail path/to/promtail/config.yml \
  --node-service path/to/node_exporter.service \
  --output-dir ./output
```

### Validate Alloy Configuration

```bash
# Requires alloy binary to be installed
alloy-migrator validate alloy-config.river
```

## Example Migration

### Input: Promtail Config
```yaml
clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: system
    static_configs:
      - targets:
          - localhost
        labels:
          job: varlogs
          host: myhost
          __path__: /var/log/**/*.log
```

### Output: Alloy Config
```hcl
loki.write "default" {
  endpoint {
    url = "http://loki:3100/loki/api/v1/push"
  }
  external_labels {}
}

local.file_match "system" {
  path_targets {
    __address__ = "localhost"
    job = "varlogs"
    host = "myhost"
    __path__ = "/var/log/**/*.log"
  }
}

loki.source.file "system" {
  targets = [local.file_match.system.targets]
  forward_to = ["loki.write.default.receiver"]
}
```

## Configuration Mapping

### Promtail to Alloy Components

| Promtail | Alloy Component | Notes |
|----------|-----------------|-------|
| `clients` | `loki.write` | Loki endpoints |
| `scrape_configs.static_configs` | `local.file_match` + `loki.source.file` | File scraping |
| `scrape_configs.journal` | `loki.source.journal` | Systemd journal |
| `pipeline_stages` | `stages` block | Processing pipelines |
| `relabel_configs` | `relabel_rules` | Label manipulation |

### Node Exporter to Alloy Components

| Node Exporter | Alloy Component | Notes |
|--------------|-----------------|-------|
| `--collector.*` flags | `prometheus.exporter.unix` | Enabled collectors |
| `--collector.textfile.directory` | `textfile` block | Custom metrics |
| N/A | `prometheus.scrape` | Auto-generated scraper |
| N/A | `prometheus.remote_write` | Remote write endpoint |

## Post-Migration Steps

1. **Review the generated configuration**
   - Complex regex patterns in pipeline stages
   - Metrics configurations (may require manual adjustment)
   - Authentication credentials

2. **Update endpoints**
   - Set correct Prometheus remote write URLs
   - Verify Loki endpoints
   - Add authentication if needed

3. **Test the configuration**
   ```bash
   alloy run alloy-config.river
   ```

4. **Deploy to production**
   - Use systemd service or container
   - Monitor logs for any issues

## Limitations

- Pipeline stages with complex metrics require manual review
- Some Promtail features may not have direct Alloy equivalents
- Authentication credentials need to be manually added
- The tool performs best-effort conversion - always test thoroughly

## Development

```bash
# Install development dependencies
uv pip install -e .

# Run tests (when available)
pytest

# Format code
black main.py
```

## Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Submit a pull request

## License

MIT License - see LICENSE file for details