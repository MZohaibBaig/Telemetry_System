import argparse
import os
import random
import time

import httpx

BASE_URL = os.getenv("SIMULATOR_BASE_URL", "http://127.0.0.1:8000")


def login(email: str, password: str) -> str:
    resp = httpx.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password})
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_or_create_device(token: str, device_name: str, device_type: str) -> int:
    headers = {"Authorization": f"Bearer {token}"}
    existing = httpx.get(f"{BASE_URL}/devices", headers=headers).json()
    for d in existing:
        if d["device_name"] == device_name:
            return d["id"]

    resp = httpx.post(
        f"{BASE_URL}/devices",
        headers=headers,
        json={"device_name": device_name, "device_type": device_type},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def main():
    parser = argparse.ArgumentParser(description="Simulate a device posting random readings.")
    parser.add_argument("--email", default="test@example.com")
    parser.add_argument("--password", default="testpass123")
    parser.add_argument("--device-name", default="Living Room Sensor")
    parser.add_argument("--device-type", default="temperature_sensor")
    parser.add_argument("--unit", default="celsius")
    parser.add_argument("--min-value", type=float, default=18.0)
    parser.add_argument("--max-value", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between readings")
    args = parser.parse_args()

    token = login(args.email, args.password)
    device_id = get_or_create_device(token, args.device_name, args.device_type)
    headers = {"Authorization": f"Bearer {token}"}

    print(f"Simulating device_id={device_id} ({args.device_name}) — Ctrl+C to stop")

    try:
        while True:
            value = round(random.uniform(args.min_value, args.max_value), 2)
            resp = httpx.post(
                f"{BASE_URL}/devices/{device_id}/readings",
                headers=headers,
                json={"value": value, "unit": args.unit},
            )
            resp.raise_for_status()
            print(f"  posted {value} {args.unit}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()