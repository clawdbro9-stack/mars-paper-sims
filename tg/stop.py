import os,signal
from pathlib import Path
r=Path(__file__).resolve().parent
for name,expected in [('supervisor.pid','supervisor.py'),('sim.pid','sim.py')]:
 try:
  pid=int((r/name).read_text());cmd=Path('/proc',str(pid),'cmdline').read_bytes().decode().replace('\x00',' ')
  if str(r/expected) in cmd:os.kill(pid,signal.SIGTERM)
 except (FileNotFoundError,ProcessLookupError):pass
