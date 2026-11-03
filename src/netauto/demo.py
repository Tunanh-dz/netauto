from netauto.transport import run

r = run("r1", ["ip", "-br", "link"])
print("Thành công:", r.ok)
print(r.stdout)

r = run("r1", ["vtysh", "-c", "show ip ospf neighbor json"])
print("OSPF neighbor (JSON thô, 300 ký tự đầu):")
print(r.stdout[:300])
