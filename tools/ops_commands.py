# -*- coding: utf-8 -*-
"""离线 Linux 运维命令库、模糊检索与安全参数化生成。"""
from difflib import SequenceMatcher
import json
import os
import re
import shlex

from config import CUSTOM_OPS_FILE, ensure_config_dir


CATEGORIES = {
    'all': ('全部命令', 'All commands'),
    'logs': ('日志查询', 'Logs'),
    'status': ('服务器运行状态', 'Server status'),
    'process': ('进程诊断', 'Processes'),
    'network': ('网络排查', 'Network'),
    'disk': ('磁盘与文件只读检查', 'Disk & read-only files'),
    'service': ('服务管理', 'Services'),
    'container': ('容器与 Kubernetes', 'Containers & Kubernetes'),
    'security': ('账号与安全审计', 'Accounts & security'),
    'schedule': ('定时任务', 'Scheduled tasks'),
    'software': ('软件与证书', 'Software & certificates'),
}

RISK_LABELS = {
    'safe': ('只读安全', 'Read-only'),
    'caution': ('谨慎使用', 'Use with care'),
    'danger': ('修改系统 · 执行前强确认', 'Changes state · confirmation required'),
}


def _param(name, zh, en, default='', kind='text', placeholder=''):
    return {
        'name': name, 'label_zh': zh, 'label_en': en,
        'default': default, 'kind': kind, 'placeholder': placeholder,
    }


def _cmd(command, category, title_zh, description_zh, title_en, description_en,
         template=None, params=None, risk='safe', workflow=None, tags=''):
    return {
        'command': command, 'category': category,
        'title_zh': title_zh, 'description_zh': description_zh,
        'title_en': title_en, 'description_en': description_en,
        'template': template or command, 'params': params or [],
        'risk': risk, 'workflow': workflow, 'tags': tags,
    }


