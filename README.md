# :rainbow: Infinix GT Book Mecha Bar Linux Driver

Unofficial Linux CLI for the **rear Mecha bar** RGB controller on the Infinix GT Book.

Sends lighting commands over the onboard UART (`/dev/ttyS4` @ 115200 baud). Infinix Control Center on Windows uses the same protocol; this tool brings that control to Linux.

## :sparkles: What it does

`infinix-mecha-bar` talks to the Mecha bar over an internal serial port — not USB HID like the keyboard.

- :art: Set **lighting modes** (off, always on, breath, cover, game)
- :bulb: Adjust **RGB and brightness** per mode (game mode uses firmware presets only)
- :repeat: **Toggle** the bar off/on while remembering the last state (`-t`)
- :arrows_clockwise: **Cycle** through modes with per-mode saved settings (`-c`)
- :zzz: **Suspend / resume** around sleep, lock, or lid close (`--suspend` / `--resume`)
- :clipboard: Run an **interactive menu** when called with no arguments

State is saved to `~/.config/infinix-gtbook-mecha-bar/state.json`.

## :computer: Supported hardware

| Property  | Value                            |
| --------- | -------------------------------- |
| Interface | Onboard UART (`ttyS*`)           |
| Port      | `/dev/ttyS4` (tested on GT Book) |
| Baud rate | `115200`                         |
| Tested on | Infinix GT Book                  |

### Finding the serial port

On tested GT Book units, the Mecha bar is the only active onboard UART:

```bash
sudo cat /proc/tty/driver/serial | grep '16550A'
```

Look for the line with `16550A`, an `mmio:` address, and non-zero `tx`/`rx` counters — usually index `4` → `/dev/ttyS4`.
After running the tool once, the `tx` counter on that line should increase.

## :package: Installation

> :warning: **Disclaimer:** This is an unofficial, reverse-engineered tool. It was developed and tested on an Infinix GT Book. Other laptops may use a different UART port or may not have this bar at all. Use at your own risk.

Requires **Python 3.10+** and **pipx**.

```bash
git clone https://github.com/sanidhyy/infinix-gtbook-mecha-bar.git
cd infinix-gtbook-mecha-bar
pipx install . --system-site-packages
```

Then run `infinix-mecha-bar --help` to verify.

### Arch / AUR

Install from the AUR:

```bash
yay -S infinix-mecha-bar
# or: paru -S infinix-mecha-bar
```

## :closed_lock_with_key: Serial port permissions

> **AUR install:** the udev rule is installed and reloaded automatically.

By default, serial devices are owned by `root`. Add your user to the `uucp` group, so scripts and window-manager hooks can run without `sudo`.

1. Add your user to the `uucp` group:

```bash
sudo usermod -aG uucp $USER
```

2. Create `/etc/udev/rules.d/99-infinix-mecha-bar.rules`:

```udev
# Infinix GT Book Mecha bar (onboard UART)
KERNEL=="ttyS4", MODE="0660", GROUP="uucp"
```

3. Reload rules:

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## :rocket: Usage

### Interactive

```bash
infinix-mecha-bar
```

### Quick apply

```bash
infinix-mecha-bar breath                        # saved settings, or defaults
infinix-mecha-bar breath 32 200 90 100          # set and save custom values
infinix-mecha-bar breath 32 200 90 100 --delay 1
infinix-mecha-bar always_on 255 0 0 100         # solid red
infinix-mecha-bar -p /dev/ttyS4 breath          # override port (saved to config)
```

Omit R G B together to use saved values. Provide R G B (brightness optional, default 100) to set new ones. `--delay` applies to **breath** only.

### Toggle and cycle

```bash
infinix-mecha-bar -t          # off ↔ restore last state
infinix-mecha-bar -c          # cycle: always_on → breath → cover → game
```

### Suspend / resume

```bash
infinix-mecha-bar --suspend
infinix-mecha-bar --resume
```

### Aliases

