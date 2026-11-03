#!/bin/sh
# sw1: eth1 = trunk (VLAN 10, 30) | eth2 = access VLAN 10 | eth3 = access VLAN 30
ip link add name br0 type bridge vlan_filtering 1 2>/dev/null || true
ip link set br0 up
for p in eth1 eth2 eth3; do
  ip link set "$p" master br0
  ip link set "$p" up
  bridge vlan del dev "$p" vid 1 2>/dev/null || true
done
bridge vlan add dev eth2 vid 10 pvid untagged 2>/dev/null || true
bridge vlan add dev eth3 vid 30 pvid untagged 2>/dev/null || true
bridge vlan add dev eth1 vid 10 2>/dev/null || true
bridge vlan add dev eth1 vid 30 2>/dev/null || true