COMMANDS = [
    # 日志查询与截取
    _cmd('tail -f /path/app.log', 'logs', '实时跟踪日志', '持续显示日志末尾新增内容；按 Ctrl+C 停止，不修改原日志。', 'Follow a log', 'Continuously print newly appended lines; Ctrl+C stops it.',
         'tail -n {lines} -f {log_path}', [_param('lines', '初始行数', 'Initial lines', '200', 'int'), _param('log_path', '日志路径', 'Log path', '/var/log/app/app.log')]),
    _cmd('tail -n 500 app.log', 'logs', '查看日志末尾', '读取日志最后若干行，适合先快速确认最近报错。', 'Read last log lines', 'Read the latest lines from a log file.',
         'tail -n {lines} {log_path}', [_param('lines', '行数', 'Lines', '500', 'int'), _param('log_path', '日志路径', 'Log path', '/var/log/app/app.log')]),
    _cmd('head -n 100 app.log', 'logs', '查看日志开头', '读取日志前若干行，常用于检查启动信息和文件格式。', 'Read first log lines', 'Read the first lines from a log file.',
         'head -n {lines} {log_path}', [_param('lines', '行数', 'Lines', '100', 'int'), _param('log_path', '日志路径', 'Log path', '/var/log/app/app.log')]),
    _cmd("grep -n -C 20 'ERROR' app.log", 'logs', '按关键词定位并显示上下文', '查找关键词并显示命中行号及前后上下文，不区分大小写。', 'Locate keyword with context', 'Find a keyword with line numbers and surrounding context.',
         'grep -n -i -C {context} -- {keyword} {log_path}', [_param('context', '上下文行数', 'Context lines', '20', 'int'), _param('keyword', '日志关键词', 'Keyword', 'ERROR'), _param('log_path', '日志路径', 'Log path', '/var/log/app/app.log')]),
    _cmd("grep A | grep B | grep C", 'logs', '多关键字 AND 截取（同时包含）', '主关键字带上下文，再依次过滤必须同时包含的其它关键字（XX 和 XX 和 XX）。适合「单号 + ERROR + 接口名」类排查。', 'AND multi-keyword extract', 'Primary keyword with context, then filter lines that also contain other keywords.',
         workflow='log_and_keywords', risk='safe', tags='日志 多关键字 AND 同时包含',
         params=[
             _param('keyword', '主关键字', 'Primary keyword', 'ERROR'),
             _param('also_1', '也包含 1', 'Also contain 1', ''),
             _param('also_2', '也包含 2', 'Also contain 2', ''),
             _param('also_3', '也包含 3', 'Also contain 3', ''),
             _param('context', '上下文行数', 'Context lines', '20', 'int'),
             _param('log_path', '日志路径', 'Log path', '/var/log/app/app.log'),
         ]),
    _cmd("grep -nE 'A|B|C'", 'logs', '多关键字 OR 截取（任一包含）', '匹配多个关键字中的任意一个，适合一批错误码或一批接口名。', 'OR multi-keyword extract', 'Match any of several keywords.',
         'grep -n -i -E -C {context} -- {pattern} {log_path}', [
             _param('pattern', '关键字模式(用|分隔)', 'Pattern A|B|C', 'ERROR|Exception|Timeout'),
             _param('context', '上下文行数', 'Context lines', '10', 'int'),
             _param('log_path', '日志路径', 'Log path', '/var/log/app/app.log'),
         ], tags='日志 OR 多关键字'),
    _cmd('日志定位并截取到新文件', 'logs', '日志路径不确定：先定位，再截取', '第一步在指定目录寻找候选日志；确认准确路径后，第二步把关键词及上下文截取到另一个文件。不会改动原日志。注意：此模板会在服务器写输出文件；PengTools「日志排查」模块默认流式导出、不写远端文件。', 'Locate then extract a log', 'Find candidate logs first, then extract keyword context into another file without changing the source.',
         params=[_param('search_root', '日志搜索目录', 'Search root', '/var/log'), _param('file_pattern', '文件名模式', 'Filename pattern', '*.log'), _param('days', '最近修改天数', 'Modified within days', '7', 'int'), _param('log_path', '确认后的日志路径（可先留空）', 'Confirmed log path (optional first)', ''), _param('keyword', '截取关键词', 'Extract keyword', 'ERROR'), _param('context', '上下文行数', 'Context lines', '20', 'int'), _param('output_file', '输出文件', 'Output file', '/tmp/log_extract.txt')], workflow='log_extract', risk='caution', tags='日志截取 日志定位 find grep output'),
    _cmd("find /var/log -type f -name '*.log'", 'logs', '查找日志文件位置', '按文件名模式递归查找日志，并限制展示数量；只进行读取和目录遍历。', 'Find log files', 'Recursively find log files by filename pattern.',
         'find {search_root} -type f -name {file_pattern} 2>/dev/null | head -n {limit}', [_param('search_root', '搜索目录', 'Search root', '/var/log'), _param('file_pattern', '文件名模式', 'Filename pattern', '*.log'), _param('limit', '最多显示', 'Maximum results', '100', 'int')]),
    _cmd('find /var/log -type f -mmin -60', 'logs', '查找最近更新的日志', '找出最近若干分钟内修改过的日志，适合确认当前服务实际写入哪个文件。', 'Find recently updated logs', 'Find logs modified within the recent number of minutes.',
         'find {search_root} -type f -mmin -{minutes} 2>/dev/null | head -n {limit}', [_param('search_root', '搜索目录', 'Search root', '/var/log'), _param('minutes', '最近分钟数', 'Recent minutes', '60', 'int'), _param('limit', '最多显示', 'Maximum results', '100', 'int')]),
    _cmd("sed -n '100,200p' app.log", 'logs', '按行号截取日志区间', '只读取并显示指定起止行，适合已知报错行号时精确查看。', 'Read a log line range', 'Print an exact inclusive line range.',
         "sed -n '{start},{end}p' {log_path}", [_param('start', '起始行', 'Start line', '100', 'int'), _param('end', '结束行', 'End line', '200', 'int'), _param('log_path', '日志路径', 'Log path', '/var/log/app/app.log')]),
    _cmd("awk 'NR>=100 && NR<=200' app.log", 'logs', '使用 awk 按行截取', '按行号范围读取大日志，可和其他只读过滤命令组合。', 'Read lines with awk', 'Read a line range from a large log with awk.',
         "awk 'NR>={start} && NR<={end}' {log_path}", [_param('start', '起始行', 'Start line', '100', 'int'), _param('end', '结束行', 'End line', '200', 'int'), _param('log_path', '日志路径', 'Log path', '/var/log/app/app.log')]),
    _cmd("zgrep -n -i 'ERROR' app.log.gz", 'logs', '查询 gzip 压缩日志', '直接搜索 .gz 压缩日志，不需要先解压文件。', 'Search compressed logs', 'Search a gzip-compressed log without extracting it.',
         'zgrep -n -i -C {context} -- {keyword} {log_path}', [_param('context', '上下文行数', 'Context lines', '10', 'int'), _param('keyword', '关键词', 'Keyword', 'ERROR'), _param('log_path', '压缩日志路径', 'Compressed log', '/var/log/app/app.log.gz')]),
    _cmd('journalctl -u nginx --since today', 'logs', '查询 systemd 服务日志', '按服务名和起始时间读取 journal 日志；不会改变服务状态。', 'Read systemd service logs', 'Read journal entries for one service since a chosen time.',
         'journalctl -u {service} --since {since} --no-pager', [_param('service', '服务名', 'Service', 'nginx'), _param('since', '起始时间', 'Since', 'today')]),
    _cmd('journalctl -u nginx -f', 'logs', '实时跟踪服务日志', '持续跟踪指定 systemd 服务的最新日志，Ctrl+C 停止。', 'Follow service journal', 'Follow new journal entries for a systemd service.',
         'journalctl -u {service} -n {lines} -f', [_param('service', '服务名', 'Service', 'nginx'), _param('lines', '初始行数', 'Initial lines', '200', 'int')]),
    _cmd('journalctl -p err -b', 'logs', '查看本次启动错误日志', '显示本次系统启动以来 error 及以上级别的 journal 记录。', 'Boot error journal', 'Show error-or-higher journal entries from the current boot.'),
    _cmd('dmesg -T | tail -n 200', 'logs', '查看内核近期日志', '读取内核环形缓冲区并转换时间，常用于磁盘、网卡、OOM 和驱动故障。', 'Recent kernel messages', 'Read recent timestamped kernel messages for hardware and OOM issues.'),
    _cmd("grep -E 'Out of memory|Killed process' /var/log/messages", 'logs', '定位 OOM 记录', '在系统日志中定位内存不足和内核杀进程记录。', 'Locate OOM records', 'Find out-of-memory and killed-process records.',
         'grep -n -i -E {keyword} {log_path}', [_param('keyword', '匹配规则', 'Pattern', 'Out of memory|Killed process'), _param('log_path', '系统日志路径', 'System log', '/var/log/messages')]),

    # 服务器状态与性能
    _cmd('uptime', 'status', '运行时长与负载', '显示系统运行时长、登录用户数以及 1/5/15 分钟平均负载。', 'Uptime and load', 'Show uptime and 1/5/15-minute load averages.'),
    _cmd('top', 'status', '实时资源概览', '实时查看 CPU、内存、负载与进程；按 q 退出。', 'Live resource overview', 'Interactively view CPU, memory, load and processes.'),
    _cmd('free -h', 'status', '内存使用情况', '以易读单位显示物理内存、缓存和 swap 使用情况。', 'Memory usage', 'Show RAM, cache and swap in human-readable units.'),
    _cmd('vmstat 1 10', 'status', 'CPU、内存与调度采样', '按秒采样运行队列、内存、交换、IO 和 CPU，首行是启动以来平均值。', 'VM statistics', 'Sample run queue, memory, swap, IO and CPU.',
         'vmstat {interval} {count}', [_param('interval', '采样间隔秒', 'Interval seconds', '1', 'int'), _param('count', '采样次数', 'Sample count', '10', 'int')]),
    _cmd('mpstat -P ALL 1 5', 'status', '逐 CPU 使用率', '查看每个逻辑 CPU 的利用率与 iowait，需要 sysstat。', 'Per-CPU usage', 'Show utilization and iowait for every logical CPU.'),
    _cmd('iostat -xz 1 5', 'status', '磁盘 IO 延迟与利用率', '显示块设备吞吐、等待时间、队列和利用率，需要 sysstat。', 'Disk IO performance', 'Show device throughput, latency, queue and utilization.'),
    _cmd('sar -n DEV 1 5', 'status', '网卡吞吐采样', '按网卡采样收发包和吞吐量，需要 sysstat。', 'Network throughput sample', 'Sample packet and throughput counters per interface.'),
    _cmd('pidstat 1 5', 'status', '逐进程资源采样', '按进程采样 CPU 使用率和上下文切换，需要 sysstat。', 'Per-process statistics', 'Sample CPU and context-switch usage by process.'),
    _cmd('cat /proc/loadavg', 'status', '读取系统负载原始值', '显示负载、可运行任务数和最近创建的 PID。', 'Raw load values', 'Show load averages, runnable tasks and latest PID.'),
    _cmd('cat /proc/meminfo', 'status', '读取完整内存指标', '读取内核暴露的详细内存、缓存、页表和 HugePage 指标。', 'Detailed memory metrics', 'Read detailed kernel memory counters.'),
    _cmd('lscpu', 'status', 'CPU 硬件信息', '显示 CPU 架构、核数、线程、NUMA 和虚拟化信息。', 'CPU information', 'Show CPU architecture, cores, threads, NUMA and virtualization.'),
    _cmd('uname -a', 'status', '内核与架构信息', '显示主机内核版本、架构和构建信息。', 'Kernel and architecture', 'Show kernel version, architecture and build details.'),

    # 进程诊断
    _cmd('ps -ef', 'process', '查看全部进程', '以完整格式列出所有进程、父进程、用户和启动命令。', 'List all processes', 'List every process with parent, owner and command.', tags='进程列表 process list'),
    _cmd('ps aux --sort=-%cpu | head', 'process', 'CPU 占用最高进程', '按 CPU 使用率倒序查看最忙的进程。', 'Top CPU processes', 'Sort processes by descending CPU usage.'),
    _cmd('ps aux --sort=-%mem | head', 'process', '内存占用最高进程', '按内存占用比例倒序查看进程。', 'Top memory processes', 'Sort processes by descending memory usage.'),
    _cmd('pgrep -af java', 'process', '按名称查进程及命令行', '按关键词匹配进程，并显示 PID 和完整命令行。', 'Find processes by name', 'Match processes and show PID plus full command line.',
         'pgrep -af {keyword}', [_param('keyword', '进程关键词', 'Process keyword', 'java')]),
    _cmd('pstree -ap', 'process', '查看进程父子树', '以树形展示父子进程关系、PID 和启动参数。', 'Process tree', 'Show parent-child process relationships and arguments.'),
    _cmd('lsof -p 1234', 'process', '查看进程打开的文件', '列出指定 PID 打开的文件、网络连接和动态库。', 'Files opened by a process', 'List files, sockets and libraries opened by a PID.',
         'lsof -p {pid}', [_param('pid', '进程 PID', 'Process PID', '1234', 'int')]),
    _cmd('cat /proc/1234/status', 'process', '查看进程状态详情', '读取进程状态、内存、线程、能力和上下文切换信息。', 'Process status details', 'Read process state, memory, threads and context switches.',
         'cat /proc/{pid}/status', [_param('pid', '进程 PID', 'Process PID', '1234', 'int')]),
    _cmd('strace -p 1234 -f -tt', 'process', '跟踪进程系统调用', '附加到运行中的进程观察系统调用，可能带来性能影响，完成后 Ctrl+C 退出。', 'Trace system calls', 'Attach to a process and trace syscalls; may affect performance.',
         'strace -p {pid} -f -tt -s {string_size}', [_param('pid', '进程 PID', 'Process PID', '1234', 'int'), _param('string_size', '字符串显示长度', 'String size', '256', 'int')], risk='caution'),
    _cmd('kill -15 1234', 'process', '优雅终止进程', '向进程发送 SIGTERM，会改变服务状态；必须先确认 PID、影响范围及自动拉起机制。', 'Terminate a process gracefully', 'Send SIGTERM; verify PID and service impact first.',
         'kill -15 {pid}', [_param('pid', '进程 PID', 'Process PID', '1234', 'int')], risk='danger'),
    _cmd('renice 5 -p 1234', 'process', '调整进程优先级', '修改运行进程的 nice 值，可能影响业务性能和调度。', 'Change process priority', 'Change a running process nice value and scheduling priority.',
         'renice {nice} -p {pid}', [_param('nice', 'Nice 值', 'Nice value', '5', 'int'), _param('pid', '进程 PID', 'Process PID', '1234', 'int')], risk='danger'),

    # 网络排查
    _cmd('ss -lntp', 'network', '查看 TCP 监听端口', '显示监听中的 TCP 端口、地址和关联进程。', 'TCP listening ports', 'Show listening TCP sockets and owning processes.'),
    _cmd('ss -antp', 'network', '查看全部 TCP 连接', '显示 TCP 连接状态、远端地址和关联进程。', 'All TCP connections', 'Show TCP states, remote addresses and processes.'),
    _cmd('ss -s', 'network', '网络连接统计摘要', '快速查看 TCP/UDP 连接数量和状态汇总。', 'Socket summary', 'Show summarized TCP and UDP socket counts.'),
    _cmd('lsof -i :8080', 'network', '定位端口占用进程', '按端口查找监听或连接该端口的进程。', 'Find process using a port', 'Find the process listening on or connected to a port.',
         'lsof -nP -i :{port}', [_param('port', '端口', 'Port', '8080', 'int')]),
    _cmd('ip addr show', 'network', '查看网卡和 IP', '显示所有网卡状态、IPv4/IPv6 地址和掩码。', 'Interfaces and IP addresses', 'Show interface state and IPv4/IPv6 addresses.'),
    _cmd('ip route show', 'network', '查看路由表', '显示默认网关和所有内核路由。', 'Routing table', 'Show the default gateway and kernel routes.'),
    _cmd('ping -c 4 10.0.0.1', 'network', '测试网络连通性', '发送有限数量 ICMP 请求，查看丢包率和延迟。', 'Test connectivity', 'Send a limited number of ICMP probes.',
         'ping -c {count} {host}', [_param('count', '次数', 'Count', '4', 'int'), _param('host', '主机/IP', 'Host/IP', '10.0.0.1')]),
    _cmd('traceroute 10.0.0.1', 'network', '追踪网络路径', '显示到目标主机经过的路由跳点和延迟。', 'Trace network path', 'Show route hops and latency to a host.',
         'traceroute {host}', [_param('host', '主机/IP', 'Host/IP', '10.0.0.1')]),
    _cmd('curl -I https://example.com', 'network', '查看 HTTP 响应头', '仅请求响应头，检查状态码、网关、缓存和重定向。', 'HTTP response headers', 'Request headers only to inspect status and redirects.',
         'curl -k -I --connect-timeout {timeout} {url}', [_param('timeout', '超时秒数', 'Timeout seconds', '10', 'int'), _param('url', 'URL', 'URL', 'https://example.com')], risk='caution'),
    _cmd('curl -v https://example.com', 'network', '诊断 HTTP/TLS 请求', '显示 DNS、连接、TLS 握手和请求响应头；可能包含敏感头信息，分享前脱敏。', 'Diagnose HTTP/TLS', 'Show DNS, connection, TLS and headers; redact secrets before sharing.',
         'curl -k -v --connect-timeout {timeout} {url}', [_param('timeout', '超时秒数', 'Timeout seconds', '10', 'int'), _param('url', 'URL', 'URL', 'https://example.com')], risk='caution'),
    _cmd('nc -vz 10.0.0.1 8080', 'network', '测试 TCP 端口', '尝试建立 TCP 连接，确认目标主机端口是否可达。', 'Test a TCP port', 'Attempt a TCP connection to test reachability.',
         'nc -vz -w {timeout} {host} {port}', [_param('timeout', '超时秒数', 'Timeout seconds', '5', 'int'), _param('host', '主机/IP', 'Host/IP', '10.0.0.1'), _param('port', '端口', 'Port', '8080', 'int')]),
    _cmd('dig example.com', 'network', '查询 DNS 解析', '显示 DNS 记录、响应服务器和耗时。', 'DNS lookup', 'Show DNS records, responding server and timing.',
         'dig {domain}', [_param('domain', '域名', 'Domain', 'example.com')]),
    _cmd('cat /etc/resolv.conf', 'network', '查看 DNS 配置', '只读查看当前 nameserver 和搜索域配置。', 'DNS configuration', 'Read configured name servers and search domains.'),
    _cmd('ethtool eth0', 'network', '查看网卡链路状态', '显示网卡速率、双工、自协商和 Link detected 状态。', 'Interface link state', 'Show speed, duplex, negotiation and link state.',
         'ethtool {interface}', [_param('interface', '网卡名', 'Interface', 'eth0')]),

    # 磁盘与文件只读检查
    _cmd('df -hT', 'disk', '文件系统空间使用率', '显示挂载点、文件系统类型、容量和使用率。', 'Filesystem usage', 'Show filesystem type, capacity and usage.'),
    _cmd('df -ih', 'disk', 'inode 使用率', '检查 inode 是否耗尽；磁盘有空间但无法创建文件时重点查看。', 'Inode usage', 'Check inode exhaustion when file creation fails despite free space.'),
    _cmd('du -xh --max-depth=1 /var | sort -h', 'disk', '统计目录占用', '统计指定目录下一层的磁盘占用并按大小排序，可能对大目录产生 IO。', 'Directory sizes', 'Summarize one directory level and sort by size; may cause IO.',
         'du -xh --max-depth={depth} {path} 2>/dev/null | sort -h | tail -n {limit}', [_param('depth', '目录深度', 'Depth', '1', 'int'), _param('path', '目录路径', 'Directory', '/var'), _param('limit', '显示数量', 'Result count', '30', 'int')], risk='caution'),
    _cmd('lsblk -f', 'disk', '查看块设备和文件系统', '显示磁盘、分区、文件系统、UUID 和挂载点。', 'Block devices', 'Show disks, partitions, filesystems, UUIDs and mount points.'),
    _cmd('findmnt', 'disk', '查看挂载关系', '以树形显示设备、挂载点、文件系统和挂载参数。', 'Mounted filesystems', 'Show devices, mount points and options as a tree.'),
    _cmd('stat /path/file', 'disk', '查看文件元数据', '显示文件大小、权限、inode 以及访问、修改和变更时间。', 'File metadata', 'Show size, permissions, inode and timestamps.',
         'stat {path}', [_param('path', '文件路径', 'File path', '/var/log/messages')]),
    _cmd('file /path/file', 'disk', '识别文件类型', '根据文件内容识别文本、二进制、压缩包或编码类型。', 'Identify file type', 'Identify text, binary, archive and encoding types.',
         'file {path}', [_param('path', '文件路径', 'File path', '/path/to/file')]),
    _cmd('wc -l app.log', 'disk', '统计文件行数', '统计文本文件行数，不修改文件。', 'Count file lines', 'Count lines in a text file.',
         'wc -l {path}', [_param('path', '文件路径', 'File path', '/var/log/app/app.log')]),
    _cmd('sha256sum file', 'disk', '计算文件 SHA-256', '计算文件校验值，用于传输或发布包完整性核对。', 'SHA-256 checksum', 'Calculate a file checksum for integrity verification.',
         'sha256sum {path}', [_param('path', '文件路径', 'File path', '/path/to/file')]),
    _cmd('diff -u old.conf new.conf', 'disk', '比较两个文本文件', '以统一差异格式只读比较两个文件。', 'Compare text files', 'Compare two text files in unified diff format.',
         'diff -u {old_file} {new_file}', [_param('old_file', '原文件', 'Old file', '/path/old.conf'), _param('new_file', '新文件', 'New file', '/path/new.conf')]),
    _cmd('tar -tf archive.tar.gz', 'disk', '查看压缩包目录', '只列出 tar/tar.gz 内容，不解压和覆盖文件。', 'List tar archive', 'List archive contents without extracting.',
         'tar -tf {archive}', [_param('archive', '压缩包路径', 'Archive path', '/path/archive.tar.gz')]),
    _cmd('chmod 640 file', 'disk', '修改文件权限', '会修改文件权限；执行前确认目标、属主、服务读取需求及回滚值。', 'Change file permissions', 'Changes permissions; verify target, owner and rollback value.',
         'chmod {mode} {path}', [_param('mode', '权限值', 'Mode', '640'), _param('path', '文件路径', 'File path', '/path/to/file')], risk='danger'),
    _cmd('chown user:group file', 'disk', '修改文件属主', '会修改文件属主和属组，错误设置可能导致服务无法启动。', 'Change file ownership', 'Changes owner/group and may prevent a service from starting.',
         'chown {owner} {path}', [_param('owner', '属主:属组', 'Owner:group', 'app:app'), _param('path', '文件路径', 'File path', '/path/to/file')], risk='danger'),

    # 服务管理
    _cmd('systemctl status nginx', 'service', '查看服务状态', '显示 systemd 服务状态、PID、最近日志和退出原因。', 'Service status', 'Show service state, PID, recent logs and exit reason.',
         'systemctl status {service} --no-pager -l', [_param('service', '服务名', 'Service', 'nginx')]),
    _cmd('systemctl list-units --type=service', 'service', '列出已加载服务', '列出 systemd 已加载服务及当前状态。', 'List loaded services', 'List loaded systemd services and states.'),
    _cmd('systemctl list-units --failed', 'service', '查看失败的服务', '筛选 systemd 启动失败或运行失败的单元。', 'Failed services', 'List failed systemd units.'),
    _cmd('systemctl show nginx', 'service', '查看服务完整属性', '输出服务启动参数、依赖、资源和重启策略等全部属性。', 'Service properties', 'Show command, dependencies, resources and restart policy.',
         'systemctl show {service}', [_param('service', '服务名', 'Service', 'nginx')]),
    _cmd('systemctl restart nginx', 'service', '重启服务', '会中断并重新启动服务；必须确认业务窗口、实例范围和回滚方案。', 'Restart a service', 'Interrupts and restarts a service; confirm window and rollback.',
         'systemctl restart {service}', [_param('service', '服务名', 'Service', 'nginx')], risk='danger'),
    _cmd('systemctl reload nginx', 'service', '重载服务配置', '请求服务重新加载配置；仍可能影响连接，执行前先完成配置检查。', 'Reload service configuration', 'Reloads configuration and may affect connections.',
         'systemctl reload {service}', [_param('service', '服务名', 'Service', 'nginx')], risk='danger'),
    _cmd('systemctl start nginx', 'service', '启动服务', '会改变服务运行状态；确认端口、依赖和是否应由集群编排启动。', 'Start a service', 'Changes service state; verify dependencies and orchestration.',
         'systemctl start {service}', [_param('service', '服务名', 'Service', 'nginx')], risk='danger'),
    _cmd('systemctl stop nginx', 'service', '停止服务', '会直接停止服务并造成业务不可用；必须经过变更审批和影响确认。', 'Stop a service', 'Stops a service and may cause an outage; approval is required.',
         'systemctl stop {service}', [_param('service', '服务名', 'Service', 'nginx')], risk='danger'),

    # 容器与 Kubernetes
    _cmd('docker ps --no-trunc', 'container', '查看运行中的容器', '列出容器 ID、镜像、启动命令、状态、端口和名称。', 'Running containers', 'List container image, command, state, ports and name.'),
    _cmd('docker stats --no-stream', 'container', '容器资源快照', '查看各容器 CPU、内存、网络和块 IO 快照。', 'Container resource snapshot', 'Show a one-time CPU, memory, network and block IO snapshot.'),
    _cmd('docker logs --tail 200 -f app', 'container', '查看容器日志', '读取并跟踪指定容器日志，Ctrl+C 停止。', 'Container logs', 'Read and follow logs for a container.',
         'docker logs --tail {lines} -f {container}', [_param('lines', '初始行数', 'Initial lines', '200', 'int'), _param('container', '容器名/ID', 'Container name/ID', 'app')]),
    _cmd('docker inspect app', 'container', '查看容器完整配置', '读取容器网络、挂载、环境变量和启动配置；分享前注意敏感信息。', 'Inspect a container', 'Read networking, mounts, environment and startup config; redact secrets.',
         'docker inspect {container}', [_param('container', '容器名/ID', 'Container name/ID', 'app')], risk='caution'),
    _cmd('docker top app', 'container', '查看容器内进程', '显示指定容器中的进程列表。', 'Processes in a container', 'Show processes running inside a container.',
         'docker top {container}', [_param('container', '容器名/ID', 'Container name/ID', 'app')]),
    _cmd('docker restart app', 'container', '重启容器', '会中断容器业务；确认副本、流量摘除和数据持久化后再执行。', 'Restart a container', 'Interrupts a container; verify replicas, traffic and persistence.',
         'docker restart {container}', [_param('container', '容器名/ID', 'Container name/ID', 'app')], risk='danger'),
    _cmd('kubectl get pods -A -o wide', 'container', '查看全部 Pod', '跨命名空间列出 Pod、节点、IP、状态和重启次数。', 'All Kubernetes pods', 'List pods across namespaces with node, IP and restart count.'),
    _cmd('kubectl describe pod app -n default', 'container', '查看 Pod 事件与详情', '显示 Pod 调度、容器状态、探针、挂载和近期事件。', 'Describe a pod', 'Show scheduling, container state, probes, mounts and events.',
         'kubectl describe pod {pod} -n {namespace}', [_param('pod', 'Pod 名称', 'Pod name', 'app-xxxxx'), _param('namespace', '命名空间', 'Namespace', 'default')]),
    _cmd('kubectl logs app -n default --tail=200', 'container', '查看 Pod 日志', '读取 Pod 指定容器的最近日志，可选择跟踪。', 'Pod logs', 'Read recent logs from a pod container.',
         'kubectl logs {pod} -n {namespace} -c {container} --tail={lines}', [_param('pod', 'Pod 名称', 'Pod name', 'app-xxxxx'), _param('namespace', '命名空间', 'Namespace', 'default'), _param('container', '容器名', 'Container', 'app'), _param('lines', '行数', 'Lines', '200', 'int')]),
    _cmd('kubectl get events -A --sort-by=.lastTimestamp', 'container', '查看集群近期事件', '按时间排序查看调度、拉镜像、探针和资源异常。', 'Kubernetes events', 'Sort scheduling, image, probe and resource events by time.'),
    _cmd('kubectl top pods -A', 'container', '查看 Pod 资源使用', '显示 Pod CPU 和内存使用，需要 Metrics Server。', 'Pod resource usage', 'Show pod CPU and memory usage; Metrics Server required.'),
    _cmd('kubectl rollout restart deployment/app -n default', 'container', '滚动重启 Deployment', '会逐步替换 Pod；必须确认副本、PDB、健康检查和发布窗口。', 'Rollout restart a deployment', 'Replaces pods gradually; verify replicas, PDB and health checks.',
         'kubectl rollout restart deployment/{deployment} -n {namespace}', [_param('deployment', 'Deployment 名称', 'Deployment', 'app'), _param('namespace', '命名空间', 'Namespace', 'default')], risk='danger'),

    # 账号、安全和审计
    _cmd('who', 'security', '查看当前登录用户', '显示当前登录终端、来源和登录时间。', 'Logged-in users', 'Show current login terminals, origins and times.'),
    _cmd('w', 'security', '查看登录用户及活动', '显示登录用户、来源、空闲时间和正在执行的命令。', 'User sessions and activity', 'Show sessions, origins, idle time and current commands.'),
    _cmd('last -n 20', 'security', '查看近期登录历史', '读取 wtmp 中最近成功登录、重启和关机记录。', 'Recent login history', 'Read recent successful logins and reboot records.'),
    _cmd('lastb -n 20', 'security', '查看失败登录记录', '读取失败登录记录，通常需要 root 权限。', 'Failed login history', 'Read failed login attempts; usually requires root.', risk='caution'),
    _cmd('id app', 'security', '查看用户 UID 和组', '显示用户 UID、主组和附加组。', 'User identity and groups', 'Show UID, primary group and supplementary groups.',
         'id {user}', [_param('user', '用户名', 'User', 'app')]),
    _cmd('getent passwd app', 'security', '查询账号目录信息', '通过 NSS 查询本地或目录服务中的账号信息。', 'Account directory lookup', 'Query local or directory-service account data through NSS.',
         'getent passwd {user}', [_param('user', '用户名', 'User', 'app')]),
    _cmd('sudo -l -U app', 'security', '查看用户 sudo 权限', '列出指定用户被允许执行的 sudo 命令，需要相应权限。', 'User sudo permissions', 'List sudo commands permitted for a user.',
         'sudo -l -U {user}', [_param('user', '用户名', 'User', 'app')], risk='caution'),
    _cmd('getenforce', 'security', '查看 SELinux 状态', '显示 SELinux 当前为 Enforcing、Permissive 或 Disabled。', 'SELinux state', 'Show whether SELinux is enforcing, permissive or disabled.'),

    # 定时任务
    _cmd('crontab -l', 'schedule', '查看当前用户 Cron', '只读列出当前用户的定时任务。', 'Current user cron', 'List cron jobs for the current user.'),
    _cmd('crontab -l -u app', 'schedule', '查看指定用户 Cron', '只读列出指定用户定时任务，可能需要 root。', 'Another user cron', 'List cron jobs for a selected user.',
         'crontab -l -u {user}', [_param('user', '用户名', 'User', 'app')], risk='caution'),
    _cmd('systemctl list-timers --all', 'schedule', '查看 systemd 定时器', '列出定时器上次和下次执行时间及关联服务。', 'Systemd timers', 'List previous and next runs plus associated services.'),
    _cmd('atq', 'schedule', '查看一次性 at 任务', '列出当前排队等待执行的一次性任务。', 'Queued at jobs', 'List queued one-time at jobs.'),

    # 软件、环境和证书
    _cmd('rpm -qa | sort', 'software', '查看 RPM 已安装包', '列出并排序 RPM 系统中已安装的软件包。', 'Installed RPM packages', 'List installed RPM packages in sorted order.'),
    _cmd('dpkg -l', 'software', '查看 DEB 已安装包', '列出 Debian/Ubuntu 系统中的包状态和版本。', 'Installed DEB packages', 'List Debian/Ubuntu package state and versions.'),
    _cmd('java -version', 'software', '查看 Java 版本', '显示当前 PATH 中 Java 运行时版本和发行版。', 'Java version', 'Show the Java runtime version selected by PATH.'),
    _cmd('python3 --version', 'software', '查看 Python 版本', '显示当前 PATH 中 Python 3 版本。', 'Python version', 'Show the Python 3 version selected by PATH.'),
    _cmd('nginx -T', 'software', '检查并展开 Nginx 配置', '校验 Nginx 配置语法并输出完整合并配置，可能包含敏感路径和域名。', 'Validate Nginx configuration', 'Validate and print merged Nginx configuration; may expose sensitive data.', risk='caution'),
    _cmd('openssl x509 -in cert.pem -noout -text', 'software', '查看证书完整信息', '读取本地 PEM 证书的主题、签发者、有效期和扩展。', 'Inspect a certificate', 'Read subject, issuer, validity and extensions from a PEM certificate.',
         'openssl x509 -in {cert_path} -noout -text', [_param('cert_path', '证书路径', 'Certificate path', '/path/cert.pem')]),
    _cmd('openssl s_client -connect host:443 -servername host', 'software', '检查远端 TLS 证书链', '连接目标 TLS 端口并显示握手和证书链；不会发送业务请求。', 'Inspect remote TLS chain', 'Connect to a TLS endpoint and display handshake and certificate chain.',
         'openssl s_client -connect {host}:{port} -servername {host} </dev/null', [_param('host', '域名', 'Host', 'example.com'), _param('port', '端口', 'Port', '443', 'int')], risk='caution'),
]


