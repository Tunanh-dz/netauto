#!/bin/sh
# r1: gateway VLAN 10 và 30, nối r2 qua eth2
ip link set eth1 up
ip link add link eth1 name eth1.10 type vlan id 10 2>/dev/null || true
ip link add link eth1 name eth1.30 type vlan id 30 2>/dev/null || true
ip addr add 192.168.10.1/24 dev eth1.10 2>/dev/null || true
ip addr add 192.168.30.1/24 dev eth1.30 2>/dev/null || true
ip link set eth1.10 up
ip link set eth1.30 up
ip addr add 10.0.12.1/30 dev eth2 2>/dev/null || true
ip link set eth2 up
sysctl -w net.ipv4.ip_forward=1 >/dev/null
