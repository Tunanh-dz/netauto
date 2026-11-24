"""Quản lý tài khoản giao diện web (lệnh dòng lệnh).

  python3 -m netauto.users add an --role admin      # hỏi mật khẩu hai lần (không hiện trên màn hình)
  python3 -m netauto.users list
  python3 -m netauto.users passwd an
  python3 -m netauto.users rename an tuananh        # đổi tên đăng nhập (mật khẩu và phiên giữ nguyên)
  python3 -m netauto.users role binh operator
  python3 -m netauto.users disable binh   |   enable binh

Vai trò: viewer (chỉ xem), operator (vận hành), admin (quản trị). Tài khoản đầu tiên phải tạo bằng lệnh này.
"""
import argparse
import getpass
import sys

from netauto import auth, db, maintenance


def _read_password(args, ask, stdin) -> str:
    if args.password_stdin:
        return stdin.readline().rstrip("\n")
    first = ask("Mật khẩu: ")
    if first != ask("Nhập lại mật khẩu: "):
        raise ValueError("Hai lần nhập mật khẩu không khớp.")
    return first


def main(argv=None, *, ask=getpass.getpass, stdin=sys.stdin, out=print) -> int:
    ap = argparse.ArgumentParser(prog="netauto.users", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=db.DB_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="tạo tài khoản")
    p.add_argument("username")
    p.add_argument("--role", required=True)
    p.add_argument("--password-stdin", action="store_true", help="đọc mật khẩu từ đầu vào chuẩn (dùng cho script)")
    sub.add_parser("list", help="liệt kê tài khoản")
    p = sub.add_parser("passwd", help="đổi mật khẩu (kết thúc mọi phiên đang đăng nhập của tài khoản đó)")
    p.add_argument("username")
    p.add_argument("--password-stdin", action="store_true")
    p = sub.add_parser("rename", help="đổi tên đăng nhập")
    p.add_argument("username")
    p.add_argument("new_name")
    p = sub.add_parser("role", help="đổi vai trò")
    p.add_argument("username")
    p.add_argument("role")
    for name in ("disable", "enable"):
        sub.add_parser(name, help=f"{name} tài khoản").add_argument("username")
    args = ap.parse_args(argv)

    conn = auth.connect(args.db)
    try:
        try:
            if args.cmd == "add":
                user = auth.create_user(conn, args.username, _read_password(args, ask, stdin), args.role, maintenance.now())
                out(f"Đã tạo tài khoản {user['username']} ({user['role']}).")
            elif args.cmd == "list":
                for u in auth.list_users(conn):
                    out(f"{u['username']:<16} {u['role']:<9} {'KHÓA' if u['disabled'] else 'hoạt động':<10} tạo {u['created_at']}")
                if auth.count_users(conn) == 0:
                    out("(chưa có tài khoản nào; tạo bằng: python3 -m netauto.users add <tên> --role admin)")
            elif args.cmd == "passwd":
                auth.set_password(conn, args.username, _read_password(args, ask, stdin))
                out(f"Đã đổi mật khẩu của {args.username}.")
            elif args.cmd == "rename":
                auth.rename_user(conn, args.username, args.new_name)
                out(f"Đã đổi tên {args.username} thành {args.new_name.strip().lower()}.")
            elif args.cmd == "role":
                auth.set_role(conn, args.username, args.role)
                out(f"{args.username} giờ có vai trò {args.role}.")
            else:
                auth.set_disabled(conn, args.username, args.cmd == "disable")
                out(f"Đã {'khóa' if args.cmd == 'disable' else 'mở khóa'} {args.username}.")
        except ValueError as exc:
            out(f"Lỗi: {exc}")
            return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
