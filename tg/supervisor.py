import fcntl,os,signal,subprocess,time
from pathlib import Path
r=Path(__file__).resolve().parent
lock=(r/'supervisor.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
(r/'supervisor.pid').write_text(str(os.getpid()))
running=True;child=None
def stop(*_):
 global running
 running=False
 if child and child.poll() is None:child.terminate()
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
while running:
 child=subprocess.Popen(['python3',str(r/'sim.py')],cwd=r)
 child.wait()
 if running:time.sleep(5)
