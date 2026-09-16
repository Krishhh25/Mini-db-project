"""Interactive client for the MiniDB network server.

    python client.py [--host 127.0.0.1] [--port 9999] [--token SECRET]

Start the server first with:
    python -m minidb.server
"""
import argparse
import socket


def read_response(f):
    lines = []
    while True:
        line = f.readline()
        if not line:
            return None  # connection closed
        decoded = line.decode().rstrip("\n")
        if decoded == "END":
            break
        lines.append(decoded)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Connect to a MiniDB server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--token", default=None,
                         help="Auth token, if the server was started with MINIDB_AUTH_TOKEN set.")
    args = parser.parse_args()

    with socket.create_connection((args.host, args.port)) as sock:
        f = sock.makefile("rwb")
        print(read_response(f))

        if args.token:
            f.write(f"AUTH {args.token}\n".encode())
            f.flush()
            print(read_response(f))

        while True:
            try:
                line = input(f"{args.host}:{args.port}> ")
            except EOFError:
                break
            if not line.strip():
                continue

            f.write((line + "\n").encode())
            f.flush()

            resp = read_response(f)
            if resp is None:
                print("Connection closed.")
                break
            print(resp)

            if line.strip().upper() == "EXIT":
                break


if __name__ == "__main__":
    main()
