"""A mouse for headless sway: a minimal Wayland client that speaks the
wlr-virtual-pointer protocol over the raw wire format, so the tests can drag
windows by their title bars as a user does. sway's own `seat cursor` commands
move the cursor without the motion events a drag needs."""

import os
import socket
import struct
import time

BUTTON_LEFT = 0x110


class Pointer:
    DISPLAY, REGISTRY = 1, 2

    def __init__(self, env):
        path = os.path.join(env["XDG_RUNTIME_DIR"], env["WAYLAND_DISPLAY"])
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.connect(path)
        self.buffer = b""
        self.next_id = 3
        self.send(self.DISPLAY, 1, struct.pack("<I", self.REGISTRY))
        globals_ = self.roundtrip()
        name = next(name for name, interface in globals_ if interface == "zwlr_virtual_pointer_manager_v1")
        manager = self.bind(name, "zwlr_virtual_pointer_manager_v1", 1)
        self.pointer = self.allocate()
        self.send(manager, 0, struct.pack("<II", 0, self.pointer))
        self.roundtrip()

    def allocate(self):
        self.next_id += 1
        return self.next_id - 1

    def send(self, target, opcode, payload=b""):
        self.socket.sendall(struct.pack("<II", target, (8 + len(payload)) << 16 | opcode) + payload)

    def bind(self, name, interface, version):
        new = self.allocate()
        text = interface.encode() + b"\0"
        text += b"\0" * (-len(text) % 4)
        self.send(self.REGISTRY, 0, struct.pack("<II", name, len(interface) + 1) + text + struct.pack("<II", version, new))
        return new

    def roundtrip(self):
        """Wait until sway has handled every request sent so far; return the globals it announced."""
        callback = self.allocate()
        self.send(self.DISPLAY, 0, struct.pack("<I", callback))
        found = []
        while True:
            while len(self.buffer) < 8:
                self.buffer += self.socket.recv(4096)
            sender, word = struct.unpack("<II", self.buffer[:8])
            size, opcode = word >> 16, word & 0xFFFF
            while len(self.buffer) < size:
                self.buffer += self.socket.recv(4096)
            body, self.buffer = self.buffer[8:size], self.buffer[size:]
            if sender == self.REGISTRY and opcode == 0:
                name, length = struct.unpack("<II", body[:8])
                found.append((name, body[8:8 + length - 1].decode()))
            elif sender == self.DISPLAY and opcode == 0:
                raise RuntimeError(f"wayland error: {body!r}")
            elif sender == callback:
                return found

    def stamp(self):
        return int(time.monotonic() * 1000) & 0xFFFFFFFF

    def to(self, x, y, width, height):
        self.send(self.pointer, 1, struct.pack("<IIIII", self.stamp(), x, y, width, height))
        self.send(self.pointer, 4)

    def button(self, pressed):
        self.send(self.pointer, 2, struct.pack("<III", self.stamp(), BUTTON_LEFT, int(pressed)))
        self.send(self.pointer, 4)

    def drag(self, start, end, size, steps=12):
        """Press the left button at `start`, move to `end` in steps and let go,
        in layout coordinates of a layout `size` wide and high."""
        self.to(*start, *size)
        self.roundtrip()
        self.button(True)
        self.roundtrip()
        for step in range(1, steps + 1):
            x = start[0] + (end[0] - start[0]) * step // steps
            y = start[1] + (end[1] - start[1]) * step // steps
            self.to(x, y, *size)
            self.roundtrip()
            time.sleep(0.01)
        self.button(False)
        self.roundtrip()

    def close(self):
        self.socket.close()
