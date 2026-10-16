"""Local supervised backend, shared by scheduled and desktop launchers."""
from app.supervision import run_local_server

if __name__ == '__main__':
    run_local_server()
