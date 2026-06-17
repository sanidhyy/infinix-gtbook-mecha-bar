#!/usr/bin/env python3

import argparse
import json
import sys
from enum import IntEnum
from pathlib import Path

import serial

DEFAULT_PORT = "/dev/ttyS4"
DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT_S = 1

PACKET_LEN = 17
HDR_0 = 0x34
HDR_1 = 0x0E

CONFIG_DIR = Path.home() / ".config" / "infinix-gtbook-mecha-bar"
STATE_FILE = CONFIG_DIR / "state.json"

DEFAULT_R, DEFAULT_G, DEFAULT_B = 32, 200, 90
DEFAULT_BRIGHTNESS = 100


class PacketIdx(IntEnum):
    HDR0 = 0
    HDR1 = 1
    MODE = 2
    R1 = 3
    G1 = 5
    B1 = 7
    DELAY = 9
    BRIGHTNESS = 10
    CHECKSUM = 16


BRIGHTNESS_SCALE = 0.44

MODES = {
    "off": 0,
    "always_on": 1,
    "breath": 2,
    "cover": 6,
    "game": 9,
}

MODE_ALIASES = {
    "alwayson": "always_on",
    "always-on": "always_on",
    "always on": "always_on",
    "static": "always_on",
    "close": "off",
}

MODE_LABELS = {
    "off": "Off",
    "always_on": "Always On",
    "breath": "Breath",
    "cover": "Cover",
    "game": "Game",
}

CYCLE_ORDER = ["always_on", "breath", "cover", "game"]
MODE_DISPLAY_ORDER = [*CYCLE_ORDER, "off"]

RGB_MODES = {"always_on", "breath", "cover"}
BRIGHTNESS_MODES = {"always_on", "breath", "cover", "game"}


class SerialPortError(Exception):
    """The Mecha bar serial port could not be opened."""


def default_last_active():
    return {
        "mode": "breath",
        "r": DEFAULT_R,
        "g": DEFAULT_G,
        "b": DEFAULT_B,
        "brightness_pct": DEFAULT_BRIGHTNESS,
        "delay": None,
    }


def default_state():
    return {
        "power_on": False,
        "last_active": default_last_active(),
        "modes": {},
        "cycle_index": 0,
        "suspended_from_on": False,
        "port": DEFAULT_PORT,
    }


def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_state():
    ensure_config_dir()
    if not STATE_FILE.exists():
        return default_state()
    try:
        with STATE_FILE.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default_state()

    base = default_state()
    base.update({k: v for k, v in data.items() if k in base})
    if not isinstance(base.get("modes"), dict):
        base["modes"] = {}
    if not isinstance(base.get("last_active"), dict):
        base["last_active"] = default_last_active()
    if not isinstance(base.get("cycle_index"), int):
        base["cycle_index"] = 0
    if not isinstance(base.get("port"), str) or not base["port"]:
        base["port"] = DEFAULT_PORT
    return base


def save_state(state):
    ensure_config_dir()
    with STATE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")


def get_saved_mode_settings(state, mode):
    entry = state.get("modes", {}).get(mode)
    if not entry:
        if mode == "game":
            return 0, 0, 0, DEFAULT_BRIGHTNESS, None
        return DEFAULT_R, DEFAULT_G, DEFAULT_B, DEFAULT_BRIGHTNESS, None
    delay = entry.get("delay")
    delay = int(delay) if delay is not None else None
    if mode != "breath":
        delay = None
    return (
        int(entry.get("r", DEFAULT_R)),
        int(entry.get("g", DEFAULT_G)),
        int(entry.get("b", DEFAULT_B)),
        int(entry.get("brightness_pct", DEFAULT_BRIGHTNESS)),
        delay,
    )


def save_mode_settings(state, mode, r, g, b, brightness_pct, delay=None):
    if mode != "breath":
        delay = None
    state.setdefault("modes", {})[mode] = {
        "r": r,
        "g": g,
        "b": b,
        "brightness_pct": brightness_pct,
        "delay": delay,
    }


def clamp_int(value: int, lo: int, hi: int, *, label: str) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{label} must be an integer (got {value!r})")
    if value < lo or value > hi:
        raise ValueError(f"{label} must be in range {lo}-{hi} (got {value})")
    return value


def normalize_mode(name):
    if not name:
        return None
    raw = name.strip().lower()
    raw = MODE_ALIASES.get(raw, raw)
    if raw in MODES:
        return raw
    return None


