"""Create-only networkd drop-in for the authorized node2 test identity."""
import os
from pathlib import Path
import socket
import subprocess

DROPIN=Path('/etc/systemd/network/10-netplan-eth0.network.d/50-cs-lab-address.conf')
CONFIG='''# Authorized isolated lab node2 only; retain existing DHCP management.
[Address]
Address=172.24.168.125/20

[Route]
Destination=172.24.168.13/32
Scope=link
PreferredSource=172.24.168.125
'''


def main():
    if socket.gethostname()!='cs-s2s6-node2' or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Owned disposable node2 required')
    if os.geteuid()!=0:
        raise RuntimeError('Explicit guest root installation required')
    if Path('/sys/class/net/eth0/address').read_text().strip()!='00:15:5d:62:b1:01':
        raise RuntimeError('Unexpected lab interface identity')
    if not Path('/run/systemd/network/10-netplan-eth0.network').is_file():
        raise RuntimeError('Expected generated networkd configuration required')
    if DROPIN.parent.is_symlink():
        raise RuntimeError('Refusing symlink drop-in directory')
    DROPIN.parent.mkdir(mode=0o755,exist_ok=True)
    with DROPIN.open('x') as output:
        output.write(CONFIG)
    DROPIN.chmod(0o644)
    for command in (['networkctl','reload'],['networkctl','reconfigure','eth0']):
        result=subprocess.run(command,capture_output=True,text=True,timeout=30)
        if result.returncode:
            raise RuntimeError('Networkd operation failed; retain drop-in for diagnosis')
    print('Lab-only address/source route drop-in installed; DHCP unchanged; reboot verification required')


if __name__=='__main__': main()