def command_text(command, language='zh'):
    title = command['title_zh' if language == 'zh' else 'title_en']
    return f"{command['command']}  ·  {title}"


def search_commands(query='', category='all', limit=None, commands=None):
    from tools.list_pin import is_pinned, ops_command_pin_id, pinned_at_rank, namespace_is_pinned, namespace_pinned_at

    query = re.sub(r'\s+', ' ', query.strip().casefold())
    scored = []
    source = commands if commands is not None else COMMANDS
    for index, command in enumerate(source):
        if category != 'all' and command['category'] != category:
            continue
        if not query:
            score = 1.0 - index / max(len(source) * 10, 1)
        else:
            haystack = ' '.join((
                command['command'], command['title_zh'], command['description_zh'],
                command['title_en'], command['description_en'], command['tags'],
            )).casefold()
            command_name = command['command'].casefold()
            if command_name == query:
                score = 10.0
            elif command_name.startswith(query):
                score = 8.0
            elif query in command_name:
                score = 7.0
            elif query in haystack:
                score = 6.0
            else:
                tokens = query.split()
                token_ratio = sum(token in haystack for token in tokens) / len(tokens)
                fuzzy = max(
                    SequenceMatcher(None, query, command_name).ratio(),
                    SequenceMatcher(None, query, command['title_zh'].casefold()).ratio(),
                )
                score = max(token_ratio * 4.0, fuzzy * 3.0)
                if score < 1.45:
                    continue
        cmd = dict(command)
        pin_id = ops_command_pin_id(cmd)
        cmd['pinned'] = namespace_is_pinned('ops_command', pin_id)
        if cmd['pinned']:
            cmd['pinned_at'] = namespace_pinned_at('ops_command', pin_id)
        scored.append((score, index, cmd))

    pinned_rows = [row for row in scored if is_pinned(row[2])]
    plain_rows = [row for row in scored if not is_pinned(row[2])]
    pinned_rows.sort(key=lambda row: (pinned_at_rank(row[2]), row[0]), reverse=True)
    plain_rows.sort(key=lambda row: (-row[0], row[1]))
    result = [row[2] for row in pinned_rows + plain_rows]
    return result[:limit] if limit else result


