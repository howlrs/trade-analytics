#!/bin/bash
# boot_inject: セッション開始/コンパクション時にコンテキストを注入

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
STATE_FILE="$PROJECT_DIR/state.json"

# state.json があれば直近状態を表示
if [ -f "$STATE_FILE" ]; then
  node -e "
    const s = JSON.parse(require('fs').readFileSync('$STATE_FILE','utf8'));
    const now = Date.now();
    const lines = [];

    // ポジション
    if (s.positions) {
      const entries = Object.entries(s.positions);
      lines.push('Positions: ' + entries.length);
      for (const [pool, pos] of entries) {
        lines.push('  pool:' + pool.slice(0,10) + '… → pos:' + pos.slice(0,10) + '…');

        // 直近リバランス
        const rt = s.lastRebalanceTimes?.[pos];
        if (rt) {
          const ago = Math.round((now - rt) / 3600000);
          lines.push('  Last rebalance: ' + ago + 'h ago');
        }

        // 日次リバランス回数
        const dc = s.dailyRebalanceCounts?.[pos];
        if (dc) lines.push('  Daily rebalances: ' + dc.count + ' (' + dc.date + ')');
      }
    }

    if (s.lastUpdated) lines.push('State updated: ' + s.lastUpdated);
    if (lines.length) console.log(lines.join('\n'));
  " 2>/dev/null || true
fi

# .env の運用状態
if [ -f "$PROJECT_DIR/.env" ]; then
  PAUSED=$(grep -E "^PAUSED=" "$PROJECT_DIR/.env" 2>/dev/null | cut -d= -f2)
  DRY_RUN=$(grep -E "^DRY_RUN=" "$PROJECT_DIR/.env" 2>/dev/null | cut -d= -f2)
  echo "PAUSED=${PAUSED:-false} DRY_RUN=${DRY_RUN:-true}"
fi
