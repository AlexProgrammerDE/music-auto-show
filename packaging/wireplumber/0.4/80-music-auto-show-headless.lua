-- Allow this dedicated headless user to own Bluetooth audio devices even when
-- systemd-logind does not consider its session to be the active seat.
bluez_monitor.properties["with-logind"] = false
