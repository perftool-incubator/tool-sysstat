# tool-sysstat
[![CI Actions Status](https://github.com/perftool-incubator/tool-sysstat/workflows/crucible-ci/badge.svg)](https://github.com/perftool-incubator/tool-sysstat/actions)

System performance metric collection and post-processing for the [crucible](https://github.com/perftool-incubator/crucible) framework using the Linux [sysstat](https://github.com/sysstat/sysstat) utilities.

## Subtools

Sysstat runs one or more subtools in parallel during test execution. The default set is all four:

| Subtool | Data Collected |
|---------|---------------|
| mpstat | Per-CPU utilization statistics |
| sar | Memory (paging, swapping, utilization), network device statistics |
| iostat | Disk I/O throughput and latency |
| pidstat | Per-process CPU, memory, and I/O statistics |

## Configuration

The start script accepts two parameters:
- `--subtools <list>` — Comma-separated list of subtools to run (default: `mpstat,sar,iostat,pidstat`)
- `--interval <seconds>` — Collection interval in seconds (default: `3`)

## Integration

Sysstat runs as a profiler tool on endpoint nodes. It is allowed on profiler, master, and worker collector roles but blocked on client and server roles. The post-processor (`sysstat-post-process`) converts raw sysstat output into crucible's canonical metric format.

### rickshaw.json
Defines how sysstat integrates with rickshaw: which files to deploy to engines, which endpoint/collector-type combinations are allowed or blocked, and the post-processing script.
