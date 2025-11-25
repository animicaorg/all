#!/usr/bin/env bash
set -Eeuo pipefail

# ---- Tunables ---------------------------------------------------------------
CHAIN_ID="${ANIMICA_CHAIN_ID:-0xa11ca}"
NETWORK_NAME="${ANIMICA_NETWORK:-testnet}"
DATADIR="${ANIMICA_DATADIR:-$HOME/.animica/$NETWORK_NAME}"
RPC_ADDR="${ANIMICA_RPC_ADDR:-0.0.0.0}"
RPC_PORT="${ANIMICA_RPC_PORT:-8545}"
WS_PORT="${ANIMICA_WS_PORT:-8546}"
P2P_PORT="${ANIMICA_P2P_PORT:-30333}"
NAT_IP="${ANIMICA_NAT_IP:-$(hostname -I | awk '{print $1}')}"
LOGDIR="${ANIMICA_LOGDIR:-$PWD/logs}"
LOGFILE="${ANIMICA_LOGFILE:-$LOGDIR/${NETWORK_NAME}.log}"
PIDFILE="${ANIMICA_PIDFILE:-$LOGDIR/${NETWORK_NAME}.pid}"

mkdir -p "$LOGDIR" "$(dirname "$DATADIR")"

# We assume you have aicf/node.py stub (python -m aicf.node)
node_cmd() {
  echo "python -m aicf.node"
}

busy() {
  ss -lntp | egrep -q ":(($RPC_PORT)|($WS_PORT)|($P2P_PORT))\\b"
}

kill_ports() {
  command -v fuser >/dev/null 2>&1 && fuser -k "${RPC_PORT}/tcp" "${WS_PORT}/tcp" "${P2P_PORT}/tcp" >/dev/null 2>&1 || true
  command -v lsof  >/dev/null 2>&1 && lsof -ti TCP:"$RPC_PORT","$WS_PORT","$P2P_PORT" -sTCP:LISTEN 2>/dev/null | xargs -r kill -TERM || true
  sleep 0.4
  command -v lsof  >/dev/null 2>&1 && lsof -ti TCP:"$RPC_PORT","$WS_PORT","$P2P_PORT" -sTCP:LISTEN 2>/dev/null | xargs -r kill -KILL || true
}

start() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "[animica] already running (pid $(cat "$PIDFILE"))"; exit 0
  fi
  if busy; then
    echo "[animica] ports busy; refusing to start"
    ss -lntp | egrep ":(($RPC_PORT)|($WS_PORT)|($P2P_PORT))\\b" || true
    exit 1
  fi
  CMD="$(node_cmd)"
  echo "[animica] starting $CMD" | tee -a "$LOGFILE"
  nohup bash -lc "$CMD --network $NETWORK_NAME --chain-id $CHAIN_ID --datadir \"$DATADIR\" --rpc-addr $RPC_ADDR --rpc-port $RPC_PORT --ws-port $WS_PORT --p2p-port $P2P_PORT --nat $NAT_IP --http --ws --allow-cors" >>"$LOGFILE" 2>&1 &
  echo $! >"$PIDFILE"
  sleep 0.6
  if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "[animica] PID $(cat "$PIDFILE"), logs: $LOGFILE"
  else
    echo "[animica] failed to start; recent log:"; tail -n 80 "$LOGFILE" || true; exit 1
  fi
}

run_fg() {
  if busy; then
    echo "[animica] ports busy; showing owners:"
    ss -lntp | egrep ":(($RPC_PORT)|($WS_PORT)|($P2P_PORT))\\b" || true
    exit 1
  fi
  CMD="$(node_cmd)"
  echo "[animica] running foreground: $CMD --network $NETWORK_NAME --chain-id $CHAIN_ID --datadir $DATADIR --rpc-addr $RPC_ADDR --rpc-port $RPC_PORT --ws-port $WS_PORT --p2p-port $P2P_PORT --nat $NAT_IP --http --ws --allow-cors"
  exec bash -lc "$CMD --network $NETWORK_NAME --chain-id $CHAIN_ID --datadir \"$DATADIR\" --rpc-addr $RPC_ADDR --rpc-port $RPC_PORT --ws-port $WS_PORT --p2p-port $P2P_PORT --nat $NAT_IP --http --ws --allow-cors"
}

stop() {
  if [ -f "$PIDFILE" ]; then
    PID="$(cat "$PIDFILE" || true)"
    [ -n "${PID:-}" ] && kill "$PID" 2>/dev/null || true
    rm -f "$PIDFILE"
  fi
  kill_ports
  echo "[animica] stopped"
}

status() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "[animica] running (pid $(cat "$PIDFILE"))"
  else
    echo "[animica] not running"
  fi
}

logs() { tail -n 200 -f "$LOGFILE"; }

reset() {
  stop
  rm -rf "$DATADIR"
  echo "[animica] datadir cleared: $DATADIR"
}

case "${1:-}" in
  start) start ;;
  run) run_fg ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  logs) logs ;;
  reset) reset ;;
  *) echo "Usage: $0 {start|run|stop|restart|status|logs|reset}"; exit 1 ;;
esac
