#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

TOOLBOX_HOME = os.environ.get("TOOLBOX_HOME")
if TOOLBOX_HOME:
    sys.path.append(str(Path(TOOLBOX_HOME) / "python"))

from toolbox.cdm_metrics import CDMMetrics
from toolbox.fileio import open_read_text_file
from toolbox.system_cpu_topology import build_cpu_topology, get_cpu_topology


def build_netdev_types():
    netdev_types = {}
    netdev_file = "netdev-types.txt"
    if os.path.isfile(netdev_file):
        try:
            fh, _ = open_read_text_file(netdev_file)
            for line in fh:
                line = line.strip()
                m = re.match(r'^(\S+)\s+\.\./\.\./devices/(\S+)$', line)
                if m:
                    netdev_types[m.group(1)] = m.group(2)
            fh.close()
        except FileNotFoundError:
            pass
    return netdev_types


def get_netdev_type(name, netdev_types):
    if name in netdev_types:
        if netdev_types[name].startswith("pci"):
            return "physical"
        elif netdev_types[name].startswith("virtual"):
            return "virtual"
        else:
            return "unknown"
    return "unknown"


def get_hms_ms(hour, minute, sec):
    return 1000 * (int(hour) * 3600 + int(minute) * 60 + int(sec))


def advance_ymd(ymd_ms):
    return ymd_ms + (1000 * 60 * 60 * 24)


def ymd_to_epoch_ms(ymd):
    result = subprocess.run(
        ["date", "+%s%N", "-d", ymd, "-u"],
        capture_output=True, text=True
    )
    return int(result.stdout.strip()) // 1000000