def validate_rgb(r, g, b):
    for label, value in (("R", r), ("G", g), ("B", b)):
        clamp_int(value, 0, 255, label=label)


def validate_brightness(brightness_pct):
    clamp_int(brightness_pct, 0, 100, label="brightness")


def validate_delay_for_mode(mode_name, delay):
    if delay is not None and mode_name != "breath":
        raise ValueError("--delay is only valid for breath mode")


def validate_action_flags(args):
    flags = [args.toggle, args.cycle, args.suspend, args.resume]
    if sum(flags) > 1:
        raise ValueError("Use only one of: -t/--toggle, -c/--cycle, --suspend, --resume")


def resolve_port(args):
    state = load_state()
    return args.port if args.port is not None else state.get("port", DEFAULT_PORT)


def serial_permission_message(port: str) -> str:
    return (
        f"Permission denied opening {port}. "
        f"Run with sudo, or add your user to the serial group "
        f"(often 'dialout' or 'uucp' depending on distro), then log out and back in."
    )


def is_serial_permission_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, serial.SerialException):
        if getattr(exc, "errno", None) == 13:
            return True
        msg = str(exc).lower()
        return "permission denied" in msg or "errno 13" in msg
    return False


def open_serial(*, port: str, baud: int, timeout_s: float) -> serial.Serial:
    try:
        return serial.Serial(port, baud, timeout=timeout_s)
    except (PermissionError, serial.SerialException) as e:
        if is_serial_permission_error(e):
            raise SerialPortError(serial_permission_message(port)) from e
        if isinstance(e, serial.SerialException):
            raise SerialPortError(
                f"Could not open {port}: {e}. "
                f"Verify the port exists (default: {DEFAULT_PORT})."
            ) from e
        raise


def brightness_pct_to_byte(brightness_pct: int) -> int:
    validate_brightness(brightness_pct)
    raw = int(255 * (brightness_pct / 100.0))
    scaled = int(raw * BRIGHTNESS_SCALE)
    return max(0, min(255, scaled))


def build_packet(
    *,
    mode: int,
    r: int,
    g: int,
    b: int,
    brightness_pct: int,
    delay: int | None,
) -> bytearray:
    clamp_int(mode, 0, 255, label="mode")
    validate_rgb(r, g, b)
    brightness_byte = brightness_pct_to_byte(brightness_pct)

    payload = bytearray(PACKET_LEN)
    payload[PacketIdx.HDR0] = HDR_0
    payload[PacketIdx.HDR1] = HDR_1
    payload[PacketIdx.MODE] = mode

    payload[PacketIdx.R1] = r
    payload[PacketIdx.G1] = g
    payload[PacketIdx.B1] = b

    if delay is not None:
        clamp_int(delay, 0, 255, label="delay")
        payload[PacketIdx.DELAY] = delay

    payload[PacketIdx.BRIGHTNESS] = brightness_byte
    payload[PacketIdx.CHECKSUM] = sum(payload[0 : PacketIdx.CHECKSUM]) & 0xFF
    return payload


def send_packet(*, ser: serial.Serial, payload: bytes) -> None:
    try:
        ser.write(payload)
    except serial.SerialException as e:
        raise SerialPortError(f"Serial write error: {e}") from e


def resolve_settings(state, mode, r=None, g=None, b=None, brightness_pct=None, delay=None):
    if (
        r is not None
        and g is not None
        and b is not None
        and brightness_pct is not None
    ):
        validate_rgb(r, g, b)
        validate_brightness(brightness_pct)
        if delay is not None:
            clamp_int(delay, 0, 255, label="delay")
        validate_delay_for_mode(mode, delay)
        return r, g, b, brightness_pct, delay

    saved_r, saved_g, saved_b, saved_brightness, saved_delay = get_saved_mode_settings(
        state, mode
    )
    return (
        saved_r if r is None else r,
        saved_g if g is None else g,
        saved_b if b is None else b,
        saved_brightness if brightness_pct is None else brightness_pct,
        saved_delay if delay is None else delay,
    )


def persist_successful_apply(state, mode, r, g, b, brightness_pct, delay, port):
    state["port"] = port
    if mode != "off":
        save_mode_settings(state, mode, r, g, b, brightness_pct, delay)
        state["last_active"] = {
            "mode": mode,
            "r": r,
            "g": g,
            "b": b,
            "brightness_pct": brightness_pct,
            "delay": delay,
        }
        state["power_on"] = True
    else:
        state["power_on"] = False
    save_state(state)