def _safe_value(param, value):
    value = str(value).strip()
    if param.get('kind') == 'int':
        if not re.fullmatch(r'-?\d+', value):
            raise ValueError(f"{param['label_zh']}必须是整数")
        return value
    if not value:
        raise ValueError(f"请填写{param['label_zh']}")
    if '\n' in value or '\r' in value:
        raise ValueError(f"{param['label_zh']}不能包含换行")
    return shlex.quote(value)


def build_command(command, values):
    if command.get('workflow') == 'log_extract':
        return _build_log_extract(command, values)
    if command.get('workflow') == 'log_and_keywords':
        return _build_log_and_keywords(command, values)
    rendered = {}
    for param in command['params']:
        raw = values.get(param['name'], param.get('default', ''))
        rendered[param['name']] = _safe_value(param, raw)
    return command['template'].format(**rendered)


def _build_log_and_keywords(command, values):
    params = {param['name']: param for param in command['params']}
    keyword = _safe_value(params['keyword'], values.get('keyword', 'ERROR'))
    context = _safe_value(params['context'], values.get('context', '20'))
    log_path = _safe_value(params['log_path'], values.get('log_path', '/var/log/app/app.log'))
    extras = []
    for key in ('also_1', 'also_2', 'also_3'):
        raw = str(values.get(key, '')).strip()
        if not raw:
            continue
        extras.append(_safe_value(params[key], raw) if key in params else shlex.quote(raw))
    cmd = f'grep -a -n -i -C {context} -- {keyword} {log_path}'
    for extra in extras:
        cmd += f' | grep -a -i -- {extra}'
    if not extras:
        cmd += '\n# 提示：也包含 1/2/3 可填多个必须同时出现的关键字'
    return cmd


