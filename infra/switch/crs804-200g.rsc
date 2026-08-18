# MikroTik CRS804-4DDQ-HRM — Diony spark-4x-v1
# Force 200G. Autoneg often lands at 50G (fail).
# Apply in RouterOS, then /export to confirm persist.
# Map ports to spark-01..04 CX-7 port 2. Adjust interface names to the live device.

/interface ethernet
set [find default-name=qsfp56-dd-1] auto-negotiation=no speed=200Gbps mtu=9000
set [find default-name=qsfp56-dd-2] auto-negotiation=no speed=200Gbps mtu=9000
set [find default-name=qsfp56-dd-3] auto-negotiation=no speed=200Gbps mtu=9000
set [find default-name=qsfp56-dd-4] auto-negotiation=no speed=200Gbps mtu=9000

# If the CRS804 build uses fec as a separate property, set RS-FEC in Winbox
# (Ethernet → FEC → RS-FEC) and export the resulting line here after first lab.

/system comment
set value="diony spark-4x-v1 fabric 200G autoneg-off mtu9000"

# Save
/export file=diony-spark-4x-v1-crs804
