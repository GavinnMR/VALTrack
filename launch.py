"""One-command launch for VALTrack.

Starts the self-hosted vlrggapi data source in the background, waits for it to
answer, then runs the Streamlit app in the foreground. Starting the API too
means the in-app refresh works without a second terminal. When the app exits
(Ctrl+C or closing it), the API is shut down again.

Run it with the VALTrack virtual environment's Python, from the repo root:

    .venv/Scripts/python launch.py      (Windows)
    .venv/bin/python launch.py          (macOS or Linux)

Viewing stored data does not need the API; only fetching new data does. So if
the API fails to start, the app is still launched and works on what is stored.
"""
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
VLRGGAPI_DIR = REPO_ROOT / "vlrggapi"
API_HEALTH_URL = "http://127.0.0.1:3001/version"
API_START_TIMEOUT = 30  # seconds to wait for the API to answer


def _venv_python(venv_dir):
    """The Python executable inside a virtual environment, per platform."""
    windows = venv_dir / "Scripts" / "python.exe"
    posix = venv_dir / "bin" / "python"
    return windows if windows.exists() else posix


def _api_is_up():
    try:
        with urllib.request.urlopen(API_HEALTH_URL, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _start_api():
    """Start vlrggapi in the background, or return None if it cannot start.

    Returns the process handle (None when already running or unavailable), and a
    flag for whether the API is reachable so the caller can warn the user.
    """
    if _api_is_up():
        print("vlrggapi is already running.")
        return None, True

    api_python = _venv_python(VLRGGAPI_DIR / ".venv")
    if not VLRGGAPI_DIR.exists() or not api_python.exists():
        print(
            "Could not find the vlrggapi virtual environment. The app will still "
            "open on stored data, but the in-app refresh will not work until "
            "vlrggapi is set up (see the README)."
        )
        return None, False

    print("Starting vlrggapi...")
    proc = subprocess.Popen(
        [str(api_python), "-u", "main.py"], cwd=str(VLRGGAPI_DIR)
    )
    deadline = time.monotonic() + API_START_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            print("vlrggapi exited while starting. The app will still open.")
            return proc, False
        if _api_is_up():
            print("vlrggapi is up at http://127.0.0.1:3001")
            return proc, True
        time.sleep(0.5)
    print("vlrggapi did not answer in time. The app will still open.")
    return proc, False


def _run_app():
    """Run the Streamlit app in the foreground with this same interpreter."""
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(REPO_ROOT / "app.py")],
        cwd=str(REPO_ROOT),
    )


def main():
    api_proc, _ = _start_api()
    try:
        _run_app()
    except KeyboardInterrupt:
        pass
    finally:
        if api_proc is not None and api_proc.poll() is None:
            print("Shutting down vlrggapi...")
            api_proc.terminate()
            try:
                api_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api_proc.kill()


if __name__ == "__main__":
    main()