def set_mecha_bar(
    mode="breath",
    r=DEFAULT_R,
    g=DEFAULT_G,
    b=DEFAULT_B,
    brightness_pct=DEFAULT_BRIGHTNESS,
    delay=None,
    *,
    ser,
    port=DEFAULT_PORT,
    baud=DEFAULT_BAUD,
    timeout_s=DEFAULT_TIMEOUT_S,
    update_config=True,
):
    try:
        clamp_int(baud, 1, 10_000_000, label="baud")
        if timeout_s < 0:
            raise ValueError("timeout must be >= 0")

        mode_name = normalize_mode(mode) if isinstance(mode, str) else None
        if mode_name is None:
            raise ValueError("Unknown mode. See --help for valid names.")

        validate_delay_for_mode(mode_name, delay)

        if mode_name == "game":
            r, g, b = 0, 0, 0
            delay = None
        elif mode_name == "off":
            r, g, b = 0, 0, 0
            brightness_pct = 0
            delay = None
        else:
            validate_rgb(r, g, b)
            validate_brightness(brightness_pct)
            if delay is not None:
                clamp_int(delay, 0, 255, label="delay")

        mode_byte = MODES[mode_name]
        packet = build_packet(
            mode=mode_byte,
            r=r,
            g=g,
            b=b,
            brightness_pct=brightness_pct,
            delay=delay,
        )
        send_packet(ser=ser, payload=packet)

        if update_config:
            state = load_state()
            persist_successful_apply(state, mode_name, r, g, b, brightness_pct, delay, port)

        mode_label = MODE_LABELS.get(mode_name, mode_name)
        delay_str = f" delay:{delay}" if delay is not None else ""
        print(
            f"Mecha bar → {mode_label} | R:{r} G:{g} B:{b} | "
            f"Brightness: {brightness_pct}%{delay_str} | "
            f"Checksum: {packet[PacketIdx.CHECKSUM]:#04x}"
        )
        return True

    except ValueError as e:
        print(f"Invalid input: {e}")
        return False
    except SerialPortError as e:
        print(f"Port error: {e}")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


def cmd_toggle(*, ser, port, baud, timeout_s):
    state = load_state()
    if state.get("power_on", False):
        last = state.get("last_active", default_last_active())
        mode_label = MODE_LABELS.get(last.get("mode", "breath"), last.get("mode", "breath"))
        print(
            f"Toggling off (restoring later: {mode_label}, "
            f"R:{last['r']} G:{last['g']} B:{last['b']}, {last['brightness_pct']}%)"
        )
        return set_mecha_bar(
            mode="off",
            ser=ser,
            port=port,
            baud=baud,
            timeout_s=timeout_s,
            update_config=True,
        )

    last = state.get("last_active", default_last_active())
    mode_name = normalize_mode(last.get("mode", "breath")) or "breath"
    r, g, b, brightness_pct, delay = resolve_settings(
        state,
        mode_name,
        last.get("r"),
        last.get("g"),
        last.get("b"),
        last.get("brightness_pct"),
        last.get("delay"),
    )

    mode_label = MODE_LABELS.get(mode_name, mode_name)
    print(f"Toggling on → {mode_label} | R:{r} G:{g} B:{b} | {brightness_pct}%")
    return set_mecha_bar(
        mode=mode_name,
        r=r,
        g=g,
        b=b,
        brightness_pct=brightness_pct,
        delay=delay,
        ser=ser,
        port=port,
        baud=baud,
        timeout_s=timeout_s,
        update_config=True,
    )


def cmd_cycle(*, ser, port, baud, timeout_s):
    state = load_state()
    current_index = int(state.get("cycle_index", 0))
    next_index = (current_index + 1) % len(CYCLE_ORDER)
    mode_name = CYCLE_ORDER[next_index]

    r, g, b, brightness_pct, delay = get_saved_mode_settings(state, mode_name)

    state["cycle_index"] = next_index
    save_state(state)

    mode_label = MODE_LABELS.get(mode_name, mode_name)
    print(f"Cycling → {mode_label} | R:{r} G:{g} B:{b} | {brightness_pct}%")
    return set_mecha_bar(
        mode=mode_name,
        r=r,
        g=g,
        b=b,
        brightness_pct=brightness_pct,
        delay=delay,
        ser=ser,
        port=port,
        baud=baud,
        timeout_s=timeout_s,
        update_config=True,
    )