def _build_log_extract(command, values):
    params = {param['name']: param for param in command['params']}
    search_root = _safe_value(params['search_root'], values.get('search_root', '/var/log'))
    file_pattern = _safe_value(params['file_pattern'], values.get('file_pattern', '*.log'))
    days = _safe_value(params['days'], values.get('days', '7'))
    first = (
        '# 第 1 步：路径不确定时先查找最近更新的候选日志\n'
        f'find {search_root} -type f -name {file_pattern} -mtime -{days} 2>/dev/null | head -n 100'
    )
    log_path = str(values.get('log_path', '')).strip()
    if not log_path:
        return first + '\n\n# 确认准确日志路径后，填入“确认后的日志路径”再生成截取命令。'
    keyword = _safe_value(params['keyword'], values.get('keyword', 'ERROR'))
    context = _safe_value(params['context'], values.get('context', '20'))
    source = _safe_value(params['log_path'], log_path)
    output = _safe_value(params['output_file'], values.get('output_file', '/tmp/log_extract.txt'))
    second = (
        '# 第 2 步：从已确认的日志中截取关键词和上下文到新文件（原日志不变）\n'
        f'grep -n -i -C {context} -- {keyword} {source} > {output}'
    )
    return first + '\n\n' + second


def contains_forbidden_delete(command_text_value):
    """命令库硬约束：不允许出现文件删除类命令或 find -delete。"""
    normalized = re.sub(r'\s+', ' ', command_text_value.strip().casefold())
    return bool(re.search(r'(^|[;&|]\s*)(rm|rmdir|unlink|shred)\b|\bfind\b.*\s-delete\b', normalized))


