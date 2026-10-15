#!/usr/bin/env python3
"""The work light: Magicshine HORI 900 over BLE (BlueZ D-Bus; system python3-dbus + python3-gi, nothing installed).
Needs the robot's Bluetooth on rtk_btusb (scripts/setup/robot_bt_rtk.sh).

  light_hori.py on [level 1-99]   (default 99 = full)
  light_hori.py query             model, battery, temperature (read-only)
  There is NO off: no confirmed BLE power-off exists for this lamp (2026-10-07). Off = the lamp's button.
Exit 0 = the light acknowledged the output frame (B6), 1 = no acknowledgement.

Protocol (open-source captures: lenne0815/karoo-magicshine-controls, TimFourcode/evo1300; checked on this light
2026-10-07): service FFE1, char FFE0 (write + notify), frames DE <len> <cmd> <payload> <xor(len..payload)> ED.
The official app's order: A1 (info), A6 01 EF (acknowledged B7), A2 output (acknowledged B6), A4 (battery), 180 ms
apart. On the HORI 900 the A2 frame is only accepted with byte 17 = 0xBB (FLAG_SAVE): with 0x00 ("apply only") it
is silently rejected, in both published layouts. A2 = ADD a custom mode to a slot (0xBB add / 0xAA delete, brightness
1..100; m4jki/allty-1500s-control, confirmed on an ALLTY 1500S = M1-B3); the lamp switches to the added mode. A
brightness-0 "off" is acknowledged but the light stays on (owner, 2026-10-07).
"""
import sys, time
import dbus, dbus.mainloop.glib
from gi.repository import GLib

ADDR = '02:19:00:00:4B:1C'                          # advertises "M1-B0 HORI_900"; AD query answers "HORI_900"
DEV = '/org/bluez/hci0/dev_' + ADDR.replace(':', '_')
FFE0 = DEV + '/service000c/char000d'


def frame(cmd, payload):
    ln = len(payload) + 5
    c = ln ^ cmd
    for b in payload:
        c ^= b
    return bytes([0xDE, ln, cmd] + list(payload) + [c, 0xED])


A1, A4, AD = frame(0xA1, [0]), frame(0xA4, [0]), frame(0xAD, [0])
A6 = frame(0xA6, [0x01, 0xEF])


def output(level):
    """Module-1 layout (Karoo capture DE14A20101010163000150000000000000BB3FED = full); level 0 = off."""
    if level <= 0:
        return frame(0xA2, [1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xBB])
    return frame(0xA2, [1, 1, 1, 1, min(level, 0x63), 0, 1, 0x50, 0, 0, 0, 0, 0, 0, 0xBB])


def run(frames, settle_ms=1500):
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    dev = bus.get_object('org.bluez', DEV)
    if not dev.Get('org.bluez.Device1', 'Connected', dbus_interface='org.freedesktop.DBus.Properties'):
        adapter = dbus.Interface(bus.get_object('org.bluez', '/org/bluez/hci0'), 'org.bluez.Adapter1')
        try:
            adapter.StartDiscovery()                # BlueZ only connects to a device it has seen recently
            time.sleep(4)
            adapter.StopDiscovery()
        except dbus.DBusException:
            pass
        dbus.Interface(dev, 'org.bluez.Device1').Connect(timeout=20)
        time.sleep(1.5)
    rx = []
    bus.add_signal_receiver(lambda iface, ch, inv: 'Value' in ch and rx.append(bytes(ch['Value'])),
                            'PropertiesChanged', 'org.freedesktop.DBus.Properties', 'org.bluez', path=FFE0)
    ch = dbus.Interface(bus.get_object('org.bluez', FFE0), 'org.bluez.GattCharacteristic1')
    ch.StartNotify()                                # stays on while this process holds the D-Bus connection
    loop = GLib.MainLoop()

    def send(i=0):
        if i < len(frames):
            ch.WriteValue(dbus.Array(frames[i], signature='y'), {'type': 'request'})
            GLib.timeout_add(180, send, i + 1)
        else:
            GLib.timeout_add(settle_ms, loop.quit)
        return False
    GLib.timeout_add(300, send)
    loop.run()
    return rx


def main():
    a = sys.argv[1:] or ['query']
    if a[0] == 'query':
        rx = run([A1, A4, AD])
        for r in rx:
            if r[2] == 0xBD:
                print('model', r[4:-2].rstrip(b'\0').decode(errors='replace'))
            elif r[2] == 0xB4:
                print('battery byte', r[8], '(coarse: 100/50/30 on the EVO 1700)')
        return 0 if rx else 1
    if a[0] == 'off':
        print('no BLE off for the HORI 900 (brightness 0 is not a valid mode; tested 2026-10-07): use the lamp button')
        return 1
    if a[0] != 'on':
        sys.exit(__doc__)
    level = max(1, int(a[1]) if len(a) > 1 else 0x63)
    rx = run([A1, A6, output(level), A4])
    ok = any(r[2] == 0xB6 for r in rx)
    print(f"light {a[0]}{'' if a[0] == 'off' else ' ' + str(level)}: {'acknowledged' if ok else 'NO acknowledgement'}")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
