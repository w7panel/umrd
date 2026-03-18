#!/bin/bash
#
# UMRD 一键安装脚本
#
set -e

echo "========================================="
echo "  UMRD 安装程序"
echo "========================================="

# 检查root权限
if [ "$EUID" -ne 0 ]; then
    echo "错误: 请使用 sudo 运行此脚本"
    exit 1
fi

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "[1/4] 安装Python包..."
pip3 install -e "$SCRIPT_DIR" -q

echo "[2/4] 安装systemd服务..."
cp "$SCRIPT_DIR/service/umrd.service" /etc/systemd/system/
systemctl daemon-reload

echo "[3/4] 启动UMRD..."
systemctl enable --now umrd

echo "[4/4] 检查状态..."
sleep 1
systemctl status umrd --no-pager || true

echo ""
echo "========================================="
echo "  安装完成!"
echo "========================================="
echo ""
echo "查看状态: cat /run/umrd/status"
echo "查看日志: journalctl -u umrd -f"