def process_sar(source, log_file):
    print(f"Post-processing for {source} started")
    metrics = CDMMetrics()
    netdev_types = build_netdev_types()
    ymd_timestamp_ms = None
    scan_mode = ""
    hms_ms = None
    prev_hms_ms = None
    no_names = {}
    desc = {"class": "throughput", "source": source}

    try:
        fh, _ = open_read_text_file(log_file)
    except FileNotFoundError:
        print(f"ERROR: could not open {log_file}")
        return

    for line in fh:
        line = line.rstrip("\n")

        m = re.match(r'^Linux\s\S+\s\S+\s+(\d+-\d+-\d+)\s+\S+\s+\S+', line)
        if m:
            ymd_timestamp_ms = ymd_to_epoch_ms(m.group(1))
            continue

        if source == "sar-mem":
            if re.search(r'pgpgin/s\s+pgpgout/s\s+fault/s\s+majflt/s\s+pgfree/s\s+pgscank/s\s+pgscand/s\s+pgsteal/s\s+%vmeff$', line):
                scan_mode = "paging"
            elif re.search(r'pswpin/s\s+pswpout/s$', line):
                scan_mode = "swapping"
            elif re.search(r'%smem-10\s+%smem-60\s+%smem-300\s+%smem\s+%fmem-10\s+%fmem-60\s+%fmem-300\s+%fmem$', line):
                scan_mode = "memory-starved"
            elif re.search(r'kbmemfree\s+kbavail\s+kbmemused\s+%memused\s+kbbuffers\s+kbcached\s+kbcommit\s+%commit\s+kbactive\s+kbinact\s+kbdirty$', line):
                scan_mode = "memory-utilization"
            elif scan_mode == "paging":
                m = re.match(r'(\d+):(\d+):(\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)$', line)
                if m:
                    hms_ms = get_hms_ms(m.group(1), m.group(2), m.group(3))
                    if prev_hms_ms is not None and prev_hms_ms > hms_ms:
                        ymd_timestamp_ms = advance_ymd(ymd_timestamp_ms)
                    sample = {"end": ymd_timestamp_ms + hms_ms}
                    for metric_type, val in [
                        ("KB-Paged-in-sec", m.group(4)),
                        ("KB-Paged-out-sec", m.group(5)),
                        ("Pages-freed-sec", m.group(8)),
                        ("kswapd-scanned-pages-sec", m.group(9)),
                        ("scanned-pages-sec", m.group(10)),
                        ("reclaimed-pages-sec", m.group(11)),
                        ("VM-Efficiency", m.group(12)),
                    ]:
                        desc["type"] = metric_type
                        sample["value"] = float(val)
                        metrics.log_sample(source, desc, no_names, sample)
                    faults_minor = float(m.group(6)) - float(m.group(7))
                    faults_major = float(m.group(7))
                    desc["type"] = "Page-faults-sec"
                    for fault_type, val in [("minor", faults_minor), ("major", faults_major)]:
                        sample["value"] = val
                        metrics.log_sample(source, desc, {"type": fault_type}, sample)
                elif line == "":
                    scan_mode = ""
            elif scan_mode == "swapping":
                m = re.match(r'(\d+):(\d+):(\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)$', line)
                if m:
                    hms_ms = get_hms_ms(m.group(1), m.group(2), m.group(3))
                    if prev_hms_ms is not None and prev_hms_ms > hms_ms:
                        ymd_timestamp_ms = advance_ymd(ymd_timestamp_ms)
                    sample = {"end": ymd_timestamp_ms + hms_ms}
                    desc["type"] = "Pages-swapped-in-sec"
                    sample["value"] = float(m.group(4))
                    metrics.log_sample(source, desc, no_names, sample)
                    desc["type"] = "Pages-swapped-out-sec"
                    sample["value"] = float(m.group(5))
                    metrics.log_sample(source, desc, no_names, sample)
                elif line == "":
                    scan_mode = ""
            elif scan_mode == "memory-starved":
                m = re.match(r'(\d+):(\d+):(\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)$', line)
                if m:
                    hms_ms = get_hms_ms(m.group(1), m.group(2), m.group(3))
                    if prev_hms_ms is not None and prev_hms_ms > hms_ms:
                        ymd_timestamp_ms = advance_ymd(ymd_timestamp_ms)
                    sample = {"end": ymd_timestamp_ms + hms_ms}
                    for tw, val in [("010s", m.group(4)), ("060s", m.group(5)), ("300s", m.group(6)), ("last_interval", m.group(7))]:
                        desc["type"] = f"%-Time-Tasks-Waiting-on-Memory-{tw}"
                        sample["value"] = float(val)
                        metrics.log_sample(source, desc, no_names, sample)
                    for tw, val in [("010s", m.group(8)), ("060s", m.group(9)), ("300s", m.group(10)), ("last_interval", m.group(11))]:
                        desc["type"] = f"%-Time-Non-Idle-Tasks-Stalled-on-Memory-{tw}"
                        sample["value"] = float(val)
                        metrics.log_sample(source, desc, no_names, sample)
                elif line == "":
                    scan_mode = ""
            elif scan_mode == "memory-utilization":
                m = re.match(r'(\d+):(\d+):(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+\.\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+\.\d+)\s+(\d+)\s+(\d+)\s+(\d+)$', line)
                if m:
                    hms_ms = get_hms_ms(m.group(1), m.group(2), m.group(3))
                    if prev_hms_ms is not None and prev_hms_ms > hms_ms:
                        ymd_timestamp_ms = advance_ymd(ymd_timestamp_ms)
                    sample = {"end": ymd_timestamp_ms + hms_ms}
                    for metric_type, val in [
                        ("Memory-Free-KB", m.group(4)),
                        ("Memory-Available-KB", m.group(5)),
                        ("Memory-Used-KB", m.group(6)),
                        ("Memory-Used-Percent", m.group(7)),
                        ("Memory-Buffers-KB", m.group(8)),
                        ("Memory-Cached-KB", m.group(9)),
                        ("Memory-Commit-KB", m.group(10)),
                        ("Memory-Commit-Percent", m.group(11)),
                        ("Memory-Active-KB", m.group(12)),
                        ("Memory-Inactive-KB", m.group(13)),
                        ("Memory-Dirty-KB", m.group(14)),
                    ]:
                        desc["type"] = metric_type
                        sample["value"] = float(val)
                        metrics.log_sample(source, desc, no_names, sample)
                elif line == "":
                    scan_mode = ""

        elif source == "sar-io":
            if re.search(r'%sio-10\s+%sio-60\s+%sio-300\s+%sio\s+%fio-10\s+%fio-60\s+%fio-300\s+%fio$', line):
                scan_mode = "io-starved"
            elif scan_mode == "io-starved":
                m = re.match(r'(\d+):(\d+):(\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)$', line)
                if m:
                    hms_ms = get_hms_ms(m.group(1), m.group(2), m.group(3))
                    if prev_hms_ms is not None and prev_hms_ms > hms_ms:
                        ymd_timestamp_ms = advance_ymd(ymd_timestamp_ms)
                    sample = {"end": ymd_timestamp_ms + hms_ms}
                    for tw, val in [("010s", m.group(4)), ("060s", m.group(5)), ("300s", m.group(6)), ("last_interval", m.group(7))]:
                        desc["type"] = f"%-Time-Tasks-Lost-Waiting-on-IO-{tw}"
                        sample["value"] = float(val)
                        metrics.log_sample(source, desc, no_names, sample)
                    for tw, val in [("010s", m.group(8)), ("060s", m.group(9)), ("300s", m.group(10)), ("last_interval", m.group(11))]:
                        desc["type"] = f"%-Time-Tasks-Stalled-Waiting-on-IO-{tw}"
                        sample["value"] = float(val)
                        metrics.log_sample(source, desc, no_names, sample)
                elif line == "":
                    scan_mode = ""

        elif source == "sar-tasks":
            if re.search(r'proc/s\s+cswch/s$', line):
                scan_mode = "task"
            elif scan_mode == "task":
                m = re.match(r'(\d+):(\d+):(\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)$', line)
                if m:
                    hms_ms = get_hms_ms(m.group(1), m.group(2), m.group(3))
                    if prev_hms_ms is not None and prev_hms_ms > hms_ms:
                        ymd_timestamp_ms = advance_ymd(ymd_timestamp_ms)
                    sample = {"end": ymd_timestamp_ms + hms_ms}
                    desc["type"] = "Processes-created-sec"
                    sample["value"] = float(m.group(4))
                    metrics.log_sample(source, desc, no_names, sample)
                    desc["type"] = "Context-switches-sec"
                    sample["value"] = float(m.group(5))
                    metrics.log_sample(source, desc, no_names, sample)
                elif line == "":
                    scan_mode = ""

        elif source == "sar-scheduler":
            if re.search(r'%scpu-10\s+%scpu-60\s+%scpu-300\s+%scpu$', line):
                scan_mode = "cpu-starved"
            elif re.search(r'runq-sz\s+plist-sz\s+ldavg-1\s+ldavg-5\s+ldavg-15\s+blocked$', line):
                scan_mode = "task-lists"
            elif scan_mode == "cpu-starved":
                m = re.match(r'(\d+):(\d+):(\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)$', line)
                if m:
                    hms_ms = get_hms_ms(m.group(1), m.group(2), m.group(3))
                    if prev_hms_ms is not None and prev_hms_ms > hms_ms:
                        ymd_timestamp_ms = advance_ymd(ymd_timestamp_ms)
                    sample = {"end": ymd_timestamp_ms + hms_ms}
                    for tw, val in [("010s", m.group(4)), ("060s", m.group(5)), ("300s", m.group(6)), ("last_interval", m.group(7))]:
                        desc["type"] = f"%-Time-Tasks-CPU-Starved-{tw}"
                        sample["value"] = float(val)
                        metrics.log_sample(source, desc, no_names, sample)
                elif line == "":
                    scan_mode = ""
            elif scan_mode == "task-lists":
                m = re.match(r'(\d+):(\d+):(\d+)\s+(\d+)\s+(\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+)$', line)
                if m:
                    hms_ms = get_hms_ms(m.group(1), m.group(2), m.group(3))
                    if prev_hms_ms is not None and prev_hms_ms > hms_ms:
                        ymd_timestamp_ms = advance_ymd(ymd_timestamp_ms)
                    sample = {"end": ymd_timestamp_ms + hms_ms}
                    desc["type"] = "Run-Queue-Length"
                    sample["value"] = float(m.group(4))
                    metrics.log_sample(source, desc, no_names, sample)
                    desc["type"] = "Process-List-Size"
                    sample["value"] = float(m.group(5))
                    metrics.log_sample(source, desc, no_names, sample)
                    for tw, val in [("01m", m.group(6)), ("05m", m.group(7)), ("15m", m.group(8))]:
                        desc["type"] = f"Load-Average-{tw}"
                        sample["value"] = float(val)
                        metrics.log_sample(source, desc, no_names, sample)
                    desc["type"] = "IO-Blocked-Tasks"
                    sample["value"] = float(m.group(9))
                    metrics.log_sample(source, desc, no_names, sample)
                elif line == "":
                    scan_mode = ""

        elif source == "sar-net":
            if re.search(r'IFACE\s+rxpck/s\s+txpck/s\s+rxkB/s\s+txkB/s\s+rxcmp/s\s+txcmp/s\s+rxmcst/s\s+%ifutil$', line):
                scan_mode = "net"
            elif scan_mode == "net":
                m = re.match(r'(\d+):(\d+):(\d+)\s+(\S+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)', line)
                if m:
                    hms_ms = get_hms_ms(m.group(1), m.group(2), m.group(3))
                    if prev_hms_ms is not None and prev_hms_ms > hms_ms:
                        ymd_timestamp_ms = advance_ymd(ymd_timestamp_ms)
                    sample = {"end": ymd_timestamp_ms + hms_ms}
                    dev = m.group(4)
                    rxkB = float(m.group(7))
                    txkB = float(m.group(8))
                    rxpack = float(m.group(5))
                    txpack = float(m.group(6))
                    desc["type"] = "L2-Gbps"
                    for direction, kB in [("rx", rxkB), ("tx", txkB)]:
                        names = {"dev": dev, "direction": direction, "type": get_netdev_type(dev, netdev_types)}
                        sample["value"] = kB / 1000000 * 8
                        metrics.log_sample(source, desc, names, sample)
                    desc["type"] = "packets-sec"
                    for direction, pkt in [("rx", rxpack), ("tx", txpack)]:
                        names = {"dev": dev, "direction": direction, "type": get_netdev_type(dev, netdev_types)}
                        sample["value"] = pkt
                        metrics.log_sample(source, desc, names, sample)
                elif line == "":
                    scan_mode = ""

            if re.search(r'IFACE\s+rxerr/s\s+txerr/s\s+coll/s\s+rxdrop/s\s+txdrop/s\s+txcarr/s\s+rxfram/s\s+rxfifo/s\s+txfifo/s$', line):
                scan_mode = "net-error"
            elif scan_mode == "net-error":
                m = re.match(r'(\d+):(\d+):(\d+)\s+(\S+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)', line)
                if m:
                    hms_ms = get_hms_ms(m.group(1), m.group(2), m.group(3))
                    if prev_hms_ms is not None and prev_hms_ms > hms_ms:
                        ymd_timestamp_ms = advance_ymd(ymd_timestamp_ms)
                    sample = {"end": ymd_timestamp_ms + hms_ms}
                    dev = m.group(4)
                    desc["type"] = "errors-sec"
                    errors = {
                        "tx": {"collision": float(m.group(7)), "drop": float(m.group(9)), "carrier": float(m.group(10)), "fifo-overrun": float(m.group(13))},
                        "rx": {"drop": float(m.group(8)), "frame-alignment": float(m.group(11)), "fifo-overrun": float(m.group(12))},
                    }
                    for direction in errors:
                        for flavor in errors[direction]:
                            sample["value"] = errors[direction][flavor]
                            names = {"dev": dev, "direction": direction, "type": get_netdev_type(dev, netdev_types), "error": flavor}
                            metrics.log_sample(source, desc, names, sample)
                elif line == "":
                    scan_mode = ""

        prev_hms_ms = hms_ms

    fh.close()
    metrics.finish_samples()
    print(f"Post-processing for {source} complete")


