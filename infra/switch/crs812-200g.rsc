# MikroTik CRS812 — Diony spark-4x-v1 (forum-proven 4-node Spark class)
# Two 400G QSFP-DD ports broken out to 2x200G each → Spark Cluster (4).
# Community: disable autoneg, 200G-baseCR4 / speed=200Gbps, MTU 9000, RS-FEC.
# Interface names vary by RouterOS; verify with /interface ethernet print
# before applying. This snippet is the intended policy, not a blind paste
# onto an uninspected switch.

# Enable 2x200G breakout on the two QSFP-DD ports used for Spark
/interface ethernet set qsfp56-dd-1 breakout-mode=2x200G
/interface ethernet set qsfp56-dd-2 breakout-mode=2x200G

# Force 200G on breakout legs. Some builds expose -1 and -5 as the 200G
# mains; others expose -1..-4. Print first, then set the live 200G legs:
/interface ethernet
set qsfp56-dd-1-1 auto-negotiation=disabled speed=200Gbps mtu=9000 fec=rs-fec
set qsfp56-dd-1-5 auto-negotiation=disabled speed=200Gbps mtu=9000 fec=rs-fec
set qsfp56-dd-2-1 auto-negotiation=disabled speed=200Gbps mtu=9000 fec=rs-fec
set qsfp56-dd-2-5 auto-negotiation=disabled speed=200Gbps mtu=9000 fec=rs-fec

# Cable map (breakout):
#   qsfp56-dd-1 → spark-01 + spark-02  (CX-7 port 2)
#   qsfp56-dd-2 → spark-03 + spark-04  (CX-7 port 2)

/system comment
set value="diony spark-4x-v1 CRS812 2x200G breakout autoneg-off mtu9000"

/export file=diony-spark-4x-v1-crs812