def infer_risk(command_text_value):
    normalized = re.sub(r'\s+', ' ', command_text_value.strip().casefold())
    mutating = (
        r'(^|[;&|]\s*)(systemctl\s+(start|stop|restart|reload)|kill\b|renice\b|'
        r'chmod\b|chown\b|mv\b|cp\b|docker\s+restart|kubectl\s+rollout\s+restart)'
    )
    return 'danger' if re.search(mutating, normalized) else 'safe'


def output_guide(command, language='zh'):
    """解释命令典型输出的关键字段；按命令族匹配，未命中时给出分类级说明。"""
    text = command['command'].casefold()
    guides = [
        (r'^ps -ef', 'UID=进程用户；PID=进程号；PPID=父进程号；C=CPU 调度占用；STIME=启动时间；TTY=终端；TIME=累计 CPU 时间；CMD=完整启动命令。', 'UID=owner; PID=process ID; PPID=parent ID; C=CPU scheduling usage; STIME=start time; TTY=terminal; TIME=CPU time; CMD=full command.'),
        (r'^ps aux', 'USER=用户；PID=进程号；%CPU/%MEM=资源比例；VSZ=虚拟内存；RSS=常驻物理内存；STAT=进程状态；START/TIME=启动与累计 CPU 时间；COMMAND=命令。', 'USER=owner; PID=ID; %CPU/%MEM=usage; VSZ=virtual memory; RSS=resident memory; STAT=state; START/TIME=timing; COMMAND=command.'),
        (r'^uptime', '依次为当前时间、已运行时长、登录用户数、1/5/15 分钟平均负载。负载需结合 CPU 核数判断，持续高于核数通常表示任务排队。', 'Shows current time, uptime, logged-in users and 1/5/15-minute load. Compare load with CPU core count.'),
        (r'^top', '顶部依次展示负载、任务数、CPU、内存和 swap；进程区重点看 PID、USER、%CPU、%MEM、TIME+、COMMAND。按 q 退出。', 'Header shows load, tasks, CPU, memory and swap. Process rows include PID, USER, %CPU, %MEM, TIME+ and COMMAND.'),
        (r'^free ', 'total=总量；used=已用；free=完全空闲；buff/cache=缓存；available=不触发 swap 时大致可用内存。判断余量优先看 available。', 'total=total; used=used; free=unused; buff/cache=cache; available=memory usable without swapping. Prefer available for capacity.'),
        (r'^vmstat', 'r/b=运行队列和不可中断任务；si/so=换入换出；bi/bo=块设备读写；us/sy/id/wa=用户、内核、空闲和 IO 等待 CPU 百分比。首行是启动以来平均值。', 'r/b=run queue and blocked tasks; si/so=swap; bi/bo=block IO; us/sy/id/wa=user, system, idle and IO-wait CPU. First row is boot average.'),
        (r'^iostat', 'r/s、w/s=每秒读写；rkB/s、wkB/s=吞吐；await=平均等待毫秒；aqu-sz=队列；%util=设备忙碌比例。await 和 %util 持续偏高需排查 IO。', 'r/s,w/s=IOPS; rkB/s,wkB/s=throughput; await=latency ms; aqu-sz=queue; %util=device busy percentage.'),
        (r'^mpstat', '%usr/%sys=用户和内核 CPU；%iowait=等待 IO；%idle=空闲；CPU=ALL 是总体，其余行为各逻辑 CPU。', '%usr/%sys=user and system CPU; %iowait=IO wait; %idle=idle; CPU=ALL is aggregate.'),
        (r'^sar -n', 'IFACE=网卡；rxpck/s、txpck/s=每秒收发包；rxkB/s、txkB/s=吞吐；%ifutil=接口利用率。', 'IFACE=interface; rxpck/s,txpck/s=packets; rxkB/s,txkB/s=throughput; %ifutil=utilization.'),
        (r'^pidstat', 'PID=进程号；%usr/%system=用户和内核 CPU；%CPU=总占用；CPU=所在核；Command=进程名。', 'PID=process; %usr/%system=user/system CPU; %CPU=total; CPU=core; Command=name.'),
        (r'^(tail|head|sed|awk|zgrep|grep)', '输出主体是原日志行；grep -n 前缀数字是行号，-C 会附带前后文。无输出通常表示关键词未命中、路径错误或权限不足，不代表服务一定正常。', 'Output is source log text. grep -n prefixes line numbers and -C adds context. No output may mean no match, wrong path or insufficient permission.'),
        (r'^find ', '每行是一个匹配路径。无输出表示范围内未找到、目录不可读或筛选条件过严；先核对搜索根目录、文件名模式和时间条件。', 'Each line is a matched path. No output can mean no match, unreadable directories or overly strict filters.'),
        (r'^journalctl', '常见列为时间、主机、服务/进程名[PID] 和消息正文；--since 控制时间范围，-p 控制级别。无记录还要检查服务名、日志持久化和权限。', 'Rows contain time, host, service/process[PID] and message. --since selects time and -p severity. No rows may indicate name, retention or permission issues.'),
        (r'^dmesg', '每行包含内核时间和子系统消息；重点关注 error、fail、timeout、oom、reset 等词，并结合相邻上下文判断。', 'Rows contain kernel time and subsystem messages. Focus on error, fail, timeout, oom and reset with context.'),
        (r'^ss ', 'Netid=协议；State=状态；Recv-Q/Send-Q=收发队列；Local/Peer Address:Port=本地和远端；Process=关联进程。监听服务通常为 LISTEN。', 'Netid=protocol; State=state; Recv-Q/Send-Q=queues; Local/Peer Address:Port=endpoints; Process=owner. Listeners show LISTEN.'),
        (r'^lsof', 'COMMAND/PID/USER=进程；FD=文件描述符；TYPE=对象类型；DEVICE/SIZE/OFF/NODE=设备与节点；NAME=文件或网络端点。', 'COMMAND/PID/USER=process; FD=descriptor; TYPE=object; DEVICE/SIZE/OFF/NODE=metadata; NAME=file or endpoint.'),
        (r'^ip addr', '每个网卡块包含状态、MAC 和 inet/inet6 地址；UP 表示启用，LOWER_UP 表示物理链路已建立。', 'Each interface shows state, MAC and inet/inet6 addresses. UP=enabled; LOWER_UP=physical link present.'),
        (r'^ip route', 'default 行给出默认网关和出口网卡；其余行是目标网段、下一跳、设备和源地址。', 'default shows gateway and interface; other rows show destination, next hop, device and source.'),
        (r'^ping ', '每行显示响应主机、序号、TTL 和延迟；末尾汇总发送/接收、丢包率及 min/avg/max 延迟。丢包或延迟波动需结合路由排查。', 'Replies show host, sequence, TTL and latency. Summary includes sent/received, loss and min/avg/max latency.'),
        (r'^traceroute', '每行是一个路由跳点及多次探测延迟；星号表示该次未响应，不一定代表链路中断，需看后续跳点是否继续。', 'Each row is a hop and probe latency. An asterisk is no reply, not necessarily a broken path if later hops respond.'),
        (r'^curl ', '< 状态行和响应头来自服务器，> 表示发出的请求头，* 是连接/TLS 诊断；重点看 HTTP 状态码、Location、耗时和证书错误。', '< is response, > request, and * connection/TLS diagnostics. Check HTTP status, Location, timing and certificate errors.'),
        (r'^dig ', 'QUESTION=查询；ANSWER=解析结果；AUTHORITY=权威信息；SERVER=响应 DNS；Query time=耗时。ANSWER 为空需检查状态码和记录类型。', 'QUESTION=query; ANSWER=result; AUTHORITY=authority; SERVER=resolver; Query time=latency. Check status and record type if ANSWER is empty.'),
        (r'^df -h', 'Filesystem=设备；Type=类型；Size/Used/Avail=容量；Use%=使用率；Mounted on=挂载点。使用率过高时还要结合 inode 检查。', 'Filesystem=device; Type=type; Size/Used/Avail=capacity; Use%=usage; Mounted on=mount point. Also inspect inodes.'),
        (r'^df -i', 'Inodes=总 inode；IUsed/IFree=已用/剩余；IUse%=使用率。接近 100% 时即使磁盘有空间也可能无法创建文件。', 'Inodes=total; IUsed/IFree=used/free; IUse%=usage. Near 100% can prevent file creation despite free bytes.'),
        (r'^du ', '每行左侧是目录占用，右侧是路径；排序后末尾通常是占用最大的目录。统计大目录可能较慢。', 'Each row shows size then path; after sorting, largest entries are at the end. Large trees may take time.'),
        (r'^lsblk', 'NAME=设备树；FSTYPE=文件系统；UUID=标识；FSAVAIL/FSUSE%=余量和使用率；MOUNTPOINTS=挂载点。', 'NAME=device tree; FSTYPE=filesystem; UUID=ID; FSAVAIL/FSUSE%=capacity; MOUNTPOINTS=mount paths.'),
        (r'^systemctl status', 'Loaded=单元文件状态；Active=当前状态和持续时间；Main PID=主进程；下方是进程树和近期日志。failed 后重点看退出码和日志。', 'Loaded=unit file; Active=state and duration; Main PID=process; below are process tree and logs. For failed, inspect exit code and journal.'),
        (r'^systemctl list', 'UNIT=单元名；LOAD=是否加载；ACTIVE/SUB=总体和细分状态；DESCRIPTION=说明。failed 条目需要结合 status 和 journalctl。', 'UNIT=name; LOAD=loaded; ACTIVE/SUB=state; DESCRIPTION=description. Investigate failed units with status and journalctl.'),
        (r'^docker ps', 'CONTAINER ID=容器；IMAGE=镜像；COMMAND=入口命令；CREATED/STATUS=创建和状态；PORTS=映射；NAMES=名称。', 'CONTAINER ID=container; IMAGE=image; COMMAND=entrypoint; CREATED/STATUS=state; PORTS=mappings; NAMES=name.'),
        (r'^docker stats', 'CPU %=CPU；MEM USAGE/LIMIT 与 MEM %=内存；NET I/O=网络；BLOCK I/O=磁盘；PIDS=进程数。', 'CPU %=CPU; MEM USAGE/LIMIT and MEM %=memory; NET I/O=network; BLOCK I/O=disk; PIDS=process count.'),
        (r'^kubectl get pods', 'READY=就绪容器数；STATUS=阶段；RESTARTS=重启次数；AGE=存活时间；IP/NODE=地址和节点。重点关注非 Running、未 Ready 和重启增长。', 'READY=ready containers; STATUS=phase; RESTARTS=count; AGE=age; IP/NODE=placement. Watch non-Running, unready and rising restarts.'),
        (r'^kubectl describe', '依次展示元数据、容器状态、探针、卷、条件和 Events；故障定位通常先看 State/Last State、Reason、Exit Code 和底部事件。', 'Shows metadata, container state, probes, volumes, conditions and Events. Check State/Last State, Reason, Exit Code and events.'),
        (r'^kubectl (logs|top|events|get events)', 'logs 为容器标准输出；top 展示 CPU/内存；events 展示类型、原因、对象、次数和消息。无输出需核对命名空间、Pod/容器名和权限。', 'logs is container output; top shows CPU/memory; events show type, reason, object, count and message. Verify namespace, names and permissions if empty.'),
        (r'^openssl x509', '重点字段：Subject=证书主体；Issuer=签发者；Not Before/After=有效期；Public Key=公钥；X509v3 SAN=适用域名。', 'Key fields: Subject; Issuer; Not Before/After; Public Key; X509v3 SAN hostnames.'),
        (r'^openssl s_client', 'CONNECTED 表示 TCP 成功；Certificate chain 是证书链；Verify return code=0 通常表示校验成功；还需检查协议、套件、SNI 和有效期。', 'CONNECTED means TCP success; Certificate chain lists chain; Verify return code=0 usually succeeds. Check protocol, cipher, SNI and validity.'),
    ]
    for pattern, zh, en in guides:
        if re.search(pattern, text):
            return zh if language == 'zh' else en
    defaults = {
        'logs': ('输出通常由时间、来源和消息组成；先确认时间范围，再结合上下文和同一请求标识判断。', 'Output usually contains time, source and message. Confirm time range and correlate context/request IDs.'),
        'status': ('重点比较当前值、趋势和系统容量基线，单个瞬时数值不能直接判定故障。', 'Compare current values and trends with system capacity; one snapshot alone does not prove a fault.'),
        'process': ('先确认 PID、用户、父子关系和完整命令，再结合 CPU、内存及日志判断。', 'Verify PID, owner, parent relationship and full command, then correlate resources and logs.'),
        'network': ('重点查看状态、端点、丢包、延迟和错误；无输出也可能是权限、协议或筛选条件问题。', 'Inspect state, endpoints, loss, latency and errors. Empty output may be permission, protocol or filter related.'),
        'disk': ('核对设备或路径、容量、使用率、权限和时间；大目录读取可能需要等待。', 'Check device/path, capacity, usage, permissions and timestamps; large trees may take time.'),
        'service': ('执行后用 status 和日志确认 Active 状态、主 PID、退出码及近期错误。', 'After execution verify Active state, main PID, exit code and recent logs.'),
        'container': ('结合对象状态、重启次数、事件、资源和日志判断，注意命名空间及容器名。', 'Correlate state, restarts, events, resources and logs; verify namespace and container name.'),
        'security': ('输出可能包含账号和来源地址等敏感信息，分享前脱敏，并结合时间和审计记录确认。', 'Output may contain account and origin data; redact before sharing and correlate with audit time.'),
        'schedule': ('重点确认任务主体、执行用户、时间表达式、下次运行时间和关联服务。', 'Verify task, execution user, schedule, next run and associated service.'),
        'software': ('重点核对版本、路径、发行版、有效期及错误码，确认命令使用的是预期环境。', 'Check version, path, distribution, validity and error codes; verify the expected environment.'),
    }
    zh, en = defaults.get(command['category'], ('结合命令说明核对输出。', 'Interpret output with the command description.'))
    return zh if language == 'zh' else en


