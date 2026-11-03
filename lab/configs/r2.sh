#!/bin/sh
# r2: gateway VLAN 20 và 99, nối r1 qua eth2
ip link set eth1 up
ip link add link eth1 name eth1.20 type vlan id 20 2>/dev/null || true
ip link add link eth1 name eth1.99 type vlan id 99 2>/dev/null || true
ip addr add 192.168.20.1/24 dev eth1.20 2>/dev/null || true
ip addr add 192.168.99.1/24 dev eth1.99 2>/dev/null || true
ip link set eth1.20 up
ip link set eth1.99 up
ip addr add 10.0.12.2/30 dev eth2 2>/dev/null || true
ip link set eth2 up
sysctl -w net.ipv4.ip_forward=1 >/dev/null