def process_mpstat(fork_idx, num_forks, log_file, cpu_topo):
    print(f"Post-processing for mpstat-{fork_idx} started")
    metrics = CDMMetrics()
    ymd_timestamp_ms = None
    hms_ms = None
    prev_hms_ms = None
    desc = {"class": "throughput", "source": "mpstat"}
    sample = {}

    try:
        fh, _ = open_read_text_file(log_file)
    except FileNotFoundError:
        print(f"ERROR: could not open {log_file}")
        return

    for line in fh:
        line = line.rstrip("\n")

        m = re.search(r'"date": "(\d+-\d+-\d+)"', line)
        if m:
            ymd_timestamp_ms = ymd_to_epoch_ms(m.group(1))
            continue

        m = re.search(r'"timestamp": "(\d+):(\d+):(\d+)"', line)
        if m:
            hms_ms = get_hms_ms(m.group(1), m.group(2), m.group(3))
            if prev_hms_ms is not None and prev_hms_ms > hms_ms:
                ymd_timestamp_ms = advance_ymd(ymd_timestamp_ms)
            sample["end"] = ymd_timestamp_ms + hms_ms
            prev_hms_ms = hms_ms
            continue

        m = re.match(r'\s*(\{"cpu": "\d+".*\}),?$', line)
        if m:
            try:
                cpu_entry = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            cpu_num = int(cpu_entry["cpu"])
            if cpu_num % num_forks != fork_idx:
                continue
            package, die, core, thread = get_cpu_topology(cpu_num, cpu_topo)
            for cpu_type in cpu_entry:
                if cpu_type == "cpu":
                    continue
                names = {"package": package, "die": die, "core": core, "thread": thread, "num": cpu_num, "type": cpu_type}
                if cpu_type in ("idle", "iowait", "steal"):
                    desc["type"] = "NonBusy-CPU"
                else:
                    desc["type"] = "Busy-CPU"
                sample["value"] = cpu_entry[cpu_type] / 100
                metrics.log_sample(f"mpstat-{fork_idx}", desc, names, sample)

        prev_hms_ms = hms_ms

    fh.close()
    metrics.finish_samples()
    print(f"Post-processing for mpstat-{fork_idx} complete")