def cmd_suspend(*, ser, port, baud, timeout_s):
    state = load_state()
    currently_on = state.get("power_on", False)

    state["suspended_from_on"] = currently_on
    save_state(state)

    if currently_on:
        print("Suspending: Turning Mecha bar off.")
        return set_mecha_bar(
            mode="off",
            ser=ser,
            port=port,
            baud=baud,
            timeout_s=timeout_s,
            update_config=True,
        )

    print("Suspending: Mecha bar was already off.")
    return True


def cmd_resume(*, ser, port, baud, timeout_s):
    state = load_state()
    should_resume = state.pop("suspended_from_on", False)
    save_state(state)

    if should_resume:
        print("Resuming: Restoring previous state.")
        return cmd_toggle(ser=ser, port=port, baud=baud, timeout_s=timeout_s)

    print("Resuming: Mecha bar was manually turned off prior to suspend. Leaving off.")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="infinix-mecha-bar",
        description="Control Infinix GT Book rear Mecha bar via internal UART.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s -t\n"
            "      Toggle Mecha bar off/on (restores last state when turning on).\n"
            "  %(prog)s -c\n"
            "      Cycle effects (uses saved RGB/brightness per mode).\n"
            "  %(prog)s breath\n"
            "      Apply breath with saved settings, or defaults if none saved.\n"
            "  %(prog)s breath 32 200 90 100\n"
            "      Apply breath with custom RGB and brightness (saved for next time).\n"
            "  %(prog)s breath 32 200 90 100 --delay 1\n"
            "      Apply breath with a custom delay byte.\n"
            "  %(prog)s -p /dev/ttyS4 breath\n"
            "      Use a specific serial port (saved to config for future runs).\n"
            "  %(prog)s --suspend\n"
            "      Turn off before sleep/lock; remember if it was on.\n"
            "  %(prog)s --resume\n"
            "      Restore after wake only if it was on before suspend.\n"
            f"\nConfig file: {STATE_FILE}"
        ),
    )
    parser.add_argument(
        "-t",
        "--toggle",
        action="store_true",
        help="Toggle Mecha bar off/on",
    )
    parser.add_argument(
        "-c",
        "--cycle",
        action="store_true",
        help="Cycle through effects (saved RGB/brightness per mode when omitted)",
    )
    parser.add_argument(
        "--suspend",
        action="store_true",
        help="Suspend Mecha bar state (for system idle/lock)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume Mecha bar state (for system wake)",
    )
    parser.add_argument(
        "-p",
        "--port",
        default=None,
        help=f"Serial port device (default: saved port or {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=DEFAULT_BAUD,
        help=f"Baud rate (default: {DEFAULT_BAUD})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"Serial timeout seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    parser.add_argument("mode", nargs="?", help="Mode name (quick apply)")
    parser.add_argument("r", nargs="?", type=int, help="Red (0-255)")
    parser.add_argument("g", nargs="?", type=int, help="Green (0-255)")
    parser.add_argument("b", nargs="?", type=int, help="Blue (0-255)")
    parser.add_argument("brightness_pct", nargs="?", type=int, help="Brightness (0-100)")
    parser.add_argument(
        "-d",
        "--delay",
        type=int,
        default=None,
        metavar="N",
        help="Optional delay byte 0-255 for breath (smaller tends to be faster)",
    )
    return parser


def prompt_int(label: str, lo: int, hi: int, default: int) -> int:
    while True:
        raw = input(f"{label} ({lo}-{hi}) [{default}]: ").strip()
        if not raw:
            return default
        try:
            value = int(raw, 0)
        except ValueError:
            print("  Enter an integer.")
            continue
        if lo <= value <= hi:
            return value
        print(f"  Enter a value from {lo} to {hi}.")


def prompt_mode() -> str:
    names = MODE_DISPLAY_ORDER
    print("\nModes:")
    for i, name in enumerate(names, start=1):
        print(f"  {i}. {MODE_LABELS.get(name, name)}")

    while True:
        raw = input("\nSelect mode (number or name): ").strip()
        if not raw:
            continue
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(names):
                return names[idx]
            print(f"  Pick a number from 1 to {len(names)}.")
            continue
        mode_name = normalize_mode(raw)
        if mode_name:
            return mode_name
        allowed = ", ".join(MODE_LABELS.get(n, n) for n in names)
        print(f"  Unknown mode. Choose from: {allowed}")


def prompt_settings(mode_name):
    state = load_state()
    defaults = get_saved_mode_settings(state, mode_name)
    r, g, b, brightness_pct, saved_delay = defaults

    if mode_name == "game":
        brightness_pct = prompt_int("Brightness (%)", 0, 100, defaults[3])
        return 0, 0, 0, brightness_pct, None

    if mode_name == "off":
        return 0, 0, 0, 0, None

    if mode_name in RGB_MODES:
        print()
        print(f"Color for '{MODE_LABELS.get(mode_name, mode_name)}' (0-255 per channel):")
        r = prompt_int("  Red", 0, 255, defaults[0])
        g = prompt_int("  Green", 0, 255, defaults[1])
        b = prompt_int("  Blue", 0, 255, defaults[2])

    if mode_name in BRIGHTNESS_MODES:
        brightness_pct = prompt_int("Brightness (%)", 0, 100, defaults[3])

    delay = None
    if mode_name == "breath":
        delay_raw = input(
            f"Delay byte (0-255) [{'skip' if saved_delay is None else saved_delay}]: "
        ).strip()
        if delay_raw:
            try:
                delay = clamp_int(int(delay_raw, 0), 0, 255, label="delay")
            except ValueError:
                print("  Invalid delay; skipping.")
                delay = None
        elif saved_delay is not None:
            delay = saved_delay

    return r, g, b, brightness_pct, delay


def run_interactive(args, *, ser):
    print("Infinix GT Book Mecha bar (UART)")
    print(f"Config: {STATE_FILE}")
    print(f"Port: {args.port} @ {args.baud} baud\n")

    mode_name = prompt_mode()
    r, g, b, brightness_pct, delay = prompt_settings(mode_name)
    ok = set_mecha_bar(
        mode=mode_name,
        r=r,
        g=g,
        b=b,
        brightness_pct=brightness_pct,
        delay=delay,
        ser=ser,
        port=args.port,
        baud=args.baud,
        timeout_s=args.timeout,
        update_config=True,
    )
    sys.exit(0 if ok else 1)


def run_quick_apply(args, *, ser):
    mode_name = normalize_mode(args.mode)
    if mode_name is None:
        raise ValueError(f"Unknown mode: {args.mode!r}")

    state = load_state()
    has_rgb = args.r is not None or args.g is not None or args.b is not None
    has_brightness = args.brightness_pct is not None
    has_delay = args.delay is not None

    if mode_name == "game" and has_rgb:
        print("Warning: game mode ignores RGB values.", file=sys.stderr)

    if has_delay:
        validate_delay_for_mode(mode_name, args.delay)

    if not has_rgb and not has_brightness and not has_delay:
        r, g, b, brightness_pct, delay = get_saved_mode_settings(state, mode_name)
    elif has_rgb:
        if args.r is None or args.g is None or args.b is None:
            raise ValueError("Quick apply requires R G B together, or omit all for saved values")
        brightness_pct = (
            args.brightness_pct if args.brightness_pct is not None else DEFAULT_BRIGHTNESS
        )
        r, g, b, brightness_pct, delay = resolve_settings(
            state,
            mode_name,
            args.r,
            args.g,
            args.b,
            brightness_pct,
            args.delay,
        )
    else:
        r, g, b, brightness_pct, delay = get_saved_mode_settings(state, mode_name)
        if has_brightness:
            validate_brightness(args.brightness_pct)
            brightness_pct = args.brightness_pct
        if has_delay:
            delay = clamp_int(args.delay, 0, 255, label="delay")

    return set_mecha_bar(
        mode=mode_name,
        r=r,
        g=g,
        b=b,
        brightness_pct=brightness_pct,
        delay=delay,
        ser=ser,
        port=args.port,
        baud=args.baud,
        timeout_s=args.timeout,
        update_config=True,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        validate_action_flags(args)
        clamp_int(args.baud, 1, 10_000_000, label="baud")
        if args.timeout < 0:
            raise ValueError("timeout must be >= 0")

        port = resolve_port(args)
        args.port = port

        ser = open_serial(port=port, baud=args.baud, timeout_s=args.timeout)
        try:
            serial_kwargs = {
                "ser": ser,
                "port": port,
                "baud": args.baud,
                "timeout_s": args.timeout,
            }

            if args.toggle:
                ok = cmd_toggle(**serial_kwargs)
            elif args.cycle:
                ok = cmd_cycle(**serial_kwargs)
            elif args.suspend:
                ok = cmd_suspend(**serial_kwargs)
            elif args.resume:
                ok = cmd_resume(**serial_kwargs)
            elif args.mode is None:
                run_interactive(args, ser=ser)
                return
            else:
                ok = run_quick_apply(args, ser=ser)

            sys.exit(0 if ok else 1)
        finally:
            ser.close()
    except SerialPortError as e:
        print(f"Port error: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
