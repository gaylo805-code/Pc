#!/usr/bin/env bash
set -uo pipefail

API="https://api.github.com"
REPO="gaylo805-code/Pc"
BRANCH="main"
WORKFLOW="main.yml"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKEN_FILE="$DIR/.token"
TOKEN="${GITHUB_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f "$TOKEN_FILE" ]; then
  TOKEN="$(cat "$TOKEN_FILE")"
fi
if [ -z "$TOKEN" ]; then
  echo "Thieu GitHub token. Dat GITHUB_TOKEN hoac tao file .token" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

API_WORKFLOWS="$API/repos/$REPO/actions/workflows/$WORKFLOW"

echo "==> Kích hoạt workflow $WORKFLOW ($REPO | $BRANCH) ..."

HTTP=$(curl -s -o "$TMP/resp.json" -w "%{http_code}" -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d "{\"ref\":\"$BRANCH\"}" \
  "$API_WORKFLOWS/dispatches")

if [ "$HTTP" != "204" ]; then
  echo "Loi khi kich hoat (HTTP $HTTP): $(cat "$TMP/resp.json")" >&2
  exit 1
fi
echo "OK - da gui lenh tao may."

echo "==> Cho workflow bat dau chay ..."

PREV_RUN=$(curl -s -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
  "$API_WORKFLOWS/runs?event=workflow_dispatch&per_page=1" | jq -r '.workflow_runs[0].id // 0')

RUN_ID=""
for i in $(seq 1 60); do
  curl -s -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
    "$API_WORKFLOWS/runs?event=workflow_dispatch&per_page=1" > "$TMP/runs.json"
  NEW_ID=$(jq -r '.workflow_runs[0].id // 0' "$TMP/runs.json" 2>/dev/null)
  if [ -n "$NEW_ID" ] && [ "$NEW_ID" != "0" ] && [ "$NEW_ID" != "$PREV_RUN" ]; then
    RUN_ID="$NEW_ID"
    break
  fi
  sleep 5
done

if [ -z "$RUN_ID" ]; then
  echo "Khong tim thay run moi. Kiem tra tai https://github.com/$REPO/actions" >&2
  exit 1
fi
echo "Run ID: $RUN_ID"
echo "Theo doi truc tiep: https://github.com/$REPO/actions/runs/$RUN_ID"
echo ""

echo "==> Chờ máy ảo xuất thông tin kết nối (artifact connection-info)..."
WIN_DONE=0
LAST_PID=""
for i in $(seq 1 120); do
  curl -s -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
    "$API/repos/$REPO/actions/runs/$RUN_ID/artifacts" > "$TMP/arts.json" 2>/dev/null
  AID=$(jq -r '.artifacts[] | select(.name=="connection-info") | .id' "$TMP/arts.json" 2>/dev/null | head -1)

  if [ -n "$AID" ]; then
    curl -sL -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
      -o "$TMP/conn.zip" "$API/repos/$REPO/actions/artifacts/$AID/zip" 2>/dev/null
    rm -rf "$TMP/conn"; mkdir -p "$TMP/conn"
    (unzip -o -q "$TMP/conn.zip" -d "$TMP/conn" 2>/dev/null || true)
    CF=$(find "$TMP/conn" -name 'connection.json' 2>/dev/null | head -1)

    if [ -n "$CF" ] && [ "$CF" != "$LAST_PID" ]; then
      LAST_PID="$CF"
      NODE=$(jq -r '.node_id // empty' "$CF")
      IP=$(jq -r '.zt_ip // empty' "$CF")
      RUSER=$(jq -r '.rdp_user // empty' "$CF")
      RPASS=$(jq -r '.rdp_pass // empty' "$CF")
      RNODE=$(jq -r '.rdp_port // empty' "$CF")
      NET=$(jq -r '.zerotier_net // empty' "$CF")

      WIN_DONE=1
      echo ""
      echo "================================================================"
      echo "         ✅ MÁY WINDOWS ĐÃ KÍCH HOẠT - THÔNG TIN KẾT NỐI"
      echo "================================================================"
      echo "  🆔 Repository  : $REPO"
      echo "  🔗 Run         : https://github.com/$REPO/actions/runs/$RUN_ID"
      echo "  🌐 ZeroTier    : ${NET:-b103a835d2b3a8b0}"
      echo "  🆔 Node ID     : ${NODE:-???}  (dùng để authorize)"
      if [ -n "$IP" ]; then
        echo "  📡 ZeroTier IP : $IP"
      else
        echo "  📡 ZeroTier IP : (chưa có - cần authorize node)"
      fi
      echo "  👤 Username    : ${RUSER:-runneradmin}"
      echo "  🔑 Password    : ${RPASS:-Runner@123456}"
      echo "  🔌 RDP Port    : ${RNODE:-3389}"
      echo "--------------------------------------------------------------"
      echo "  🚀 Cách kết nối:"
      echo "   1. Vào https://my.zerotier.com, authorize node ${NODE:-<Node ID>}"
      echo "      trong network ${NET:-b103a835d2b3a8b0}"
      echo "   2. Sau khi authorize, IP ZeroTier hiện trong danh sách member"
      echo "      trên my.zerotier.com (hoặc cột Managed IP)"
      echo "   3. Mở Remote Desktop Connection, gõ IP đó"
      echo "   4. Đăng nhập: ${RUSER:-runneradmin} / ${RPASS:-Runner@123456}"
      echo "================================================================"
      break
    fi
  fi

  if [ "$i" = "30" ] || [ "$i" = "60" ] || [ "$i" = "90" ]; then
    echo "... vẫn đang đợi máy ảo khởi động (lần $i/120) ..."
  fi
  sleep 10
done

if [ "$WIN_DONE" = "0" ]; then
  echo ""
  echo "⚠️  Chưa nhận được artifact sau ~20 phút. Runner có thể vẫn đang khởi động."
  echo "Xem trạng thái tại: https://github.com/$REPO/actions"
fi