def process_iostat(log_file):
    print("Post-processing for iostat started")
    metrics = CDMMetrics()

    try:
        fh, _ = open_read_text_file(log_file)
    except FileNotFoundError:
        print(f"ERROR: could not open {log_file}")
        return

    time_ms = None
    from datetime import datetime, timezone

    for line in fh:
        line = line.rstrip("\n")
        m = re.search(r'\s*"timestamp"\:\s*"(.*?)"', line)
        if m:
            dt = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S%z")
            time_ms = int(dt.timestamp() * 1000)
            continue

        m = re.search(r'\s+({.*disk_device.*})', line)
        if m:
            try:
                io_sample = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            desc = {"source": "iostat"}
            for field_name in io_sample:
                if field_name == "disk_device":
                    continue
                sample = {"value": io_sample[field_name], "end": time_ms}
                names = {"dev": io_sample["disk_device"]}

                rate_m = re.match(r'(.*)(\/s|util)$', field_name)
                if rate_m:
                    desc["class"] = "throughput"
                    oper = rate_m.group(1)
                    cmd_m = re.match(r'^(w|r|d|f)(.*)$', oper)
                    if cmd_m:
                        cmd_map = {"r": "read", "w": "write", "d": "discard", "f": "flush"}
                        type_map = {"": "operations-sec", "rqm": "operations-merged-sec", "kB": "kB-sec", "qm": "request-merges-sec"}
                        names["cmd"] = cmd_map.get(cmd_m.group(1), cmd_m.group(1))
                        desc["type"] = type_map.get(cmd_m.group(2), cmd_m.group(2))
                    elif field_name == "util":
                        desc["type"] = "percent-utilization"
                else:
                    desc["class"] = "count"
                    cmd_m = re.match(r'^(w|r|d|f)(.+)$', field_name)
                    if cmd_m:
                        cmd_map = {"r": "read", "w": "write", "d": "discard", "f": "flush"}
                        type_map = {"rqm": "percent-merged", "_await": "avg-service-time-ms", "areq-sz": "avg-req-size-kB"}
                        names["cmd"] = cmd_map.get(cmd_m.group(1), cmd_m.group(1))
                        desc["type"] = type_map.get(cmd_m.group(2), cmd_m.group(2))
                    elif field_name == "aqu-sz":
                        desc["type"] = "avg-queue-length"

                if "type" in desc and time_ms is not None and sample["value"] is not None:
                    metrics.log_sample("iostat", desc, names, sample)

    fh.close()
    metrics.finish_samples()
    print("Post-processing for iostat complete")


