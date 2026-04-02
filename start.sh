#!/bin/bash
# WhatsApp Transfer Tool v2.4 — Avvio (Desktop / pywebview)
echo ""
echo "  ██╗    ██╗ █████╗     Transfer v2.4"
echo "  ██║    ██║██╔══██╗    Android → iOS"
echo "  ██║ █╗ ██║███████║"
echo "  ██║███╗██║██╔══██║    macOS · Gratuito"
echo "  ╚███╔███╔╝██║  ██║    Merge sicuro + Rollback + crypt14"
echo "   ╚══╝╚══╝ ╚═╝  ╚═╝"
echo ""

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"

if ! command -v python3 &> /dev/null; then
  echo "❌ Python3 non trovato."
  exit 1
fi

if ! command -v adb &> /dev/null; then
  echo "⚠  ADB non trovato. Installalo con:"
  echo "   brew install android-platform-tools"
  echo ""
fi

if [ ! -d "$VENV" ]; then
  echo "📦 Creazione ambiente virtuale (solo la prima volta)..."
  python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"

# pywebview (UI nativa macOS — WKWebView)
python3 -c "import webview" 2>/dev/null || {
  echo "📦 Installazione pywebview..."
  pip install pywebview --quiet
}

# pymobiledevice3
python3 -c "import pymobiledevice3" 2>/dev/null || {
  echo "📦 Installazione pymobiledevice3..."
  pip install pymobiledevice3 --quiet
}

# wa-crypt-tools (supporto crypt14)
python3 -c "import wa_crypt_tools" 2>/dev/null || {
  echo "📦 Installazione wa-crypt-tools..."
  pip install wa-crypt-tools --quiet
}

echo "🚀 Avvio..."
echo ""
cd "$DIR"
python3 app.py
