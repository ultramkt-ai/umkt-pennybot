#!/bin/bash
BOT_DIR="/home/rafael/polymarket-probability-bot"
LOG_DIR="$BOT_DIR/logs"
mkdir -p "$LOG_DIR"

CRON_LINES="
# Polymarket Probability Bot
*/5 * * * * cd $BOT_DIR && python3 run.py monitor >> $LOG_DIR/monitor.log 2>&1
0 * * * * cd $BOT_DIR && python3 run.py scan --bankroll 800 >> $LOG_DIR/scan.log 2>&1
0 8 * * * cd $BOT_DIR && python3 run.py digest >> $LOG_DIR/digest.log 2>&1
"

if crontab -l 2>/dev/null | grep -q "Polymarket Probability Bot"; then
    echo "Atualizando cron jobs..."
    crontab -l 2>/dev/null | grep -v "Polymarket" | grep -v "run.py monitor" | grep -v "run.py scan" | grep -v "run.py digest" > /tmp/cron_temp
    echo "$CRON_LINES" >> /tmp/cron_temp
    crontab /tmp/cron_temp
else
    echo "Adicionando cron jobs..."
    (crontab -l 2>/dev/null; echo "$CRON_LINES") | crontab -
fi
rm -f /tmp/cron_temp
echo "=== Cron jobs ==="
crontab -l | grep -A5 "Polymarket"