def process_pidstat(log_file):
    print("Post-processing for pidstat started")
    metrics = CDMMetrics()
    skip_zero_pids = True
    data_re = re.compile(
        r'^(\d+):(\d+):(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(.+?)\s*$'
    )

    active_pids = set()
    if skip_zero_pids:
        try:
            fh1, _ = open_read_text_file(log_file)
            for line in fh1:
                m = data_re.match(line.rstrip("\n"))
                if m:
                    pid = m.group(5)
                    if any(float(m.group(i)) > 0 for i in (6, 7, 8, 9, 10)):
                        active_pids.add(pid)
            fh1.close()
            print(f"pidstat pass 1: found {len(active_pids)} active PIDs out of total")
        except FileNotFoundError:
            print(f"ERROR: could not open {log_file} for pass 1")
            return

    try:
        fh, _ = open_read_text_file(log_file)
    except FileNotFoundError:
        print(f"ERROR: could not open {log_file}")
        return

    ymd_timestamp_ms = None
    prev_hms_ms = None

    for line in fh:
        line = line.rstrip("\n")
        m = re.match(r'^Linux\s\S+\s\S+\s+(\d+-\d+-\d+)\s+\S+\s+\S+', line)
        if m:
            ymd_timestamp_ms = ymd_to_epoch_ms(m.group(1))
            continue

        m = data_re.match(line)
        if m:
            pid = m.group(5)
            if skip_zero_pids and pid not in active_pids:
                continue

            hms_ms = get_hms_ms(m.group(1), m.group(2), m.group(3))
            if prev_hms_ms is not None and hms_ms < prev_hms_ms:
                ymd_timestamp_ms = advance_ymd(ymd_timestamp_ms)
            prev_hms_ms = hms_ms
            time_ms = ymd_timestamp_ms + hms_ms

            command = m.group(12)
            desc = {"source": "pidstat", "class": "throughput"}
            fields = {"usr": float(m.group(6)), "system": float(m.group(7)), "guest": float(m.group(8)), "wait": float(m.group(9))}

            for field_name, val in fields.items():
                names = {"cmd": command, "pid": pid, "type": field_name}
                desc["type"] = "NonBusy-CPU" if field_name == "wait" else "Busy-CPU"
                sample = {"end": time_ms, "value": val}
                metrics.log_sample("pidstat", desc, names, sample)

    fh.close()
    metrics.finish_samples()
    print("Post-processing for pidstat complete")


