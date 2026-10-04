import argparse
import logging
import signal
import threading

from .server import App, make_server
from .store import Store
from .supervisor import Manager


def main():
    parser = argparse.ArgumentParser(description="Natter Web management panel")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9080)
    parser.add_argument("--state-dir", default=".state")
    parser.add_argument("--secure-cookie", action="store_true", help="enable when the panel is accessed through HTTPS")
    parser.add_argument("--reset-password", action="store_true", help="set a new administrator password interactively")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = Store(args.state_dir)
    if args.reset_password:
        import getpass
        password = getpass.getpass("New password (12+ characters): ")
        if password != getpass.getpass("Repeat password: "):
            parser.error("passwords do not match")
        store.change_password(password)
        return
    manager = Manager(store)
    server = make_server(App(store, manager, args.secure_cookie), args.host, args.port)
    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        manager.boot()
        logging.info("Panel listening on %s:%s; initial password file: %s", args.host, server.server_port, store.directory / "bootstrap-password.txt")
        server.serve_forever()
    finally:
        server.server_close()
        manager.close()


if __name__ == "__main__":
    main()
