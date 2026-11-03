3.1 bật cổng phía host
    dxq h-sales ip link set eth1 up
    dxq h-guest ip link set eth1 up
    dxq h-eng   ip link set eth1 up
    dxq h-mgmt  ip link set eth1 up
3.2 Cấu hình sw1
    dx sw1 sh
    # Tạo bridge biết VLAN (switch ảo):
    ip link add name br0 type bridge vlan_filtering 1
    ip link set br0 up
    # cắm các cổng bào bridge và bật lên:
    for p in eth1 eth2 eth3; do
    ip link set $p master br0
    ip link set $p up
    done
    # xóa VLAN 1 ( mặc định) - vì cổng mới luôn cắm vào vlan 1, xóa đi ddeeer ko có vlan ngàm:
    for p in eth1 eth2 eth3; do bridge vlan del dev $p vid 1; done
    # 2 cổng access:
    bridge vlan add dev eth2 vid 10 pvid untagged
    bridge vlan add dev eth3 vid 30 pvid untagged
    # cổng trunk
    bridge vlan add dev eth1 vid 10
    bridge vlan add dev eth1 vid 30
    # check:
    bridge vlan show
Bước 3.3: cấu hình sw2 ( dx sw2 sh)
    ip link add name br0 type bridge vlan_filtering 1
    ip link set br0 up
    for p in eth1 eth2 eth3; do
    ip link set $p master br0
    ip link set $p up
    done
    for p in eth1 eth2 eth3; do bridge vlan del dev $p vid 1; done
    bridge vlan add dev eth2 vid 20 pvid untagged
    bridge vlan add dev eth3 vid 99 pvid untagged
    bridge vlan add dev eth1 vid 20
    bridge vlan add dev eth1 vid 99
    bridge vlan show
    exit
Bước 3.4, check từ bên ngoài (vscode)
    dxq sw1 bridge vlan show
    dxq sw2 bridge vlan show
    dxq sw1 ip -br link show master br0