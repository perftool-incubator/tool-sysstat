# Sysstat Tool

## Purpose
Collects system performance metrics using Linux sysstat utilities (mpstat, sar, iostat, pidstat) during benchmark execution and post-processes the raw output into crucible's canonical metric format.

## Languages
- Bash: collection scripts (`sysstat-start`, `sysstat-stop`)
- Perl: post-processor (`sysstat-post-process`)

## Key Files
| File | Purpose |
|------|---------|
| `sysstat-start` | Launches configured subtools with `--subtools` and `--interval` parameters |
| `sysstat-stop` | Kills running collectors, compresses output with xz |
| `sysstat-post-process` | Parses raw sysstat output into crucible metrics (uses `toolbox::metrics`, `toolbox::json` from `$TOOLBOX_HOME/perl`) |
| `rickshaw.json` | Rickshaw integration: endpoint allow/block lists, file deployment, post-process script |
| `workshop.json` | Engine image build: compiles sysstat v12.5.1 from source |

## Configuration
- `--subtools <list>` — Comma-separated subtools to run (default: `mpstat,sar,iostat,pidstat`)
- `--interval <seconds>` — Collection interval (default: `3`)

## Conventions
- Primary branch is `master`
- Runs as a profiler tool on master/worker/profiler roles, blocked on client/server
- Standard Bash/Perl modelines and 4-space indentation
