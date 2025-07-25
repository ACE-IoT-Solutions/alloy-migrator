#!/usr/bin/env python3
"""
Alloy Migrator - Tool to migrate Prometheus node_exporter and Promtail configurations to Grafana Alloy
"""

import yaml
import typer
from pathlib import Path
from typing import Dict, List, Any, Optional
from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
import re
import json

app = typer.Typer()
console = Console()


class PromtailToAlloyMigrator:
    """Migrates Promtail configurations to Grafana Alloy format"""
    
    def __init__(self, promtail_config: Dict[str, Any]):
        self.promtail_config = promtail_config
        self.alloy_components = []
        self.component_counter = {}
        
    def _get_component_id(self, component_type: str) -> str:
        """Generate unique component IDs"""
        if component_type not in self.component_counter:
            self.component_counter[component_type] = 0
        self.component_counter[component_type] += 1
        return f"{component_type}_{self.component_counter[component_type]}"
    
    def migrate(self) -> str:
        """Convert Promtail config to Alloy config"""
        # Process clients
        self._process_clients()
        
        # Process scrape configs
        self._process_scrape_configs()
        
        return self._generate_alloy_config()
    
    def _process_clients(self):
        """Convert Promtail clients to loki.write components"""
        clients = self.promtail_config.get('clients', [])
        
        for idx, client in enumerate(clients):
            component_id = f"default" if idx == 0 else f"client_{idx}"
            
            component = {
                'type': 'loki.write',
                'id': component_id,
                'config': {
                    'endpoint': {
                        'url': client['url']
                    },
                    'external_labels': {}
                }
            }
            
            # Add authentication if present
            if 'basic_auth' in client:
                component['config']['endpoint']['basic_auth'] = {
                    'username': client['basic_auth'].get('username', ''),
                    'password': client['basic_auth'].get('password', '')
                }
            
            self.alloy_components.append(component)
    
    def _process_scrape_configs(self):
        """Convert Promtail scrape configs to Alloy components"""
        scrape_configs = self.promtail_config.get('scrape_configs', [])
        
        for config in scrape_configs:
            job_name = config.get('job_name', 'unknown')
            
            # Handle file-based scraping
            if 'static_configs' in config:
                self._process_static_configs(config, job_name)
            
            # Handle journal scraping
            if 'journal' in config:
                self._process_journal_config(config, job_name)
    
    def _process_static_configs(self, config: Dict[str, Any], job_name: str):
        """Process static file configurations"""
        static_configs = config.get('static_configs', [])
        
        for idx, static_config in enumerate(static_configs):
            targets = static_config.get('targets', [])
            labels = static_config.get('labels', {})
            
            # Create file_match component
            file_match_id = f"{job_name}_{idx}" if idx > 0 else job_name
            
            path_targets = []
            for target in targets:
                path_target = {'__address__': target}
                path_target.update(labels)
                path_targets.append(path_target)
            
            file_match_component = {
                'type': 'local.file_match',
                'id': file_match_id,
                'config': {
                    'path_targets': path_targets
                }
            }
            self.alloy_components.append(file_match_component)
            
            # Create loki.source.file component
            source_file_component = {
                'type': 'loki.source.file',
                'id': file_match_id,
                'config': {
                    'targets': f"local.file_match.{file_match_id}.targets",
                    'forward_to': ["loki.write.default.receiver"]
                }
            }
            
            # Add pipeline stages if present
            if 'pipeline_stages' in config:
                stages = self._convert_pipeline_stages(config['pipeline_stages'])
                if stages:
                    source_file_component['config']['stages'] = stages
            
            self.alloy_components.append(source_file_component)
    
    def _process_journal_config(self, config: Dict[str, Any], job_name: str):
        """Process systemd journal configuration"""
        journal_config = config.get('journal', {})
        
        # Check if we need a loki.process component for pipeline stages
        needs_process = 'pipeline_stages' in config and config['pipeline_stages']
        forward_to = f"loki.process.{job_name}.receiver" if needs_process else "loki.write.default.receiver"
        
        journal_component = {
            'type': 'loki.source.journal',
            'id': job_name,
            'config': {
                'forward_to': [forward_to]
            }
        }
        
        # Add max_age if specified
        if 'max_age' in journal_config:
            journal_component['config']['max_age'] = journal_config['max_age']
        
        # Add labels
        if 'labels' in journal_config:
            journal_component['config']['labels'] = journal_config['labels']
        
        # Handle relabel configs with separate component
        if 'relabel_configs' in config:
            relabel_rules = self._convert_relabel_configs(config['relabel_configs'])
            if relabel_rules:
                # Create a separate loki.relabel component
                relabel_component = {
                    'type': 'loki.relabel',
                    'id': f'{job_name}_relabel',
                    'config': {
                        'forward_to': [forward_to],
                        '_rules': relabel_rules  # Special marker for rules
                    }
                }
                self.alloy_components.append(relabel_component)
                # Update journal to forward to relabel component
                journal_component['config']['forward_to'] = [f'loki.relabel.{job_name}_relabel.receiver']
        
        self.alloy_components.append(journal_component)
        
        # Create loki.process component if we have pipeline stages
        if needs_process:
            process_component = {
                'type': 'loki.process',
                'id': job_name,
                'config': {
                    'forward_to': ["loki.write.default.receiver"],
                    'stages': self._convert_pipeline_stages_for_process(config['pipeline_stages'])
                }
            }
            self.alloy_components.append(process_component)
    
    def _convert_pipeline_stages_for_process(self, stages: List[Dict[str, Any]]) -> List[Dict]:
        """Convert Promtail pipeline stages to Alloy process stages"""
        if not stages:
            return []
            
        alloy_stages = []
        
        for stage in stages:
            stage_config = self._convert_stage_to_dict(stage)
            if stage_config:
                alloy_stages.append(stage_config)
        
        return alloy_stages
    
    def _convert_stage_to_dict(self, stage: Dict[str, Any]) -> Dict:
        """Convert a single pipeline stage to dictionary format"""
        if 'regex' in stage:
            return {
                'type': 'stage.regex',
                'expression': stage['regex']['expression']
            }
        elif 'json' in stage:
            return {
                'type': 'stage.json',
                'expressions': stage['json'].get('expressions', {}),
                'source': stage['json'].get('source', '')
            }
        elif 'labels' in stage:
            return {
                'type': 'stage.labels',
                'values': stage['labels']
            }
        elif 'output' in stage:
            return {
                'type': 'stage.output',
                'source': stage['output']['source']
            }
        elif 'template' in stage:
            return {
                'type': 'stage.template',
                'source': stage['template']['source'],
                'template': stage['template']['template']
            }
        elif 'match' in stage:
            # For now, skip complex match stages as they need special handling
            return None
        elif 'metrics' in stage:
            # Skip metrics stages as they need manual review
            return None
        
        return None
    
    def _convert_pipeline_stages(self, stages: List[Dict[str, Any]]) -> str:
        """Convert Promtail pipeline stages to Alloy stage format (legacy)"""
        if not stages:
            return ""
            
        alloy_stages = []
        
        for stage in stages:
            stage_str = self._convert_single_stage(stage)
            if stage_str:
                alloy_stages.append(stage_str)
        
        return f'stage.{" | stage.".join(alloy_stages)}' if alloy_stages else ""
    
    def _convert_single_stage(self, stage: Dict[str, Any]) -> str:
        """Convert a single pipeline stage"""
        if 'match' in stage:
            return self._convert_match_stage(stage['match'])
        elif 'regex' in stage:
            return f'regex {{ expression = {json.dumps(stage["regex"]["expression"])} }}'
        elif 'json' in stage:
            return self._convert_json_stage(stage['json'])
        elif 'labels' in stage:
            if not stage['labels']:
                return 'labels {}'
            label_parts = []
            for k, v in stage['labels'].items():
                if v is None or v == "":
                    label_parts.append(f'{k} = ""')
                elif isinstance(v, str):
                    label_parts.append(f'{k} = {json.dumps(v)}')
                else:
                    label_parts.append(f'{k} = {v}')
            return f'labels {{ {", ".join(label_parts)} }}'
        elif 'metrics' in stage:
            return self._convert_metrics_stage(stage['metrics'])
        elif 'template' in stage:
            return f'template {{ source = "{stage["template"]["source"]}", template = {json.dumps(stage["template"]["template"])} }}'
        elif 'output' in stage:
            return f'output {{ source = "{stage["output"]["source"]}" }}'
        
        return ""
    
    def _convert_match_stage(self, match: Dict[str, Any]) -> str:
        """Convert match stage with nested stages"""
        parts = [f'match {{']
        
        if 'selector' in match:
            parts.append(f'  selector = {json.dumps(match["selector"])}')
        if 'pipeline_name' in match:
            parts.append(f'  pipeline_name = {json.dumps(match["pipeline_name"])}')
        if 'action' in match:
            parts.append(f'  action = "{match["action"]}"')
        
        if 'stages' in match:
            nested_stages = []
            for stage in match['stages']:
                nested_stage = self._convert_single_stage(stage)
                if nested_stage:
                    nested_stages.append(f'    stage.{nested_stage}')
            if nested_stages:
                parts.append('  stage {')
                parts.extend(nested_stages)
                parts.append('  }')
        
        parts.append('}')
        return '\n'.join(parts)
    
    def _convert_json_stage(self, json_config: Dict[str, Any]) -> str:
        """Convert JSON stage"""
        parts = ['json {']
        
        if 'source' in json_config:
            parts.append(f'  source = "{json_config["source"]}"')
        
        if 'expressions' in json_config:
            parts.append('  expressions = {')
            for key, value in json_config['expressions'].items():
                parts.append(f'    {key} = "{value}"')
            parts.append('  }')
        
        parts.append('}')
        return '\n'.join(parts)
    
    def _convert_metrics_stage(self, metrics: Dict[str, Any]) -> str:
        """Convert metrics stage"""
        # This is a simplified conversion - full metrics conversion would be more complex
        return "metrics { /* Metrics configuration requires manual review */ }"
    
    def _convert_relabel_configs(self, relabel_configs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert relabel configurations"""
        rules = []
        
        for config in relabel_configs:
            rule = {}
            
            if 'source_labels' in config:
                rule['source_labels'] = config['source_labels']
            if 'target_label' in config:
                rule['target_label'] = config['target_label']
            if 'regex' in config:
                rule['regex'] = config['regex']
            if 'replacement' in config:
                rule['replacement'] = config['replacement']
            if 'action' in config:
                rule['action'] = config['action']
            
            rules.append(rule)
        
        return rules
    
    def _generate_alloy_config(self) -> str:
        """Generate the final Alloy configuration"""
        config_lines = []
        
        for component in self.alloy_components:
            config_lines.extend(self._format_component(component))
            config_lines.append("")  # Empty line between components
        
        return '\n'.join(config_lines)
    
    def _format_component(self, component: Dict[str, Any]) -> List[str]:
        """Format a component to Alloy syntax"""
        lines = [f'{component["type"]} "{component["id"]}" {{']
        
        config = component['config']
        for key, value in config.items():
            if key == '_rules':
                # Special handling for rules - format them as direct rule blocks
                for rule in value:
                    lines.append('  rule {')
                    for k, v in rule.items():
                        lines.append(f'    {k} = {json.dumps(v)}')
                    lines.append('  }')
                    lines.append("")
            else:
                lines.extend(self._format_config_value(key, value, indent=2))
        
        lines.append('}')
        return lines
    
    def _format_config_value(self, key: str, value: Any, indent: int) -> List[str]:
        """Format configuration values with proper indentation"""
        indent_str = ' ' * indent
        lines = []
        
        if key == 'stages' and isinstance(value, list):
            # Special handling for stages in loki.process
            for stage in value:
                if isinstance(stage, dict) and 'type' in stage:
                    stage_type = stage['type']
                    lines.append(f'{indent_str}{stage_type} {{')
                    for k, v in stage.items():
                        if k != 'type':
                            if isinstance(v, dict):
                                lines.append(f'{indent_str}  {k} = {{')
                                for kk, vv in v.items():
                                    lines.append(f'{indent_str}    {kk} = {json.dumps(vv)}')
                                lines.append(f'{indent_str}  }}')
                            else:
                                lines.append(f'{indent_str}  {k} = {json.dumps(v)}')
                    lines.append(f'{indent_str}}}')
                    lines.append("")  # Empty line between stages
        elif isinstance(value, dict):
            # Special cases where dict should be formatted as attribute
            if key in ['labels', 'external_labels']:
                if value:
                    lines.append(f'{indent_str}{key} = {{')
                    items = []
                    for k, v in value.items():
                        items.append(f'{k} = {json.dumps(v)}')
                    lines.append(f'{indent_str}  {", ".join(items)},')
                    lines.append(f'{indent_str}}}')
                else:
                    lines.append(f'{indent_str}{key} = {{}}')
            elif value:  # Regular block formatting for non-empty dicts
                lines.append(f'{indent_str}{key} {{')
                for k, v in value.items():
                    lines.extend(self._format_config_value(k, v, indent + 2))
                lines.append(f'{indent_str}}}')
            else:
                lines.append(f'{indent_str}{key} = {{}}')
        elif isinstance(value, list):
            if all(isinstance(item, dict) for item in value):
                # Handle lists of dicts (like relabel_rules, path_targets)
                if len(value) == 1:
                    # Single item list
                    lines.append(f'{indent_str}{key} = [{{')
                    for k, v in value[0].items():
                        if isinstance(v, list):
                            lines.append(f'{indent_str}  {k} = {json.dumps(v)},')
                        else:
                            lines.append(f'{indent_str}  {k} = {json.dumps(v)},')
                    lines.append(f'{indent_str}}}]')
                else:
                    # Multiple items
                    lines.append(f'{indent_str}{key} = [')
                    for i, item in enumerate(value):
                        lines.append(f'{indent_str}  {{')
                        for k, v in item.items():
                            lines.append(f'{indent_str}    {k} = {json.dumps(v)},')
                        lines.append(f'{indent_str}  }},')
                    lines.append(f'{indent_str}]')
            elif key == 'forward_to':
                # Special handling for forward_to - component references should not be quoted
                refs = []
                for item in value:
                    if isinstance(item, str) and (item.startswith('loki.') or item.startswith('prometheus.')):
                        refs.append(item)
                    else:
                        refs.append(json.dumps(item))
                lines.append(f'{indent_str}{key} = [{", ".join(refs)}]')
            else:
                lines.append(f'{indent_str}{key} = {json.dumps(value)}')
        elif isinstance(value, str) and (value.startswith('local.') or value.startswith('loki.') or value.startswith('prometheus.')):
            # Reference to another component
            lines.append(f'{indent_str}{key} = {value}')
        else:
            lines.append(f'{indent_str}{key} = {json.dumps(value)}')
        
        return lines


class NodeExporterToAlloyMigrator:
    """Migrates node_exporter configurations to Grafana Alloy format"""
    
    def __init__(self, service_file_path: Optional[Path] = None, 
                 exec_start_line: Optional[str] = None):
        self.service_file_path = service_file_path
        self.exec_start_line = exec_start_line
        self.collectors = []
        self.textfile_directory = None
        self.parse_service_file()
    
    def parse_service_file(self):
        """Parse systemd service file or ExecStart line"""
        if self.exec_start_line:
            exec_line = self.exec_start_line
        elif self.service_file_path and self.service_file_path.exists():
            content = self.service_file_path.read_text()
            exec_match = re.search(r'ExecStart=(.+)', content)
            if exec_match:
                exec_line = exec_match.group(1)
            else:
                return
        else:
            return
        
        # Parse command line arguments
        if '--collector.systemd' in exec_line:
            self.collectors.append('systemd')
        
        textfile_match = re.search(r'--collector\.textfile\.directory\s+([^\s]+)', exec_line)
        if textfile_match:
            self.collectors.append('textfile')
            self.textfile_directory = textfile_match.group(1)
    
    def migrate(self) -> str:
        """Convert node_exporter config to Alloy config"""
        config_lines = ['prometheus.exporter.unix "node_exporter" {']
        
        # Add enabled collectors
        if self.collectors:
            collectors_str = ', '.join([f'"{c}"' for c in self.collectors])
            config_lines.append(f'  enable_collectors = [{collectors_str}]')
        
        # Add textfile configuration if present
        if self.textfile_directory:
            config_lines.append('')
            config_lines.append('  textfile {')
            config_lines.append(f'    directory = "{self.textfile_directory}"')
            config_lines.append('  }')
        
        # Add systemd configuration if present
        if 'systemd' in self.collectors:
            config_lines.append('')
            config_lines.append('  systemd {')
            config_lines.append('    enable_restarts = true')
            config_lines.append('  }')
        
        config_lines.append('}')
        config_lines.append('')
        
        # Add prometheus.scrape component to scrape the exporter
        config_lines.append('prometheus.scrape "node_exporter" {')
        config_lines.append('  targets = prometheus.exporter.unix.node_exporter.targets')
        config_lines.append('  forward_to = [prometheus.remote_write.default.receiver]')
        config_lines.append('}')
        config_lines.append('')
        
        # Add a placeholder for prometheus.remote_write
        config_lines.append('prometheus.remote_write "default" {')
        config_lines.append('  endpoint {')
        config_lines.append('    url = "http://your-prometheus-endpoint/api/v1/write"')
        config_lines.append('  }')
        config_lines.append('}')
        
        return '\n'.join(config_lines)


@app.command()
def migrate_promtail(
    config_file: Path = typer.Argument(..., help="Path to Promtail config.yml file"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file path"),
    show_diff: bool = typer.Option(False, "--diff", "-d", help="Show configuration differences")
):
    """Migrate Promtail configuration to Grafana Alloy format"""
    
    if not config_file.exists():
        console.print(f"[red]Error:[/red] Config file {config_file} not found")
        raise typer.Exit(1)
    
    try:
        with open(config_file, 'r') as f:
            promtail_config = yaml.safe_load(f)
        
        migrator = PromtailToAlloyMigrator(promtail_config)
        alloy_config = migrator.migrate()
        
        if output:
            output.write_text(alloy_config)
            console.print(f"[green]✓[/green] Migrated configuration written to {output}")
        else:
            console.print(Panel(
                Syntax(alloy_config, "hcl", theme="monokai"),
                title="Grafana Alloy Configuration",
                expand=False
            ))
        
        if show_diff:
            console.print("\n[yellow]Note:[/yellow] Configuration requires manual review, especially:")
            console.print("  - Pipeline stages with complex regex/metrics")
            console.print("  - Authentication credentials")
            console.print("  - Remote write endpoints")
            
    except Exception as e:
        console.print(f"[red]Error:[/red] Failed to migrate configuration: {e}")
        raise typer.Exit(1)


@app.command()
def migrate_node_exporter(
    service_file: Optional[Path] = typer.Option(None, "--service-file", "-s", help="Path to systemd service file"),
    exec_start: Optional[str] = typer.Option(None, "--exec-start", "-e", help="ExecStart line from service file"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file path")
):
    """Migrate node_exporter configuration to Grafana Alloy format"""
    
    if not service_file and not exec_start:
        console.print("[red]Error:[/red] Provide either --service-file or --exec-start")
        raise typer.Exit(1)
    
    try:
        migrator = NodeExporterToAlloyMigrator(service_file, exec_start)
        alloy_config = migrator.migrate()
        
        if output:
            output.write_text(alloy_config)
            console.print(f"[green]✓[/green] Migrated configuration written to {output}")
        else:
            console.print(Panel(
                Syntax(alloy_config, "hcl", theme="monokai"),
                title="Grafana Alloy Configuration",
                expand=False
            ))
        
        console.print("\n[yellow]Note:[/yellow] Update the prometheus.remote_write endpoint URL")
        
    except Exception as e:
        console.print(f"[red]Error:[/red] Failed to migrate configuration: {e}")
        raise typer.Exit(1)


@app.command()
def migrate_all(
    promtail_config: Optional[Path] = typer.Option(None, "--promtail", "-p", help="Path to Promtail config.yml"),
    node_service: Optional[Path] = typer.Option(None, "--node-service", "-n", help="Path to node_exporter service file"),
    output_dir: Path = typer.Option(Path("."), "--output-dir", "-o", help="Output directory for configurations")
):
    """Migrate both Promtail and node_exporter configurations"""
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    console.print("[bold]Grafana Alloy Migration Tool[/bold]\n")
    
    configs = []
    
    # Migrate Promtail if provided
    if promtail_config and promtail_config.exists():
        console.print("📋 Migrating Promtail configuration...")
        try:
            with open(promtail_config, 'r') as f:
                promtail_yaml = yaml.safe_load(f)
            
            migrator = PromtailToAlloyMigrator(promtail_yaml)
            alloy_promtail = migrator.migrate()
            configs.append(("// Promtail Migration", alloy_promtail))
            
            console.print("[green]✓[/green] Promtail configuration migrated")
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to migrate Promtail: {e}")
    
    # Migrate node_exporter if provided
    if node_service and node_service.exists():
        console.print("📊 Migrating node_exporter configuration...")
        try:
            migrator = NodeExporterToAlloyMigrator(service_file_path=node_service)
            alloy_node = migrator.migrate()
            configs.append(("// Node Exporter Migration", alloy_node))
            
            console.print("[green]✓[/green] node_exporter configuration migrated")
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to migrate node_exporter: {e}")
    
    # Combine configurations
    if configs:
        combined_config = "\n\n".join([f"{comment}\n{config}" for comment, config in configs])
        output_file = output_dir / "alloy-config.river"
        output_file.write_text(combined_config)
        
        console.print(f"\n[green]✓[/green] Combined configuration written to {output_file}")
        console.print("\n[yellow]Next steps:[/yellow]")
        console.print("1. Review and update the configuration:")
        console.print("   - Set correct remote write endpoints")
        console.print("   - Update authentication credentials")
        console.print("   - Verify pipeline stages and metrics")
        console.print("2. Test with: alloy run alloy-config.river")
        console.print("3. Deploy to production")
    else:
        console.print("[yellow]No configurations to migrate[/yellow]")


@app.command()
def validate(
    config_file: Path = typer.Argument(..., help="Path to Alloy configuration file")
):
    """Validate an Alloy configuration file (requires alloy binary)"""
    
    import subprocess
    
    try:
        result = subprocess.run(
            ["alloy", "validate", str(config_file)],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            console.print(f"[green]✓[/green] Configuration is valid")
        else:
            console.print(f"[red]✗[/red] Configuration has errors:")
            console.print(result.stderr)
            
    except FileNotFoundError:
        console.print("[yellow]Warning:[/yellow] alloy binary not found. Install it to validate configurations.")
        console.print("Visit: https://grafana.com/docs/alloy/latest/get-started/install/")


def main():
    """Alloy Migrator - Migrate Prometheus exporters to Grafana Alloy"""
    app()


if __name__ == "__main__":
    main()