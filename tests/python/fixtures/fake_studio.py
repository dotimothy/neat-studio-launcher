"""A stand-in studio: prints step lines and serves /health on a port.

python fake_studio.py PORT [--stubborn]   (--stubborn ignores SIGTERM)
"""
import http.server
import signal
import sys

port = int(sys.argv[1])
if "--stubborn" in sys.argv:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok fake studio\n")

    def log_message(self, *a):
        pass


print("▸ Loading fake model", flush=True)
print("✔ Ready", flush=True)
http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
