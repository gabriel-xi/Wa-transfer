"""
app.py — Entry point pywebview (v2.4 — Desktop)

Apre una finestra nativa macOS con WKWebView.
Nessuna porta di rete aperta, nessun browser esterno.
"""

import sys
import signal
from pathlib import Path

import webview

from api import Api


def main():
    api     = Api()
    ui_path = (Path(__file__).parent / "ui.html").resolve()

    window = webview.create_window(
        title            = "WA Transfer v2.4",
        url              = ui_path.as_uri(),
        js_api           = api,
        width            = 1280,
        height           = 820,
        min_size         = (900, 620),
        background_color = "#070B14",
        text_select      = False,
    )
    api.set_window(window)

    def _on_closing():
        pass  # hook futuro: cleanup temp, etc.

    window.events.closing += _on_closing

    signal.signal(signal.SIGINT,  lambda *_: webview.destroy_window())
    signal.signal(signal.SIGTERM, lambda *_: webview.destroy_window())

    print("\n  WA Transfer v2.4 — avvio finestra...")
    webview.start(debug=False)


if __name__ == "__main__":
    main()
