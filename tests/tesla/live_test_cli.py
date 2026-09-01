#!/usr/bin/env python3
"""
CabinGuard AI — Model 3 Live Test CLI
tests/tesla/live_test_cli.py

Interactive command-line tool for manually testing every remote
operation CabinGuard AI needs to perform on your Model 3.
Use this BEFORE running the full pipeline to confirm your credentials
and vehicle configuration are working end-to-end.

Setup:
    export TESLA_CLIENT_ID=your_client_id
    export TESLA_CLIENT_SECRET=your_client_secret
    export TESLA_MODEL3_VIN=your_vin

Run:
    python tests/tesla/live_test_cli.py

Or in simulated mode (no real API calls):
    SIMULATE=1 python tests/tesla/live_test_cli.py
"""

import os
import sys
import time
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parents[2]))

from src.alerting.tesla_client import TeslaClient

# ─────────────────────────────────────────────────────────────
# Config from environment
# ─────────────────────────────────────────────────────────────

CLIENT_ID = os.environ.get("TESLA_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("TESLA_CLIENT_SECRET", "")
VIN = os.environ.get("TESLA_MODEL3_VIN", "")           # display only
VEHICLE_ID = os.environ.get("TESLA_VEHICLE_ID", "1492931483925432")  # Pegasus numeric ID
SIMULATE = os.environ.get("SIMULATE", "0") == "1"

MENU = """
╔══════════════════════════════════════════════════╗
║        CabinGuard AI — Model 3 Test CLI          ║
╠══════════════════════════════════════════════════╣
║  1. Fetch vehicle state (online/sleeping/temps)  ║
║  2. Wake vehicle                                 ║
║  3. Vent windows  ← Rain cleared, hot day        ║
║  4. Close windows ← Rain incoming!               ║
║  5. Honk horn     ← Pre-action alert             ║
║  6. Full CabinGuard cycle (vent → alert → close) ║
║  7. Run all unit tests (no car required)         ║
║  q. Quit                                         ║
╚══════════════════════════════════════════════════╝
"""

WINDOW_CODES = {0: "CLOSED", 1: "VENTING", 2: "OPEN"}


def banner():
    mode = "🟡 SIMULATED — no real API calls" if SIMULATE else "🔴 LIVE — real commands to your car"
    vin_display = VIN if VIN else "NOT SET — set TESLA_MODEL3_VIN"
    print("\n" + "="*52)
    print("  CabinGuard AI — Model 3 Live Test")
    print(f"  Mode: {mode}")
    print(f"  VIN:  {vin_display}")
    print(f"  Vehicle ID: {VEHICLE_ID}")
    print("="*52 + "\n")


def make_client() -> TeslaClient:
    if not SIMULATE and (not CLIENT_ID or not CLIENT_SECRET or not VEHICLE_ID):
        print("❌  Missing credentials. Set env vars:")
        print("    export TESLA_CLIENT_ID=...")
        print("    export TESLA_CLIENT_SECRET=...")
        print("    export TESLA_MODEL3_VIN=...")
        print("    Or run with SIMULATE=1 for mock mode.")
        sys.exit(1)

    cfg = {
        "client_id": CLIENT_ID or "simulated",
        "client_secret": CLIENT_SECRET or "simulated",
        "simulated": SIMULATE,
    }
    vehicles = [
        {
            "id": VEHICLE_ID or "SIM-VIN-MODEL3",
            "name": "Model 3 (CabinGuard Test)",
            "api": "tesla_fleet",
            "actions": {
                "on_rain_alert": "close_windows",
                "on_heat_alert": "vent_windows",
            },
        }
    ]
    return TeslaClient(cfg, vehicles)


def print_vehicle_state(client: TeslaClient):
    vehicle_id = VEHICLE_ID or "SIM-VIN-MODEL3"
    print(f"\n⏳ Fetching vehicle state for Pegasus ({vehicle_id})...")
    state = client.get_vehicle_state(vehicle_id)
    if state is None:
        print("❌  Could not retrieve vehicle state.")
        return

    if state.get("simulated"):
        print("  [Simulated vehicle state]")

    vehicle_state = state.get("vehicle_state", {})
    climate = state.get("climate_state", {})
    drive = state.get("drive_state", {})

    print(f"\n  Vehicle status:  {state.get('state', 'unknown').upper()}")
    print(f"\n  🪟 Window positions:")
    print(f"     Front Driver:    {WINDOW_CODES.get(vehicle_state.get('fd_window', -1), 'unknown')}")
    print(f"     Front Passenger: {WINDOW_CODES.get(vehicle_state.get('fp_window', -1), 'unknown')}")
    print(f"     Rear Driver:     {WINDOW_CODES.get(vehicle_state.get('rd_window', -1), 'unknown')}")
    print(f"     Rear Passenger:  {WINDOW_CODES.get(vehicle_state.get('rp_window', -1), 'unknown')}")
    print(f"\n  🌡️  Temperatures:")
    inside = climate.get("inside_temp")
    outside = climate.get("outside_temp")
    print(f"     Inside cabin:  {inside}°C ({to_f(inside)}°F)" if inside else "     Inside cabin:  N/A")
    print(f"     Outside:       {outside}°C ({to_f(outside)}°F)" if outside else "     Outside:        N/A")
    if drive:
        lat = drive.get("latitude")
        lon = drive.get("longitude")
        if lat and lon:
            print(f"\n  📍 Location: {lat:.4f}, {lon:.4f}")


def to_f(c) -> str:
    if c is None:
        return "N/A"
    return f"{c * 9/5 + 32:.1f}"


def do_vent(client: TeslaClient):
    vehicle_id = VEHICLE_ID or "SIM-VIN-MODEL3"
    confirm = input("\n  🪟 Vent windows on your Model 3? [y/N] ").strip().lower()
    if confirm != "y":
        print("  Aborted.")
        return
    print("  📤 Sending VENT command...")
    client.vent_windows(vehicle_id)
    if not SIMULATE:
        print("  ⏳ Waiting 6s for windows to move...")
        time.sleep(6)
        print_vehicle_state(client)
    print("  ✅ Vent command dispatched.")


def do_close(client: TeslaClient):
    vehicle_id = VEHICLE_ID or "SIM-VIN-MODEL3"
    confirm = input("\n  🪟 Close windows on your Model 3? [y/N] ").strip().lower()
    if confirm != "y":
        print("  Aborted.")
        return
    print("  📤 Sending CLOSE command...")
    client.close_windows(vehicle_id)
    if not SIMULATE:
        print("  ⏳ Waiting 8s for windows to close...")
        time.sleep(8)
        print_vehicle_state(client)
    print("  ✅ Close command dispatched.")


def do_honk(client: TeslaClient):
    vehicle_id = VEHICLE_ID or "SIM-VIN-MODEL3"
    confirm = input("\n  📣 Honk horn on your Model 3? [y/N] ").strip().lower()
    if confirm != "y":
        print("  Aborted.")
        return
    client.honk_horn(vehicle_id)
    print("  ✅ Honk command dispatched.")


def do_full_cycle(client: TeslaClient):
    vehicle_id = VEHICLE_ID or "SIM-VIN-MODEL3"
    print("\n  🔄 Full CabinGuard AI Cycle:")
    print("     Simulates: hot day → windows vented → rain alert → windows closed")
    confirm = input("  Proceed? [y/N] ").strip().lower()
    if confirm != "y":
        print("  Aborted.")
        return

    print("\n  Step 1/3: Venting windows (simulating hot-day scenario)...")
    client.vent_windows(vehicle_id)
    if not SIMULATE:
        time.sleep(6)
    print("  → Windows vented.")

    if not SIMULATE:
        print("\n  Step 2/3: Pausing 5s (simulating time passing)...")
        time.sleep(5)

    print("\n  Step 3/3: 🌧️ Rain alert triggered — closing windows!")
    client.close_windows(vehicle_id)
    if not SIMULATE:
        time.sleep(8)
        print_vehicle_state(client)

    print("\n  ✅ Full CabinGuard AI cycle complete.")
    print("     Your Model 3 is protected.")


def do_unit_tests():
    print("\n  Running unit tests (mocked — no car needed)...")
    import subprocess
    result = subprocess.run(
        ["pytest", "tests/tesla/test_tesla_client_unit.py", "-v", "--tb=short"],
        cwd=str(Path(__file__).parents[2]),
    )
    if result.returncode == 0:
        print("\n  ✅ All unit tests passed.")
    else:
        print("\n  ❌ Some unit tests failed. See output above.")


def main():
    banner()
    client = make_client()

    while True:
        print(MENU)
        choice = input("  Select option: ").strip().lower()

        if choice == "1":
            print_vehicle_state(client)
        elif choice == "2":
            vehicle_id = VEHICLE_ID or "SIM-VIN-MODEL3"
            print(f"\n  ⏳ Waking Pegasus ({vehicle_id})...")
            if SIMULATE:
                print("  [Simulated wake — vehicle is online]")
            else:
                client._wake_vehicle(vehicle_id, max_attempts=6, sleep_sec=5)
                print("  ✅ Wake sequence complete.")
        elif choice == "3":
            do_vent(client)
        elif choice == "4":
            do_close(client)
        elif choice == "5":
            do_honk(client)
        elif choice == "6":
            do_full_cycle(client)
        elif choice == "7":
            do_unit_tests()
        elif choice in ("q", "quit", "exit"):
            print("\n  Goodbye. Stay dry! ☂️\n")
            break
        else:
            print("  Unknown option.")

        input("\n  Press Enter to continue...")


if __name__ == "__main__":
    main()