`always_on` also accepts: `static`, `alwayson`, `always-on`, `always on`

`off` also accepts: `close`

```bash
infinix-mecha-bar --help
```

## :rainbow: Modes

| Mode        | Command byte | RGB | Brightness | Notes                         |
| ----------- | ------------ | --- | ---------- | ----------------------------- |
| `off`       | `0`          | —   | —          | Turn bar off                  |
| `always_on` | `1`          | ✓   | ✓          | Solid color                   |
| `breath`    | `2`          | ✓   | ✓          | Pulsing color; optional delay |
| `cover`     | `6`          | ✓   | ✓          | Cover-style effect            |
| `game`      | `9`          | —   | ✓          | Firmware game/profile preset  |

`game` ignores custom RGB — only brightness is sent.

## :keyboard: Keybind examples

**Hyprland:**

```conf
bind = SUPER, F4, exec, infinix-mecha-bar -t
bind = SUPER CTRL, F4, exec, infinix-mecha-bar -c
```

**Hypridle / swayidle:**

```conf
listener {
    timeout = 300
    on-timeout = infinix-mecha-bar --suspend
    on-resume  = infinix-mecha-bar --resume
}
```

```bash
swayidle -w timeout 300 'infinix-mecha-bar --suspend' resume 'infinix-mecha-bar --resume'
```

## :wrench: Troubleshooting

| Symptom                           | Fix                                                  |
| --------------------------------- | ---------------------------------------------------- |
| Permission denied on `/dev/ttyS4` | Add `uucp`/`dialout` group or udev rule (see above)  |
| No effect on the bar              | Enable **Custom Mode** in firmware lighting settings |
| Wrong port                        | Check `/proc/tty/driver/serial`; use `-p /dev/ttySX` |
| `game` ignores your colors        | Expected — only brightness applies                   |
| `--delay` ignored                 | Expected — Delay is valid for `breath` only          |

## :gear: How it works

The Mecha bar controller is wired to an onboard UART. The tool opens the serial port and writes a **17-byte** packet:

```
[0]=0x34  [1]=0x0E  [2]=mode  [3]=R  [5]=G  [7]=B  [9]=delay  [10]=brightness  [16]=checksum
```

Brightness is `int(255 × percent/100 × 0.44)`, clamped to 0–255. Checksum is `sum(bytes[0:16]) & 0xFF`.

Byte 2 is the mode command — see [Modes](#rainbow-modes "🌈 Modes") for values. Color bytes are spaced at odd indices (3, 5, 7) in the frame.

### :bulb: Quick example

Command:

```bash
infinix-mecha-bar always_on 255 0 0 100
```

This sets the bar to **solid red** at **100% brightness**. The tool builds and sends:

| Byte   | Value  | Meaning                |
| ------ | ------ | ---------------------- |
| `[0]`  | `0x34` | Header 1               |
| `[1]`  | `0x0E` | Header 2               |
| `[2]`  | `0x01` | Always on              |
| `[3]`  | `0xFF` | Red = 255              |
| `[5]`  | `0x00` | Green = 0              |
| `[7]`  | `0x00` | Blue = 0               |
| `[10]` | `0x70` | Brightness 100% → 112  |
| `[16]` | `0xB2` | Checksum of bytes 0–15 |

```
34 0E 01  FF 00 00 00 00 00 00  70  ...  B2
│  │  │   │      │    │          │         └── checksum
│  │  │   └──────┴────┘          └── brightness
│  │  └── mode (always on)
│  └── header
└── header
```

After a successful write, settings are saved to `~/.config/infinix-gtbook-mecha-bar/state.json` for toggle, cycle, and quick apply.

## :handshake: Contributing

Pull requests and bug reports are welcome. If you encounter an issue, please include:

```bash
cat /proc/tty/driver/serial
ls -l /dev/ttyS4
infinix-mecha-bar always_on 255 0 0 100
```

## :page_with_curl: License

[MIT LICENSE](LICENSE "View MIT License").
