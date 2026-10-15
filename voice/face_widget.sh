#!/bin/bash
# Buddy's 3D face as a KDE desktop widget (Plasma "Web Browser" plasmoid = Qt WebEngine/Chromium, GPU compositing;
# the Firefox kiosk ran face3d at 3-13 fps on the 4K TV). Quarter screen, bottom-right. 2026-09-30.
# Keys (plasmoid kcfg): useDefaultUrl + defaultUrl decide what it loads ('url' alone is ignored). Needs a plasmashell
# restart to load a changed address. Remove: right-click the widget -> Remove.
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus
qdbus-qt6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript '
const d = desktops()[0], g = screenGeometry(d.screen);
const w = d.addWidget("org.kde.plasma.webbrowser", g.x + g.width / 2, g.y + g.height / 2, g.width / 2, g.height / 2);
w.currentConfigGroup = ["General"];
w.writeConfig("useDefaultUrl", true); w.writeConfig("defaultUrl", "http://127.0.0.1:8290/face3d"); w.writeConfig("url", "http://127.0.0.1:8290/face3d");
print("added widget " + w.id);'
systemctl --user restart plasma-plasmashell
