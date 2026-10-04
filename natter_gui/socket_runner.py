"""Run the pinned upstream core with a bounded GUI-configured TCP thread limit."""
import os
from pathlib import Path
import runpy


def load_core(core_path=None, thread_limit=None):
    # An allowlist keeps a malformed service config from creating unbounded threads.
    limit = int(thread_limit if thread_limit is not None else os.environ["NATTER_GUI_TCP_THREADS"])
    if limit not in (256, 512, 1024):
        raise ValueError("Unsupported Natter TCP thread limit")
    path = core_path or Path(__file__).resolve().parent.parent / "vendor" / "natter" / "natter.py"
    upstream = runpy.run_path(str(path), run_name="natter_gui_socket_core")
    original_init = upstream["ForwardSocket"].__init__

    def configured_init(self):
        original_init(self)
        self.max_threads = limit

    upstream["ForwardSocket"].__init__ = configured_init
    return upstream


if __name__ == "__main__":
    load_core()["main"]()