def load_custom_commands(path=None):
    path = path or CUSTOM_OPS_FILE
    if not os.path.isfile(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            loaded = json.load(stream)
    except (OSError, ValueError, TypeError):
        return []
    result = []
    for item in loaded if isinstance(loaded, list) else []:
        if not isinstance(item, dict):
            continue
        command_text_value = str(item.get('command', '')).strip()
        category = item.get('category', 'status')
        if not command_text_value or contains_forbidden_delete(command_text_value) or category not in CATEGORIES or category == 'all':
            continue
        risk = item.get('risk', 'safe')
        if infer_risk(command_text_value) == 'danger':
            risk = 'danger'
        if risk not in RISK_LABELS:
            risk = 'safe'
        result.append(_cmd(
            command_text_value, category,
            str(item.get('title_zh', command_text_value)),
            str(item.get('description_zh', '用户自定义命令')),
            str(item.get('title_en', item.get('title_zh', command_text_value))),
            str(item.get('description_en', item.get('description_zh', 'Custom command'))),
            risk=risk, tags=str(item.get('tags', '用户自定义 custom')),
        ) | {'builtin': False})
    return result


def save_custom_commands(commands, path=None):
    path = path or CUSTOM_OPS_FILE
    if path == CUSTOM_OPS_FILE:
        ensure_config_dir()
    else:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = []
    for item in commands:
        if item.get('builtin', True):
            continue
        command_text_value = str(item.get('command', '')).strip()
        if not command_text_value or contains_forbidden_delete(command_text_value):
            raise ValueError('自定义命令不能包含文件删除命令')
        payload.append({key: item.get(key, '') for key in (
            'command', 'category', 'title_zh', 'description_zh',
            'title_en', 'description_en', 'risk', 'tags',
        )})
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
