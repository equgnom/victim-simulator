"""Quick standalone device lister — no venv-relative imports needed.

Run this first on any new machine (laptop today, Pi later) to find the
exact device name substrings to put in config.yaml.
"""

import sounddevice as sd

if __name__ == "__main__":
    print(sd.query_devices())
    print("\nDefault input:", sd.default.device[0])
    print("Default output:", sd.default.device[1])
