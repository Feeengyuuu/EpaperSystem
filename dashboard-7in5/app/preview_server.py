import argparse
import importlib
import io
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qs, urlparse

sys.dont_write_bytecode = True

render_lock = Lock()


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>7.5-inch e-Paper Preview</title>
  <style>
    html, body {
      margin: 0;
      min-height: 100%;
      background: #d6d6d6;
      color: #111;
      font-family: Arial, sans-serif;
    }
    body {
      display: grid;
      place-items: center;
      padding: 24px;
      box-sizing: border-box;
    }
    .screen {
      width: 800px;
      height: 480px;
      background: #fff;
      border: 1px solid #111;
      box-shadow: 0 12px 30px rgba(0, 0, 0, 0.18);
      overflow: hidden;
    }
    img {
      display: block;
      width: 800px;
      height: 480px;
      max-width: none;
      max-height: none;
    }
  </style>
</head>
<body>
  <div class="screen">
    <img id="preview" src="/preview.png?mode=layout&t=0" width="800" height="480" alt="e-Paper preview">
  </div>
  <script>
    const image = document.getElementById("preview");
    const params = new URLSearchParams(window.location.search);
    const mode = params.get("mode") || "layout";
    const interval = Number(params.get("interval") || "1000");

    function refresh() {
      image.src = `/preview.png?mode=${encodeURIComponent(mode)}&t=${Date.now()}`;
    }

    setInterval(refresh, Math.max(250, interval));
  </script>
</body>
</html>
"""


def render_png(mode: str) -> bytes:
    with render_lock:
        import main as dashboard
        import preview_7in5
        import render_7in5_layout
        import epaper_7in5_adapter

        dashboard = importlib.reload(dashboard)
        render_7in5_layout = importlib.reload(render_7in5_layout)
        epaper_7in5_adapter = importlib.reload(epaper_7in5_adapter)
        preview_7in5 = importlib.reload(preview_7in5)

        preview_7in5.seed_preview_data()

        if mode == "layout":
            image = render_7in5_layout.render_screen_7in5(
                dashboard,
                render_7in5_layout.load_7in5_fonts(dashboard.FONT_DIR),
            )
        else:
            source = dashboard.render_screen(
                epaper_7in5_adapter.CanvasEPD(),
                epaper_7in5_adapter.load_fonts(dashboard.FONT_DIR),
            )
            image = epaper_7in5_adapter.adapt_for_7in5(source, mode)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


class PreviewHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            payload = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if parsed.path == "/preview.png":
            params = parse_qs(parsed.query)
            mode = params.get("mode", ["layout"])[0]
            if mode not in {"layout", "squash", "fit", "crop-left", "crop-center", "crop-right"}:
                mode = "layout"
            try:
                payload = render_png(mode)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:
                payload = f"render failed: {exc}".encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def main():
    parser = argparse.ArgumentParser(description="Serve a live 1:1 800x480 e-paper preview.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.realpath(__file__)))
    server = ThreadingHTTPServer((args.host, args.port), PreviewHandler)
    print(f"Serving 1:1 preview at http://{args.host}:{args.port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
