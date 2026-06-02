#!/usr/bin/env bash
# Collect RPi health metrics to /var/log/rpi-health.log as compact JSON lines
set -euo pipefail

LOG=/var/log/rpi-health.log

_ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
json_escape() { sed 's/\\/\\\\/g; s/"/\\"/g; s/\n/ /g; s/\r/ /g'; }
null_if_na() { [[ "${1:-}" == "NA" || -z "${1:-}" ]] && echo null || echo "$1"; }

# vcgencmd may be unavailable without firmware tools; tolerate missing
has_vcgencmd=0; command -v vcgencmd >/dev/null 2>&1 && has_vcgencmd=1
throttled_raw=$([[ $has_vcgencmd -eq 1 ]] && vcgencmd get_throttled 2>/dev/null | awk -F= '{print $2}' || echo "NA")
temp_c_raw=$([[ $has_vcgencmd -eq 1 ]] && vcgencmd measure_temp 2>/dev/null | awk -F= '{print $2}' | tr -d "'C" || echo "NA")
volt_core=$([[ $has_vcgencmd -eq 1 ]] && vcgencmd measure_volts core 2>/dev/null | awk -F= '{print $2}' | tr -d 'V' || echo "NA")
volt_sdram_c=$([[ $has_vcgencmd -eq 1 ]] && vcgencmd measure_volts sdram_c 2>/dev/null | awk -F= '{print $2}' | tr -d 'V' || echo "NA")
volt_sdram_i=$([[ $has_vcgencmd -eq 1 ]] && vcgencmd measure_volts sdram_i 2>/dev/null | awk -F= '{print $2}' | tr -d 'V' || echo "NA")
volt_sdram_p=$([[ $has_vcgencmd -eq 1 ]] && vcgencmd measure_volts sdram_p 2>/dev/null | awk -F= '{print $2}' | tr -d 'V' || echo "NA")

# Decode throttled bits when present (hex like 0x50005)
undervolt=0; freq_capped=0; throttled=0; soft_temp=0; uv_since=0; fc_since=0; th_since=0; st_since=0
if [[ "$throttled_raw" =~ ^0x[0-9a-fA-F]+$ ]]; then
  val=$((16#${throttled_raw#0x}))
  (( (val & (1<<0)) != 0 )) && undervolt=1
  (( (val & (1<<1)) != 0 )) && freq_capped=1
  (( (val & (1<<2)) != 0 )) && throttled=1
  (( (val & (1<<3)) != 0 )) && soft_temp=1
  (( (val & (1<<16)) != 0 )) && uv_since=1
  (( (val & (1<<17)) != 0 )) && fc_since=1
  (( (val & (1<<18)) != 0 )) && th_since=1
  (( (val & (1<<19)) != 0 )) && st_since=1
fi

# CPU load, freq, governor, memory, swap
read -r l1 l5 l15 _ < /proc/loadavg || { l1=0 l5=0 l15=0; }
cpu_khz=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null || echo "0")
cpu_mhz=$(( cpu_khz / 1000 ))
gov=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "NA")
mem_total_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
mem_avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
swap_total_kb=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
swap_free_kb=$(awk '/SwapFree/ {print $2}' /proc/meminfo)

# Disk/rootfs
df_out=$(df -P / | tail -1)
root_used_pct=$(awk '{print $5}' <<<"$df_out" | tr -d '%')
root_avail_kb=$(awk '{print $4}' <<<"$df_out")
root_ro=0; mount | grep -E ' on / ' | grep -q '(ro,' && root_ro=1 || true

# Uptime and boot id
uptime_s=$(awk '{print int($1)}' /proc/uptime)
boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo "NA")

# Top 5 processes by CPU (instant snapshot)
TOP_JSON=$(ps -eo pid,pcpu,pmem,comm --no-headers | sort -k2 -nr | head -n 5 | awk '{printf("{\"pid\":%s,\"cpu\":%s,\"mem\":%s,\"comm\":\"%s\"}", $1,$2,$3,$4); if(NR<5) printf ","}' )

# Sensor daemon processes (if any)
SENSOR_JSON=""; pids=$(pgrep -f "sensor_lora_daemon.py" || true)
if [[ -n "${pids:-}" ]]; then
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    read -r pcpu pmem rss comm <<< "$(ps -o pcpu=,pmem=,rss=,comm= -p "$pid" 2>/dev/null | awk '{print $1" "$2" "$3" "$4}')"
    [[ -n "${SENSOR_JSON}" ]] && SENSOR_JSON+="," || true
    SENSOR_JSON+="{\"pid\":$pid,\"cpu\":${pcpu:-0},\"mem\":${pmem:-0},\"rss_kb\":${rss:-0},\"comm\":\"${comm:-NA}\"}"
  done <<< "$pids"
fi

# Kernel messages in last minute via journald
JOUR_KMSG=$(journalctl -k --utc --since "1 minute ago" --until "now" --output=short-iso --no-pager 2>/dev/null | \
  grep -iE 'under-?volt|oom|error|fail|throttl|mmc|ext4' || true)
JOUR_KMSG_JSON=$(printf '%s' "$JOUR_KMSG" | json_escape)

# Recent critical counts (5 minutes)
crit_5m=$(journalctl --utc --since "5 minutes ago" --until "now" --no-pager 2>/dev/null | grep -iE 'under-?volt|oom' | wc -l || echo 0)

# Emit one-line JSON
printf '{"ts":"%s","boot_id":"%s","uptime_s":%s,' "$(_ts)" "$boot_id" "$uptime_s" >> "$LOG"
printf '"vcgencmd":{"throttled":"%s","decoded":{"undervolt":%s,"freq_capped":%s,"throttled":%s,"soft_temp":%s,"undervolt_since_boot":%s,"freq_capped_since_boot":%s,"throttled_since_boot":%s,"soft_temp_since_boot":%s},' \
  "$throttled_raw" "$undervolt" "$freq_capped" "$throttled" "$soft_temp" "$uv_since" "$fc_since" "$th_since" "$st_since" >> "$LOG"
printf '"temp_c":%s,' "$(null_if_na "$temp_c_raw")" >> "$LOG"
printf '"volt_core_v":%s,"volt_sdram_c_v":%s,"volt_sdram_i_v":%s,"volt_sdram_p_v":%s},' \
  "$(null_if_na "$volt_core")" "$(null_if_na "$volt_sdram_c")" "$(null_if_na "$volt_sdram_i")" "$(null_if_na "$volt_sdram_p")" >> "$LOG"
printf '"cpu":{"mhz":%s,"governor":"%s"},' "$cpu_mhz" "$gov" >> "$LOG"
printf '"loadavg":[%s,%s,%s],' "$l1" "$l5" "$l15" >> "$LOG"
printf '"mem":{"total_kb":%s,"avail_kb":%s,"swap_total_kb":%s,"swap_free_kb":%s},' "$mem_total_kb" "$mem_avail_kb" "$swap_total_kb" "$swap_free_kb" >> "$LOG"
printf '"disk":{"root_used_pct":%s,"root_avail_kb":%s,"root_ro":%s},' "$root_used_pct" "$root_avail_kb" "$root_ro" >> "$LOG"
printf '"top":[%s],' "$TOP_JSON" >> "$LOG"
printf '"sensor_daemon":[%s],' "$SENSOR_JSON" >> "$LOG"
printf '"kmsg_recent":"%s","critical_events_5m":%s}\n' "$JOUR_KMSG_JSON" "$crit_5m" >> "$LOG"
