---
name: Bug report
about: Report a problem with infinix-mecha-bar
title: ''
labels: bug
assignees: ''

---

**Describe the bug**
A clear description of what went wrong.

**To reproduce**
Steps and exact command(s):

```bash
# example
infinix-mecha-bar breath 32 200 90 100
```

1.
2.
3.

**Expected behavior**
What you expected the Mecha bar (or CLI) to do.

**Actual behavior**
What happened instead — include full terminal output if there was an error.

```
paste output here
```

**Environment**

- Laptop model: [e.g. Infinix GT Book]
- OS / distro: [e.g. CachyOS, Arch]
- Install method: [pipx / AUR / manual]
- `infinix-mecha-bar` version: [e.g. 0.1.0]
- Custom Mode enabled in firmware: [yes / no / unsure]

**Diagnostics**

Please run these and paste the output:

```bash
cat /proc/tty/driver/serial
ls -l /dev/ttyS4
groups
infinix-mecha-bar --help
```

If the issue is mode-specific, include the command you ran and whether the bar changed at all.

**Additional context**
Anything else that might help — lid-close hooks, Hyprland config, non-default port (`-p`), etc.
