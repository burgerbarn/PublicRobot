// Buddy mode <-> desktop mode: minimize / restore the kiosk window (Firefox launched with --name buddy-screen).
function kiosk() { return workspace.windowList().find(w => w.resourceClass === 'buddy-screen'); }
function toggle() {
    const w = kiosk();
    if (!w) return;
    if (w.minimized || workspace.activeWindow !== w) { w.minimized = false; workspace.activeWindow = w; }
    else w.minimized = true;
}
registerShortcut('BuddyScreenToggle', 'Buddy: toggle face / desktop', 'Meta+Shift+B', toggle);