def main():
    print("sysstat-post-process")

    files = sorted(os.listdir("."))
    print(f"files to process:\n {' '.join(files)}")
    threads = []

    # SAR processing
    sar_files = [f for f in files if re.match(r'^sar-stdout\.txt(\.xz)?$', f)]
    if len(sar_files) > 1:
        print(f"ERROR: there should never be more than one sar file: {sar_files}")
    elif len(sar_files) == 0:
        print("ERROR: there is no sar file")
    else:
        log_file = sar_files[0]
        print(f"Found {log_file}")
        for source in ("sar-mem", "sar-scheduler", "sar-io", "sar-tasks", "sar-net"):
            t = threading.Thread(target=process_sar, args=(source, log_file))
            t.start()
            threads.append(t)
        print(f"Waiting for {len(threads)} sar post-processing threads")
        for t in threads:
            t.join()
        threads = []

    # mpstat processing
    mpstat_files = [f for f in files if re.match(r'^mpstat\.json(\.xz)?$', f)]
    if len(mpstat_files) > 1:
        print(f"ERROR: there should never be more than one mpstat file: {mpstat_files}")
    elif len(mpstat_files) == 0:
        print("ERROR: there is no mpstat file")
    else:
        log_file = mpstat_files[0]
        print(f"Found {log_file}")
        cpu_topo = build_cpu_topology("sys/devices/system/cpu")
        num_forks = 8
        for i in range(num_forks):
            t = threading.Thread(target=process_mpstat, args=(i, num_forks, log_file, cpu_topo))
            t.start()
            threads.append(t)
        print(f"Waiting for {len(threads)} mpstat post-processing threads")
        for t in threads:
            t.join()
        threads = []

    # iostat processing (sequential)
    iostat_files = [f for f in files if re.match(r'^iostat\.json(\.xz)?$', f)]
    if len(iostat_files) > 1:
        print(f"ERROR: there should never be more than one iostat file: {iostat_files}")
    elif len(iostat_files) == 0:
        print("ERROR: there is no iostat file")
    else:
        process_iostat(iostat_files[0])

    # pidstat processing (sequential)
    pidstat_files = [f for f in files if re.match(r'^pidstat-stdout\.txt(\.xz)?$', f)]
    if len(pidstat_files) > 1:
        print(f"ERROR: there should never be more than one pidstat file: {pidstat_files}")
    elif len(pidstat_files) == 0:
        print("WARNING: there is no pidstat file")
    else:
        process_pidstat(pidstat_files[0])

    print("All sysstat post-processing is complete")


if __name__ == "__main__":
    main()
