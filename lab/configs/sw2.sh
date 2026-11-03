#!/bin/sh
# sw2: eth1 = trunk (VLAN 20, 99) | eth2 = access VLAN 20 | eth3 = access VLAN 99
ip link add name br0 type bridge vlan_filtering 1 2>/dev/null || true
ip link set br0 up
for p in eth1 eth2 eth3; do
  ip link set "$p" master br0
  ip link set "$p" up
  bridge vlan del dev "$p" vid 1 2>/dev/null || true
done
bridge vlan add dev eth2 vid 20 pvid untagged 2>/dev/null || true
bridge vlan add dev eth3 vid 99 pvid untagged 2>/dev/null || true
bridge vlan add dev eth1 vid 20 2>/dev/null || true
bridge vlan add dev eth1 vid 99 2>/dev/null || true
