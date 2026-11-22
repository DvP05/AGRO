"""AgriSutra — One-Click Startup Script.
Launches the FastAPI server and optionally opens the browser.
"""

import os
import sys
import webbrowser
import subprocess
import time

# Ensure UTF-8 output on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def main():
    print("=" * 65)
    print("  🌱 AgriSutra: Evidence-First AI Sales Agent")
    print("  Challenge 01: SGB Agro Industries Pvt. Ltd.")
    print("=" * 65)
    
    app_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(app_dir)

    print("\n[1/3] Checking environment & dependencies...")
    try:
        import fastapi
        import uvicorn
        import sklearn
        import joblib
        print("  ✓ Dependencies verified.")
    except ImportError:
        print("  ⚠️ Installing required dependencies from requirements.txt...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

    print("\n[2/3] Initializing local database & catalogue index...")
    import app.main

    print("\n[3/3] Starting AgriSutra Web Application on http://localhost:8000 ...")
    print("  • Customer WhatsApp Cockpit: http://localhost:8000")
    print("  • OpenAPI Interactive Docs:  http://localhost:8000/docs")
    print("\nPress Ctrl+C to stop the server.")
    print("=" * 65 + "\n")

    # Launch browser after a brief delay
    def open_browser():
        time.sleep(1.2)
        webbrowser.open("http://localhost:8000")

    import threading
    threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == "__main__":
    main()
